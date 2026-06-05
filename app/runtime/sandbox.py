"""
Runtime Sandbox
Spawns generated applications in isolated subprocesses, runs health checks
and smoke tests, then cleans up. Proves the generated code actually works.

Uses asyncio subprocess management for non-blocking execution with timeouts.
"""
import asyncio
import os
import sys
import json
import tempfile
import shutil
import socket
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class SmokeTestResult:
    """Result of a single smoke test against a running endpoint."""
    endpoint: str
    method: str
    expected_status: int
    actual_status: Optional[int] = None
    passed: bool = False
    error: Optional[str] = None
    latency_ms: float = 0


@dataclass
class RuntimeResult:
    """Complete result of a sandbox execution."""
    success: bool = False
    port: int = 0
    base_url: str = ""
    startup_latency_seconds: float = 0
    smoke_tests: List[SmokeTestResult] = field(default_factory=list)
    smoke_tests_passed: int = 0
    smoke_tests_failed: int = 0
    process_stdout: str = ""
    process_stderr: str = ""
    errors: List[str] = field(default_factory=list)
    temp_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "port": self.port,
            "base_url": self.base_url,
            "startup_latency_seconds": self.startup_latency_seconds,
            "smoke_tests_passed": self.smoke_tests_passed,
            "smoke_tests_failed": self.smoke_tests_failed,
            "smoke_tests": [
                {
                    "endpoint": t.endpoint,
                    "method": t.method,
                    "expected_status": t.expected_status,
                    "actual_status": t.actual_status,
                    "passed": t.passed,
                    "error": t.error,
                    "latency_ms": t.latency_ms,
                }
                for t in self.smoke_tests
            ],
            "process_stderr": self.process_stderr[-2000:],  # Last 2KB only
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Port utilities
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Find an available TCP port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Core sandbox runner
# ---------------------------------------------------------------------------

async def run_generated_app(
    config: dict,
    files: Dict[str, str],
    timeout: int = 60,
    run_smoke: bool = True,
    keep_alive: int = 300,
) -> RuntimeResult:
    """
    Spin up a generated application in an isolated sandbox, run health checks
    and smoke tests. If successful, keeps the app alive for keep_alive seconds
    so the user can interact with it.

    Args:
        config: The validated app config (metadata, db_schema, api_schema, etc.)
        files: Dict of filename -> content from codegen.generate()
        timeout: Max seconds to wait for the app to start
        run_smoke: Whether to run smoke tests against endpoints
        keep_alive: Seconds to keep the app alive after tests pass (0 = kill immediately)

    Returns:
        RuntimeResult with pass/fail status, smoke test outcomes, and diagnostics.
    """
    result = RuntimeResult()
    result.port = find_free_port()
    result.base_url = f"http://127.0.0.1:{result.port}"

    # Create isolated temp directory
    result.temp_dir = tempfile.mkdtemp(prefix="appgen_")
    try:
        # Write all files
        _write_files(result.temp_dir, files)

        # Install dependencies
        install_ok = await _install_dependencies(result)
        if not install_ok:
            return result

        # Initialize database (run schema.sql)
        _init_database(result)

        # Start the app subprocess
        process = await _start_app(result)
        if process is None:
            return result

        try:
            # Health check: poll until 200 or timeout
            t0 = time.time()
            healthy = await _health_check(result.base_url, timeout=timeout)
            result.startup_latency_seconds = round(time.time() - t0, 2)

            if not healthy:
                # Kill and drain stderr for diagnostics
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.to_thread(process.communicate), timeout=5
                    )
                    if stderr:
                        result.process_stderr = stderr.decode("utf-8", errors="replace")[-4000:]
                    if stdout:
                        result.process_stdout = stdout.decode("utf-8", errors="replace")[-2000:]
                except Exception:
                    pass
                stderr_tail = result.process_stderr[-500:] if result.process_stderr else "(not captured)"
                result.errors.append(
                    f"App did not become healthy within {timeout}s. stderr tail: {stderr_tail}"
                )
                return result

            # Run smoke tests against each endpoint
            if run_smoke:
                await _run_smoke_tests(result, config)

            result.success = (
                len(result.errors) == 0
                and result.smoke_tests_failed == 0
            )

            if result.success and keep_alive > 0:
                result._process = process
                result._keep_alive = keep_alive
                result._temp_dir = result.temp_dir
                process = None
                asyncio.create_task(_delayed_cleanup(result))
                return result

        finally:
            # Kill the subprocess (unless we transferred ownership to result)
            if process is not None:
                await _stop_process(process)

    except Exception as e:
        result.errors.append(f"Sandbox error: {str(e)}")

    finally:
        # Clean up temp directory (unless kept alive)
        if not (result.success and hasattr(result, '_temp_dir')):
            try:
                shutil.rmtree(result.temp_dir, ignore_errors=True)
            except Exception:
                pass

    return result


