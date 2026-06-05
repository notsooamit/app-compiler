"""
FastAPI server for the App Compiler.
Endpoints:
- GET / -- serves the main UI
- POST /generate -- runs the full pipeline
- POST /generate-stream -- SSE streaming with real-time progress
- POST /download-code -- downloads generated code as zip (accepts config)
- POST /evaluate -- runs benchmarks
- GET /api/cost -- token usage and cost estimate
"""
import asyncio
import json
import io
import time
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.llm import get_token_usage, reset_token_usage, estimate_cost
from app.generation.codegen import generate as generate_code
from app.generation.validator import validate_generated_code

app = FastAPI(title="App Compiler", description="AI-powered software generation compiler")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Rate Limiter (sliding window, per IP, with periodic cleanup)
# ============================================================
from app.config import RATE_LIMIT, RATE_WINDOW, MAX_PROMPT_LENGTH, CLEANUP_INTERVAL

rate_limit_buckets: dict = {}
rate_limit_lock = asyncio.Lock()
_last_cleanup = time.time()


async def _cleanup_rate_limits():
    """Periodically remove entries for IPs that haven't been seen in RATE_WINDOW * 2."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < CLEANUP_INTERVAL:
        return
    async with rate_limit_lock:
        cutoff = now - RATE_WINDOW * 2
        stale = [ip for ip, times in rate_limit_buckets.items()
                 if not times or max(times) < cutoff]
        for ip in stale:
            del rate_limit_buckets[ip]
        _last_cleanup = now


async def check_rate_limit(client_ip: str) -> bool:
    """Sliding window rate limiter with periodic cleanup. Returns True if allowed."""
    now = time.time()
    await _cleanup_rate_limits()
    async with rate_limit_lock:
        bucket = rate_limit_buckets.get(client_ip, [])
        # Remove expired entries for this IP
        bucket = [t for t in bucket if now - t < RATE_WINDOW]
        if len(bucket) >= RATE_LIMIT:
            rate_limit_buckets[client_ip] = bucket  # Save pruned list
            return False
        bucket.append(now)
        rate_limit_buckets[client_ip] = bucket
    return True


# ============================================================
# Request Models
# ============================================================
class GenerateRequest(BaseModel):
    prompt: str = Field(..., max_length=MAX_PROMPT_LENGTH)
    api_key: str = Field(..., min_length=1, description="Your DeepSeek API key (required)")


class ModifyRequest(BaseModel):
    user_prompt: str
    api_key: str = Field(..., min_length=1, description="Your DeepSeek API key (required)")
    stage: int  # 1-4, restart from this stage
    intent_ir: Optional[dict] = None  # Modified Intent IR
    architecture_ir: Optional[dict] = None  # Modified Architecture IR
    config: Optional[dict] = None  # Modified Config (for stage 4 restart)


class DownloadRequest(BaseModel):
    config: dict  # Already-generated config (avoids re-running pipeline)


class RunCodeRequest(BaseModel):
    config: dict  # Already-generated config
    keep_alive: int = 300  # Seconds to keep the app alive after tests (0 = kill immediately)


class RunCodeResponse(BaseModel):
    success: bool
    port: int = 0
    base_url: str = ""
    startup_latency_seconds: float = 0
    smoke_tests_passed: int = 0
    smoke_tests_failed: int = 0
    smoke_tests: list = []
    errors: list = []


# ============================================================
# Routes
# ============================================================
@app.api_route("/sandbox/{port}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def sandbox_proxy(port: int, path: str, request: Request):
    """Proxy requests to the sandbox app running on localhost:port"""
    import httpx
    client = httpx.AsyncClient(timeout=30.0)
    url = f"http://127.0.0.1:{port}/{path}"
    try:
        if request.query_params:
            url += f"?{request.query_params}"
        body = await request.body() if request.method in ("POST", "PUT", "PATCH") else None
        headers = dict(request.headers)
        headers.pop("host", None)
        r = await client.request(request.method, url, content=body, headers=headers)
        return HTMLResponse(r.content, status_code=r.status_code, headers=dict(r.headers))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sandbox unreachable: {str(e)}")

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def index():
    """Serve the main UI."""
    static_dir = Path(__file__).parent.parent / "static"
    index_path = static_dir / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.post("/generate")
async def generate(req: GenerateRequest, request: Request):
    """
    Run the full 4-stage generation pipeline.
    Returns the complete config with all schemas, metrics, and assumptions.
    """
    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not await check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT} requests per {RATE_WINDOW}s."
        )

    reset_token_usage()

    orchestrator = PipelineOrchestrator()
    progress_events = []

    async def progress_callback(stage, status, message, data=None):
        progress_events.append({
            "stage": stage, "status": status, "message": message,
            "data": data, "timestamp": time.time(),
        })

    orchestrator.progress_callback = progress_callback
    result = await orchestrator.run(req.prompt, api_key=req.api_key)

    # Generate real code from the config (fast, no LLM calls)
    generated_code = None
    if result.get("config") and result.get("success"):
        try:
            generated_code = generate_code(result["config"])
        except Exception:
            pass  # Code gen is best-effort; pipeline result still valid

    return JSONResponse({
        **result,
        "generated_code": generated_code,
        "progress": progress_events,
    })


@app.post("/generate-stream")
async def generate_stream(req: GenerateRequest, request: Request):
    """
    Run the pipeline with SSE streaming progress.
    Uses asyncio.Queue for proper async progress delivery.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not await check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    reset_token_usage()

    async def event_stream():
        task = None
        try:
            queue: asyncio.Queue = asyncio.Queue()
            orchestrator = PipelineOrchestrator()

            async def progress_callback(stage, status, message, data=None):
                await queue.put({
                    "type": "progress",
                    "stage": stage, "status": status,
                    "message": message, "data": data,
                })

            orchestrator.progress_callback = progress_callback

            # Run pipeline in background task
            async def run_pipeline():
                try:
                    result = await orchestrator.run(req.prompt, api_key=req.api_key)
                    await queue.put({"type": "result", **result})
                except Exception as e:
                    await queue.put({"type": "error", "error": str(e)})

            task = asyncio.create_task(run_pipeline())

            # Yield from queue as events arrive (with heartbeat and timeout)
            heartbeat_interval = 15  # Send keepalive comment every 15s
            last_heartbeat = asyncio.get_event_loop().time()
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] in ("result", "error"):
                        break
                    last_heartbeat = asyncio.get_event_loop().time()
                except asyncio.TimeoutError:
                    # Send heartbeat comment to keep connection alive
                    yield ": heartbeat\n\n"
                    if asyncio.get_event_loop().time() - last_heartbeat > 120:
                        yield f"data: {json.dumps({'type': 'error', 'error': 'Pipeline timed out after 120s of inactivity'})}\n\n"
                        break

            await task  # Ensure cleanup
        except asyncio.CancelledError:
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/modify")
async def modify_pipeline(req: ModifyRequest, request: Request):
    """
    Re-run pipeline from a specific stage with modified intermediate data.
    Supports mid-way requirement changes.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not await check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    reset_token_usage()

    orchestrator = PipelineOrchestrator()
    orchestrator.state.user_prompt = req.user_prompt
    orchestrator.state.api_key = req.api_key

    # Restore state from provided intermediate data
    if req.intent_ir:
        orchestrator.state.intent_ir = req.intent_ir
    if req.architecture_ir:
        orchestrator.state.architecture_ir = req.architecture_ir
    if req.config:
        orchestrator.state.config = req.config

    progress_events = []

    async def progress_callback(stage, status, message, data=None):
        progress_events.append({
            "stage": stage, "status": status, "message": message,
            "data": data, "timestamp": time.time(),
        })

    orchestrator.progress_callback = progress_callback

    # Run from specified stage
    intermediate = None
    if req.stage <= 1 and req.intent_ir:
        intermediate = req.intent_ir
    elif req.stage <= 2 and req.architecture_ir:
        intermediate = req.architecture_ir

    result = await orchestrator.run_from_stage(req.stage, intermediate)

    return JSONResponse({
        **result,
        "progress": progress_events,
    })


@app.post("/download-code")
async def download_code(req: DownloadRequest):
    """
    Download generated project code as ZIP.
    Accepts an already-generated config -- does NOT re-run the pipeline.
    """
    config = req.config
    if not config or not config.get("metadata"):
        raise HTTPException(status_code=400, detail="Invalid config: missing metadata")

    try:
        files = generate_code(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Code generation failed: {str(e)}")
    files["config.json"] = json.dumps(config, indent=2)

    # Validate generated code
    validation_issues = validate_generated_code(files)
    files["VALIDATION_REPORT.txt"] = (
        "\n".join(
            f"[{i.get('type', 'unknown')}] {i.get('file', '?')}: {i.get('message', '')}"
            for i in validation_issues
        ) if validation_issues else "All files validated successfully."
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    zip_buffer.seek(0)

    app_name = config.get("metadata", {}).get("app_name", "generated-app")
    safe_name = "".join(c for c in app_name if c.isalnum() or c in "_- ").strip()[:50]

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={safe_name}.zip"},
    )


@app.post("/run-code")
async def run_code(req: RunCodeRequest):
    """
    Generate code and run it in a sandbox to verify it actually works.
    Spins up the generated app, runs health checks and smoke tests, then cleans up.
    """
    config = req.config
    if not config or not config.get("metadata"):
        raise HTTPException(status_code=400, detail="Invalid config: missing metadata")

    try:
        files = generate_code(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Code generation failed: {str(e)}")

    # Run in sandbox (keep alive so user can interact with the app)
    try:
        from app.runtime.sandbox import run_generated_app
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Sandbox runtime is not available. The /run-code feature requires the app.runtime.sandbox module."
        )
    try:
        result = await run_generated_app(
            config, files, timeout=60, run_smoke=True,
            keep_alive=req.keep_alive,
        )
        return JSONResponse(result.to_dict())
    except Exception as e:
        return JSONResponse({"success": False, "errors": [f"Sandbox crashed: {str(e)}"], "base_url": ""})


@app.post("/evaluate")
async def evaluate(limit: int = Query(default=None, ge=1, le=20, description="Max prompts to run (1-20)"), request: Request = None):
    """Run the evaluation benchmark and return metrics."""
    # Rate limit check
    if request:
        client_ip = request.client.host if request.client else "unknown"
        if not await check_rate_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {RATE_LIMIT} requests per {RATE_WINDOW}s."
            )
    from app.evaluation.runner import run_benchmark
    from app.evaluation.dataset import get_all_prompts

    prompts = get_all_prompts()
    results = await run_benchmark(prompts, limit=limit)
    return JSONResponse(results)


@app.get("/api/cost")
async def get_cost():
    """Get token usage and cost estimate for the current session."""
    return JSONResponse(estimate_cost())


# ============================================================
# Static files (mounted last so routes take precedence)
# ============================================================
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    from app.config import PORT
    uvicorn.run(app, host="0.0.0.0", port=PORT)
