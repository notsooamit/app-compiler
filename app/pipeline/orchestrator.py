"""
Pipeline Orchestrator
Coordinates the 4-stage pipeline: Intent -> Design -> Schema -> Refinement.
Tracks progress, timing, and handles mid-stream modifications.
Uses asyncio.to_thread() to avoid blocking the event loop.
"""
import asyncio
import time
import traceback
from typing import Callable, Optional
from . import intent, design, schema, refinement


class PipelineState:
    """Tracks state across pipeline stages for mid-way modification support."""

    def __init__(self):
        self.user_prompt: str = ""
        self.api_key: Optional[str] = None
        self.intent_ir: Optional[dict] = None
        self.architecture_ir: Optional[dict] = None
        self.config: Optional[dict] = None
        self.current_stage: int = 0
        self.errors: list = []
        self.stage_timings: dict = {}
        self.needs_clarification: bool = False
        self.clarification_questions: list = []
        self.run_sandbox: bool = False
        self.sandbox_result: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "current_stage": self.current_stage,
            "needs_clarification": self.needs_clarification,
            "clarification_questions": self.clarification_questions,
            "stage_timings": self.stage_timings,
            "errors": self.errors,
            "has_intent": self.intent_ir is not None,
            "has_architecture": self.architecture_ir is not None,
            "has_config": self.config is not None,
        }