async def _delayed_cleanup(result: RuntimeResult):
    """Background task: keep app alive for keep_alive seconds, then kill and clean up."""
    keep_alive = getattr(result, '_keep_alive', 0)
    await asyncio.sleep(keep_alive)
    process = getattr(result, '_process', None)
    if process is not None:
        await _stop_process(process)
    temp_dir = getattr(result, '_temp_dir', '')
    if temp_dir:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_files(temp_dir: str, files: Dict[str, str]):
    """Write all generated files into the temp directory."""
    for filename, content in files.items():
        filepath = os.path.join(temp_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)


async def _install_dependencies(result: RuntimeResult) -> bool:
    """pip install requirements.txt into the temp directory."""
    import subprocess as _sp
    req_path = os.path.join(result.temp_dir, "requirements.txt")
    if not os.path.exists(req_path):
        result.errors.append("No requirements.txt found in generated files")
        return False

    try:
        lib_dir = os.path.join(result.temp_dir, "lib")
        os.makedirs(lib_dir, exist_ok=True)

        # Use to_thread + synchronous subprocess (avoids Windows event loop issues)
        def _run_pip():
            return _sp.run(
                [sys.executable, "-m", "pip", "install", "--target", lib_dir,
                 "-r", req_path, "--no-cache-dir", "--no-compile"],
                capture_output=True, timeout=120,
            )

        proc = await asyncio.to_thread(_run_pip)
        if proc.returncode != 0:
            stderr_text = proc.stderr.decode("utf-8", errors="replace")[:500]
            result.errors.append(
                f"pip install failed (exit {proc.returncode}): {stderr_text}"
            )
            return False
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        return True
    except Exception as e:
        import traceback
        result.errors.append(f"pip install error: {type(e).__name__}: {str(e) or '(no message)'}")
        result.errors.append(f"Traceback: {traceback.format_exc()[-500:]}")
        return False


def _init_database(result: RuntimeResult):
    """Run schema.sql against SQLite in the temp directory."""
    sql_path = os.path.join(result.temp_dir, "schema.sql")
    if not os.path.exists(sql_path):
        return  # No SQL to run

    db_path = os.path.join(result.temp_dir, "app.db")

    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        with open(sql_path, "r", encoding="utf-8") as f:
            sql_content = f.read()
        for stmt in sql_content.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                conn.execute(stmt)
        conn.commit()
        conn.close()
    except Exception as e:
        result.errors.append(f"Database init error: {str(e)}")


async def _start_app(result: RuntimeResult):
    """Spawn uvicorn subprocess on the allocated port."""
    app_py = os.path.join(result.temp_dir, "app.py")
    if not os.path.exists(app_py):
        result.errors.append("No app.py found in generated files")
        return None

    env = os.environ.copy()
    # Normalize path for SQLite URL (forward slashes) — backslashes break on Windows
    db_path = os.path.join(result.temp_dir, "app.db").replace("\\", "/")
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PORT"] = str(result.port)  # Tell the generated app which port to use
    # Include both the app dir and the lib/ subdirectory (pip --target) in PYTHONPATH
    lib_dir = os.path.join(result.temp_dir, "lib")
    paths = [result.temp_dir, lib_dir]
    existing_pp = env.get("PYTHONPATH", "")
    if existing_pp:
        paths.append(existing_pp)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONUNBUFFERED"] = "1"

    try:
        import subprocess as _sp
        process = _sp.Popen(
            [sys.executable, "-u", app_py],
            cwd=result.temp_dir,
            env=env,
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
        )
        return process
    except Exception as e:
        result.errors.append(f"Failed to start app: {str(e)}")
        return None


