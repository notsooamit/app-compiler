"""
Automated benchmark runner.
Runs all 20 prompts through the pipeline and collects metrics.
Can be run as a script or imported.
"""
import asyncio
import json
import time
import sys
import os

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.evaluation.dataset import get_all_prompts, get_real_prompts, get_edge_cases
from app.evaluation.metrics import MetricsCollector
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.llm import reset_token_usage


async def run_benchmark(prompts: list = None, limit: int = None) -> dict:
    """
    Run the full evaluation benchmark.

    Args:
        prompts: List of prompt dicts. Defaults to all 20.
        limit: Max number of prompts to run (for quick testing).

    Returns:
        Dict with summary, by_category, and all run details.
    """
    if prompts is None:
        prompts = get_all_prompts()

    if limit:
        prompts = prompts[:limit]

    collector = MetricsCollector()
    orchestrator = PipelineOrchestrator()

    # API key from environment (required for benchmarks)
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY environment variable is required for benchmarks.")
        return {"error": "DEEPSEEK_API_KEY not set"}

    print(f"Running benchmark on {len(prompts)} prompts...")
    print("=" * 60)

    for i, p in enumerate(prompts):
        print(f"\n[{i+1}/{len(prompts)}] {p['id']}: {p['description']}")
        print(f"  Prompt: {p['prompt'][:100]}...")

        # Reset token tracking per prompt
        reset_token_usage()

        try:
            result = await orchestrator.run(p["prompt"], api_key=api_key)
            collector.record_run(
                p["id"], p["category"], p["prompt"], result,
                expected_behavior=p.get("expected_behavior"),
                expected_complexity=p.get("expected_complexity"),
                intent_ir=result.get("intent_ir"),
                architecture_ir=result.get("architecture_ir"),
            )

            status = "[OK] SUCCESS" if result["success"] else "[FAIL] FAILED"
            if result.get("needs_clarification"):
                status = "? CLARIFICATION_NEEDED"
            print(f"  {status} | Latency: {result.get('total_latency_seconds', 0)}s | Repairs: {result.get('repair_count', 0)}")
        except Exception as e:
            print(f"  [FAIL] ERROR: {str(e)[:100]}")
            collector.record_run(p["id"], p["category"], p["prompt"], {
                "success": False,
                "errors": [{"stage": 0, "error": str(e)}],
            }, expected_behavior=p.get("expected_behavior"),
               expected_complexity=p.get("expected_complexity"))

    print("\n" + "=" * 60)
    print("BENCHMARK COMPLETE")
    print("=" * 60)

    summary = collector.compute_summary()
    by_category = collector.by_category()

    print("\n[METRICS] SUMMARY:")
    print(f"  Success Rate: {summary['success_rate']}%")
    print(f"  Executable: {summary['executability_rate']}%")
    print(f"  Avg Quality Score: {summary.get('avg_quality_score', 'N/A')}")
    print(f"  Avg Latency: {summary['avg_latency_seconds']}s")
    print(f"  Avg Repairs/Request: {summary['avg_repairs_per_request']}")
    print(f"  Total Cost: ${summary['total_cost_usd']}")
    print(f"  Avg Cost per Quality Point: ${summary.get('avg_cost_per_quality_point', 'N/A')}")
    print(f"  Failure Types: {summary['failure_type_distribution']}")

    print("\n[METRICS] BY CATEGORY:")
    print(f"  Real Prompts: {by_category.get('real_prompts', {}).get('success_rate', 'N/A')}% success")
    print(f"  Edge Cases: {by_category.get('edge_cases', {}).get('success_rate', 'N/A')}% success")

    # Determine project root (3 levels up from this file: runner.py -> evaluation -> app -> root)
    _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Generate cost-quality report
    report = collector.generate_cost_quality_report()
    report_path = os.path.join(_proj_root, "COST_QUALITY_ANALYSIS.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\n[REPORT] Cost-Quality Analysis written to: {report_path}")

    # Export full results
    json_path = os.path.join(_proj_root, "benchmark_results.json")
    collector.export_json(json_path)
    print(f"[REPORT] Full benchmark results exported to: {json_path}")

    return {
        "summary": summary,
        "by_category": by_category,
    }


if __name__ == "__main__":
    asyncio.run(run_benchmark())