class PipelineOrchestrator:
    """Orchestrates the multi-stage generation pipeline."""

    def __init__(self, progress_callback: Optional[Callable] = None):
        self.progress_callback = progress_callback
        self.state = PipelineState()

    async def run(self, user_prompt: str, api_key: Optional[str] = None,
                  run_sandbox: bool = False) -> dict:
        """
        Run the full 4-stage pipeline using asyncio.to_thread() to avoid blocking.

        Args:
            user_prompt: The user's natural language app description.
            api_key: User-provided DeepSeek API key (required).
            run_sandbox: If True, spin up the generated app and run smoke tests.

        Returns:
            Dict with final config, timing, metrics, and any clarifications needed.
        """
        self.state = PipelineState()
        self.state.user_prompt = user_prompt
        self.state.api_key = api_key
        self.state.run_sandbox = run_sandbox

        try:
            # Stage 1: Intent Extraction
            await self._emit_progress("stage1", "running", "Extracting intent...")
            t0 = time.time()
            self.state.intent_ir = await asyncio.to_thread(
                intent.run, user_prompt, api_key
            )
            self.state.stage_timings["stage1_intent"] = round(time.time() - t0, 2)
            self.state.current_stage = 1

            # Check if clarification needed (always enforce strict mode)
            if self.state.intent_ir.get("clarification_questions"):
                self.state.needs_clarification = True
                self.state.clarification_questions = self.state.intent_ir["clarification_questions"]
                await self._emit_progress("stage1", "needs_clarification", "Clarification needed")
                return self._build_response()

            await self._emit_progress("stage1", "complete", "Intent extracted",
                                      {"intent_ir": self.state.intent_ir})

            # Stage 2: System Design
            await self._emit_progress("stage2", "running", "Designing architecture...")
            t0 = time.time()
            self.state.architecture_ir = await asyncio.to_thread(
                design.run, self.state.intent_ir, api_key
            )
            self.state.stage_timings["stage2_design"] = round(time.time() - t0, 2)
            self.state.current_stage = 2
            await self._emit_progress("stage2", "complete", "Architecture designed",
                                      {"architecture_ir": self.state.architecture_ir})

            # Stage 3: Schema Generation
            await self._emit_progress("stage3", "running", "Generating schemas (5 parallel)...")
            t0 = time.time()
            self.state.config = await asyncio.to_thread(
                schema.run, self.state.architecture_ir, True, api_key
            )
            self.state.stage_timings["stage3_schema"] = round(time.time() - t0, 2)
            self.state.current_stage = 3
            await self._emit_progress("stage3", "complete", "Schemas generated")

            # Stage 4: Refinement
            await self._emit_progress("stage4", "running", "Validating and repairing...")
            t0 = time.time()
            self.state.config = await asyncio.to_thread(
                refinement.run,
                self.state.config, self.state.architecture_ir, 3, api_key
            )
            self.state.stage_timings["stage4_refinement"] = round(time.time() - t0, 2)
            self.state.current_stage = 4
            await self._emit_progress("stage4", "complete", "Refinement complete")

            # Optional: run sandbox to verify generated code actually works
            if run_sandbox and self.state.config and self.state.config.get("metadata", {}).get("validation_status") == "clean":
                await self._emit_progress("sandbox", "running", "Running sandbox tests...")
                try:
                    from app.generation.codegen import generate as _gen_code
                    from app.runtime.sandbox import run_generated_app as _run_sandbox
                    _files = _gen_code(self.state.config)
                    _sandbox_result = await _run_sandbox(self.state.config, _files, timeout=60, run_smoke=True)
                    self.state.sandbox_result = _sandbox_result.to_dict()
                    await self._emit_progress("sandbox", "complete",
                        f"Sandbox: {_sandbox_result.smoke_tests_passed}/{_sandbox_result.smoke_tests_passed + _sandbox_result.smoke_tests_failed} smoke tests passed")
                except Exception as _sandbox_err:
                    self.state.sandbox_result = {"success": False, "error": str(_sandbox_err)[:200]}

            return self._build_response()

        except Exception as e:
            self.state.errors.append({
                "stage": self.state.current_stage,
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
            await self._emit_progress("error", "error", str(e))
            return self._build_response()

    async def run_from_stage(self, stage: int, intermediate_data: dict = None) -> dict:
        """
        Re-run pipeline from a specific stage with modified intermediate data.
        Supports mid-way modification with proper timing and error handling.
        """
        provided_for_stage = None
        if stage <= 1 and intermediate_data:
            self.state.intent_ir = intermediate_data
            provided_for_stage = 1
        elif stage <= 2 and intermediate_data:
            self.state.architecture_ir = intermediate_data
            provided_for_stage = 2

        try:
            if stage <= 1 and provided_for_stage != 1:
                await self._emit_progress("stage1", "running", "Re-extracting intent...")
                t0 = time.time()
                self.state.intent_ir = await asyncio.to_thread(
                    intent.run, self.state.user_prompt, self.state.api_key
                )
                self.state.stage_timings["stage1_intent"] = round(time.time() - t0, 2)
                self.state.current_stage = 1
                await self._emit_progress("stage1", "complete", "Intent re-extracted")

            if stage <= 2 and provided_for_stage != 2:
                await self._emit_progress("stage2", "running", "Re-designing architecture...")
                t0 = time.time()
                self.state.architecture_ir = await asyncio.to_thread(
                    design.run, self.state.intent_ir, self.state.api_key
                )
                self.state.stage_timings["stage2_design"] = round(time.time() - t0, 2)
                self.state.current_stage = 2
                await self._emit_progress("stage2", "complete", "Architecture re-designed")

            if stage <= 3:
                await self._emit_progress("stage3", "running", "Re-generating schemas...")
                t0 = time.time()
                self.state.config = await asyncio.to_thread(
                    schema.run, self.state.architecture_ir, True, self.state.api_key
                )
                self.state.stage_timings["stage3_schema"] = round(time.time() - t0, 2)
                self.state.current_stage = 3
                await self._emit_progress("stage3", "complete", "Schemas re-generated")

            if stage <= 4:
                await self._emit_progress("stage4", "running", "Re-validating...")
                t0 = time.time()
                self.state.config = await asyncio.to_thread(
                    refinement.run,
                    self.state.config, self.state.architecture_ir, 3, self.state.api_key
                )
                self.state.stage_timings["stage4_refinement"] = round(time.time() - t0, 2)
                self.state.current_stage = 4
                await self._emit_progress("stage4", "complete", "Re-refinement complete")

        except Exception as e:
            self.state.errors.append({
                "stage": self.state.current_stage,
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
            await self._emit_progress("error", "error", str(e))

        return self._build_response()

    def _build_response(self) -> dict:
        """Build the final response dict."""
        from .llm import estimate_cost
        try:
            from app.evaluation.metrics import compute_quality_score
        except ImportError:
            compute_quality_score = lambda x: {}

        config = self.state.config
        validation_status = (
            config.get("metadata", {}).get("validation_status", "unknown")
            if config else "no_config"
        )
        repair_count = len(config.get("repair_log", [])) if config else 0

        quality_score = compute_quality_score({
            "config": config,
            "validation_status": validation_status,
            "repair_count": repair_count,
            "needs_clarification": self.state.needs_clarification,
            "intent_ir": self.state.intent_ir,
        })

        return {
            "success": len(self.state.errors) == 0 and not self.state.needs_clarification,
            "needs_clarification": self.state.needs_clarification,
            "clarification_questions": self.state.clarification_questions,
            "config": config,
            "intent_ir": self.state.intent_ir,
            "architecture_ir": self.state.architecture_ir,
            "stage_timings": self.state.stage_timings,
            "total_latency_seconds": round(sum(self.state.stage_timings.values()), 2),
            "validation_status": validation_status,
            "repair_count": repair_count,
            "assumptions_count": len(config.get("assumptions_log", [])) if config else 0,
            "quality_score": quality_score,
            "cost": estimate_cost(),
            "errors": self.state.errors,
            "sandbox_result": self.state.sandbox_result,
        }

    async def _emit_progress(self, stage: str, status: str, message: str, data: dict = None):
        """Emit progress update to callback if set."""
        if self.progress_callback:
            await self.progress_callback(stage, status, message, data)