async def _health_check(base_url: str, timeout: int = 30) -> bool:
    """Poll GET /docs until 200 response or timeout. Uses stdlib urllib (no dependencies)."""
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            def _check():
                req = urllib.request.Request(f"{base_url}/docs", method="GET")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    return resp.status
            status = await asyncio.to_thread(_check)
            if status == 200:
                return True
        except Exception:
            await asyncio.sleep(0.5)

    return False


async def _run_smoke_tests(result: RuntimeResult, config: dict):
    """Test each API endpoint from the config. Uses stdlib urllib (no dependencies)."""
    import urllib.request
    import urllib.error
    import json as _json

    endpoints = config.get("api_schema", {}).get("endpoints", [])
    if not endpoints:
        return

    # Skip auth endpoints (not generated by codegen) and limit to 10 tests
    _skip_prefixes = ("/auth/", "/login", "/logout", "/register", "/signup", "/signin")
    _filtered = [e for e in endpoints if not any(e.get("path", "").startswith(p) for p in _skip_prefixes)]
    for ep in _filtered[:10]:
        test = SmokeTestResult(
            endpoint=ep.get("path", "/"),
            method=ep.get("method", "GET"),
            expected_status=_expected_status(ep),
        )

        t0 = time.time()
        try:
            url = f"{result.base_url}{ep['path']}"
            # Replace path params with 1 for testing
            url = url.replace("{id}", "1").replace("{pk}", "1")
            for placeholder in ["{contact_id}", "{task_id}", "{user_id}",
                                "{post_id}", "{product_id}", "{order_id}",
                                "{event_id}", "{course_id}", "{project_id}"]:
                url = url.replace(placeholder, "1")

            method = ep.get("method", "GET").upper()
            body = None
            if method in ("POST", "PUT", "PATCH"):
                body = _json.dumps({"_test": True}).encode("utf-8")

            def _make_request():
                req = urllib.request.Request(url, data=body, method=method)
                req.add_header("Content-Type", "application/json")
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return resp.status
                except urllib.error.HTTPError as e:
                    return e.code  # 4xx is still a valid response

            actual_status = await asyncio.to_thread(_make_request)
            test.actual_status = actual_status
            test.passed = _is_smoke_test_pass(test.expected_status, actual_status)
        except Exception as e:
            test.error = str(e)[:200]
            test.passed = False

        test.latency_ms = round((time.time() - t0) * 1000, 1)
        result.smoke_tests.append(test)

        if test.passed:
            result.smoke_tests_passed += 1
        else:
            result.smoke_tests_failed += 1

        if result.smoke_tests_failed >= 3:
            # Too many failures, stop testing
            result.errors.append(
                f"Stopped smoke tests after {result.smoke_tests_failed} failures"
            )
            break


def _expected_status(ep: dict) -> int:
    """Determine expected HTTP status for a smoke test hit.
    Smoke tests use mock data ({"_test": True}), so we accept any
    response in the 2xx-4xx range as proof the app is running.
    Only 5xx errors or connection failures count as failures."""
    return 0  # 0 means "any status in 200-499 is acceptable"


def _is_smoke_test_pass(expected: int, actual: int) -> bool:
    """A smoke test passes if the app responds (any 2xx/4xx status).
    Only connection errors and 5xx server errors count as failures."""
    if actual is None:
        return False
    return 200 <= actual < 500


async def _stop_process(process):
    """Gracefully stop the subprocess with timeout escalation."""
    if process is None:
        return
    try:
        process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=3)
    except Exception:
        pass
