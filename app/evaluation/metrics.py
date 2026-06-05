"""
Metrics collector and reporter for evaluation runs.
Tracks: success rate, retries, failure types, latency, token usage, executability.
"""
import json
from typing import List, Dict
from datetime import datetime


class MetricsCollector:
    """Collects and aggregates metrics across multiple pipeline runs."""

    def __init__(self):
        self.runs: List[Dict] = []

    def record_run(self, prompt_id: str, category: str, prompt: str, result: dict,
                   expected_behavior: str = None, expected_complexity: str = None,
                   intent_ir: dict = None, architecture_ir: dict = None):
        """Record the result of a single pipeline run. Handles missing config safely."""
        config = result.get("config") or {}
        repair_log = config.get("repair_log", []) if config else []

        run_data = {
            "prompt_id": prompt_id,
            "category": category,
            "prompt": prompt[:200],
            "timestamp": datetime.now().isoformat(),
            "success": result.get("success", False),
            "needs_clarification": result.get("needs_clarification", False),
            "validation_status": config.get("metadata", {}).get("validation_status", "no_config")
                if config else "no_config",
            "total_latency_seconds": result.get("total_latency_seconds", 0),
            "stage_timings": result.get("stage_timings", {}),
            "repair_count": len(repair_log),
            "assumptions_count": len(config.get("assumptions_log", [])) if config else 0,
            "repair_log": repair_log,
            "errors": result.get("errors", []),
            "cost": result.get("cost", {}),
            "config": config,
            "expected_behavior": expected_behavior,
            "expected_complexity": expected_complexity,
            "intent_ir": intent_ir or result.get("intent_ir"),
            "architecture_ir": architecture_ir or result.get("architecture_ir"),
        }
        run_data["failure_type"] = self._classify_failure(run_data)
        run_data["quality_score"] = self._compute_quality_score(run_data)
        run_data["cost_efficiency"] = self._compute_cost_efficiency(run_data)
        self.runs.append(run_data)

    def _classify_failure(self, run: dict) -> str:
        """Classify the type of failure for this run."""
        if run["success"]:
            return "none"
        if run["needs_clarification"]:
            return "needs_clarification"
        if run["errors"]:
            return "pipeline_error"
        if run["validation_status"] == "has_unresolved":
            return "unresolvable_issues"
        if run["validation_status"] == "max_passes_exhausted":
            return "max_passes_exhausted"
        return "unknown"

    def _compute_quality_score(self, run_data: dict) -> dict:
        """Compute quality dimensions for a single run."""
        return compute_quality_score(run_data)

    def _compute_cost_efficiency(self, run_data: dict) -> dict:
        """Compute cost efficiency metrics for a single run."""
        cost_data = run_data.get("cost") or {}
        quality = (run_data.get("quality_score") or {}).get("composite", 1)
        total_cost = cost_data.get("estimated_cost_usd", 0)
        total_tokens = cost_data.get("total_tokens", 0)
        return {
            "total_cost_usd": total_cost,
            "tokens_total": total_tokens,
            "cost_per_quality_point": round(total_cost / max(quality, 1), 6),
            "tokens_per_quality_point": round(total_tokens / max(quality, 1), 1),
        }

    def compute_summary(self) -> dict:
        """Compute aggregate metrics across all runs."""
        if not self.runs:
            return {"error": "No runs recorded"}

        total = len(self.runs)
        successful = sum(1 for r in self.runs if r["success"])
        needs_clarification = sum(1 for r in self.runs if r["needs_clarification"])

        latencies = [r["total_latency_seconds"] for r in self.runs if r["total_latency_seconds"] > 0]
        repair_counts = [r["repair_count"] for r in self.runs]
        total_cost = sum(r.get("cost", {}).get("estimated_cost_usd", 0) for r in self.runs)

        failure_types = {}
        for r in self.runs:
            ft = r["failure_type"]
            failure_types[ft] = failure_types.get(ft, 0) + 1

        # Executability: config must have "clean" validation status (all 7 layers passed)
        executable = sum(1 for r in self.runs if r.get("validation_status") == "clean")

        # Quality score aggregation
        composites = []
        cost_per_q = []
        quality_by_cat = {"real": [], "edge": []}
        for r in self.runs:
            qs = r.get("quality_score") or {}
            composite = qs.get("composite", 0)
            composites.append(composite)
            ce = r.get("cost_efficiency") or {}
            cost_per_q.append(ce.get("cost_per_quality_point", 0))
            cat = r.get("category", "unknown")
            if cat in quality_by_cat:
                quality_by_cat[cat].append(composite)

        quality_by_category = {}
        for cat, vals in quality_by_cat.items():
            if vals:
                quality_by_category[cat] = {
                    "avg_quality_score": round(sum(vals) / len(vals), 1),
                    "count": len(vals),
                }

        return {
            "total_runs": total,
            "successful": successful,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "needs_clarification": needs_clarification,
            "executable_configs": executable,
            "executability_rate": round(executable / total * 100, 1) if total > 0 else 0,
            "avg_latency_seconds": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "min_latency_seconds": round(min(latencies), 2) if latencies else 0,
            "max_latency_seconds": round(max(latencies), 2) if latencies else 0,
            "avg_repairs_per_request": round(sum(repair_counts) / total, 1),
            "total_repairs_applied": sum(repair_counts),
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_request_usd": round(total_cost / total, 6) if total > 0 else 0,
            "failure_type_distribution": failure_types,
            "avg_quality_score": round(sum(composites) / len(composites), 1) if composites else 0,
            "avg_cost_per_quality_point": round(sum(cost_per_q) / len(cost_per_q), 6) if cost_per_q else 0,
            "quality_by_category": quality_by_category,
        }

    def by_category(self) -> dict:
        """Break down metrics by category (real vs edge)."""
        real_runs = [r for r in self.runs if r["category"] == "real"]
        edge_runs = [r for r in self.runs if r["category"] == "edge"]

        def summarize(runs):
            if not runs:
                return {}
            total = len(runs)
            successful = sum(1 for r in runs if r["success"])
            latencies = [r["total_latency_seconds"] for r in runs if r["total_latency_seconds"] > 0]
            return {
                "total": total,
                "successful": successful,
                "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
                "avg_latency": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            }

        return {
            "real_prompts": summarize(real_runs),
            "edge_cases": summarize(edge_runs),
        }

    def generate_cost_quality_report(self) -> str:
        """Generate a detailed markdown cost-quality analysis report."""
        if not self.runs:
            return "No runs recorded."

        summary = self.compute_summary()
        by_category = self.by_category()

        # --- By Complexity breakdown ---
        complexity_buckets = {"simple": [], "medium": [], "complex": []}
        for r in self.runs:
            comp = r.get("expected_complexity", "unknown")
            if comp in complexity_buckets:
                complexity_buckets[comp].append(r)

        def avg_complexity(runs):
            if not runs:
                return {}
            total = len(runs)
            composites = [r.get("quality_score", {}).get("composite", 0) for r in runs]
            costs = [r.get("cost", {}).get("estimated_cost_usd", 0) for r in runs]
            cq = [r.get("cost_efficiency", {}).get("cost_per_quality_point", 0) for r in runs]
            successful = sum(1 for r in runs if r["success"])
            return {
                "count": total,
                "avg_quality": round(sum(composites) / len(composites), 1) if composites else 0,
                "avg_cost": round(sum(costs) / len(costs), 6) if costs else 0,
                "avg_cost_per_q": round(sum(cq) / len(cq), 6) if cq else 0,
                "success_rate": round(successful / total * 100, 1),
            }

        # --- Quality Dimensions Breakdown ---
        dims = ["schema_completeness", "validation_pass_rate", "repair_effectiveness",
                "code_executability", "clarity_detection", "conflict_detection"]
        dim_avgs = {}
        for d in dims:
            vals = [r.get("quality_score", {}).get(d, 0) for r in self.runs]
            dim_avgs[d] = round(sum(vals) / len(vals), 1) if vals else 0

        lines = []
        lines.append("# Cost-Quality Analysis Report")
        lines.append("")
        lines.append(f"Generated: {datetime.now().isoformat()}")
        lines.append(f"Total runs: {summary['total_runs']}")
        lines.append("")

        # --- Summary Table ---
        lines.append("## 1. Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Runs | {summary['total_runs']} |")
        lines.append(f"| Success Rate | {summary['success_rate']}% |")
        lines.append(f"| Executability Rate | {summary['executability_rate']}% |")
        lines.append(f"| Avg Quality Score (Composite) | {summary['avg_quality_score']} |")
        lines.append(f"| Avg Cost per Request | ${summary['avg_cost_per_request_usd']} |")
        lines.append(f"| Avg Cost per Quality Point | ${summary['avg_cost_per_quality_point']} |")
        lines.append(f"| Avg Latency | {summary['avg_latency_seconds']}s |")
        lines.append(f"| Total Cost | ${summary['total_cost_usd']} |")
        lines.append("")

        # --- By Complexity ---
        lines.append("## 2. By Complexity")
        lines.append("")
        lines.append("| Complexity | Count | Avg Quality | Avg Cost | Cost/Q Point | Success Rate |")
        lines.append("|------------|-------|-------------|----------|--------------|--------------|")
        for comp in ["simple", "medium", "complex"]:
            a = avg_complexity(complexity_buckets[comp])
            if a:
                lines.append(
                    f"| {comp} | {a['count']} | {a['avg_quality']} | ${a['avg_cost']} | "
                    f"${a['avg_cost_per_q']} | {a['success_rate']}% |"
                )
        lines.append("")

        # --- By Category ---
        lines.append("## 3. By Category")
        lines.append("")
        lines.append("| Category | Count | Success Rate | Avg Quality |")
        lines.append("|----------|-------|--------------|-------------|")
        for cat_key, cat_label in [("real_prompts", "Real"), ("edge_cases", "Edge")]:
            cat_data = by_category.get(cat_key, {})
            if cat_data:
                qc = summary.get("quality_by_category", {})
                q_label = "real" if cat_key == "real_prompts" else "edge"
                avg_q = qc.get(q_label, {}).get("avg_quality_score", "N/A")
                lines.append(
                    f"| {cat_label} | {cat_data.get('total', 0)} | "
                    f"{cat_data.get('success_rate', 'N/A')}% | {avg_q} |"
                )
        lines.append("")

        # --- Quality Dimensions ---
        lines.append("## 4. Quality Dimensions Breakdown")
        lines.append("")
        lines.append("| Dimension | Avg Score | Weight |")
        lines.append("|-----------|-----------|--------|")
        weights_fmt = {
            "schema_completeness": 0.20,
            "validation_pass_rate": 0.25,
            "repair_effectiveness": 0.15,
            "code_executability": 0.25,
            "clarity_detection": 0.10,
            "conflict_detection": 0.05,
        }
        for d in dims:
            lines.append(f"| {d} | {dim_avgs[d]} | {weights_fmt.get(d, '')} |")
        lines.append(f"| **Composite** | **{summary['avg_quality_score']}** | **1.00** |")
        lines.append("")

        # --- Tradeoff Analysis ---
        lines.append("## 5. Tradeoff Analysis")
        lines.append("")

        # Temperature vs Determinism
        lines.append("### Temperature vs Determinism")
        lines.append("")
        lines.append("The pipeline uses LLM calls with inherent temperature-based variability. "
                      "Lower temperatures produce more deterministic outputs but may reduce "
                      "creativity in schema design. Higher temperatures can generate diverse "
                      "architectures at the cost of reproducibility. The current configuration "
                      "favors deterministic behavior for consistent evaluation results.")
        lines.append("")

        # Model Choice
        lines.append("### Model Choice")
        lines.append("")
        estimated_cost = summary['total_cost_usd']
        lines.append(f"The benchmark used DeepSeek as the LLM provider. "
                      f"Total estimated cost was ${estimated_cost}. "
                      "Different model choices would affect both quality and cost:")
        for model, mult in [("DeepSeek-Chat (current)", 1.0), ("GPT-4o", 3.0), ("Claude 3.5 Sonnet", 2.5), ("Claude 3 Haiku", 0.5)]:
            projected = estimated_cost * mult
            lines.append(f"- **{model}**: ~${round(projected, 2)} total cost "
                         f"(avg ${round(projected / max(summary['total_runs'], 1), 6)}/request)")
        lines.append("")

        # Repair Passes
        lines.append("### Repair Passes")
        lines.append("")
        avg_repairs = summary['avg_repairs_per_request']
        total_repairs = summary['total_repairs_applied']
        lines.append(f"The pipeline applied an average of {avg_repairs} repairs per run "
                      f"({total_repairs} total). Each repair pass adds latency and cost but "
                      "improves validation pass rates. The current configuration uses up to 3 "
                      "repair passes. Reducing passes would lower cost/run but may decrease "
                      "executability rates.")
        lines.append("")

        # Parallel vs Sequential
        lines.append("### Parallel vs Sequential Execution")
        lines.append("")
        lines.append("Stage 3 (Schema Generation) runs 5 parallel LLM calls per prompt. "
                      "This reduces wall-clock time but increases total token usage and cost. "
                      "Sequential execution would reduce peak token consumption at the cost of "
                      "increased latency. The parallel approach is preferred for interactive "
                      "scenarios; batch processing could use sequential to minimize cost.")
        lines.append("")

        # --- Recommendations ---
        lines.append("## 6. Recommendations")
        lines.append("")
        quality = summary['avg_quality_score']
        if quality < 60:
            lines.append("- **Increase repair passes**: Current quality is low; more repair iterations may help.")
        elif quality < 80:
            lines.append("- **Quality is moderate**: Consider targeted improvements in the lowest-scoring quality dimensions.")
        else:
            lines.append("- **Quality is good**: Focus on cost optimization rather than further quality improvements.")

        lines.append("- **Cost optimization**: Review prompts with the highest cost-per-quality-point ratio for potential streamlining.")
        lines.append(f"- **Edge case handling**: {by_category.get('edge_cases', {}).get('success_rate', 'N/A')}% success rate on edge cases "
                      f"vs {by_category.get('real_prompts', {}).get('success_rate', 'N/A')}% on real prompts. "
                      "Consider dedicated handling for conflicting requirements and vague inputs.")
        lines.append("- **Monitoring**: Track quality_score trends over time as prompts are added or modified.")

        lines.append("")
        return "\n".join(lines)

    def export_json(self, filepath: str):
        """Export full results to JSON file."""
        summary = self.compute_summary()
        by_category = self.by_category()
        output = {
            "summary": summary,
            "by_category": by_category,
            "runs": self.runs,
            "exported_at": datetime.now().isoformat(),
        }
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2)


# ——— Standalone quality scoring (importable without MetricsCollector) ———

def compute_quality_score(run_data: dict) -> dict:
    """Standalone: compute quality dimensions for a pipeline response.
    Can be called from the orchestrator without a MetricsCollector instance."""
    scores = {}

    # Schema Completeness (0-100): check that config has all expected keys
    config = run_data.get("config") or {}
    expected_keys = ["metadata", "db_schema", "api_schema", "ui_schema", "auth_schema", "business_logic"]
    present = sum(1 for k in expected_keys if k in config and config[k])
    scores["schema_completeness"] = round(present / len(expected_keys) * 100, 1)

    # Validation Pass Rate (0-100)
    status = run_data.get("validation_status", "no_config")
    if status == "clean":
        scores["validation_pass_rate"] = 100
    elif status == "has_unresolved":
        scores["validation_pass_rate"] = 50
    elif status == "max_passes_exhausted":
        scores["validation_pass_rate"] = 30
    else:
        scores["validation_pass_rate"] = 0

    # Repair Effectiveness (0-100)
    repair_count = run_data.get("repair_count", 0)
    if status == "clean" and repair_count > 0:
        scores["repair_effectiveness"] = min(100, 50 + repair_count * 10)
    elif status == "clean":
        scores["repair_effectiveness"] = 100  # nothing to repair
    else:
        scores["repair_effectiveness"] = max(0, 30 - repair_count * 5)

    # Code Executability (0-100)
    if status == "clean":
        scores["code_executability"] = 100
    elif status == "has_unresolved":
        scores["code_executability"] = 50
    else:
        scores["code_executability"] = 0

    # Clarity Detection (0 or 100) - for edge cases that need clarification
    needs_clarification = run_data.get("needs_clarification", False)
    expected_behavior = run_data.get("expected_behavior", "")
    if expected_behavior == "needs_clarification":
        scores["clarity_detection"] = 100 if needs_clarification else 0
    elif expected_behavior in ("needs_clarification_or_assumes",):
        scores["clarity_detection"] = 100  # either way is fine
    else:
        scores["clarity_detection"] = 100 if not needs_clarification else 50  # false positive

    # Conflict Detection (0 or 100)
    intent_ir = run_data.get("intent_ir") or {}
    ambiguities = intent_ir.get("ambiguities", [])
    if expected_behavior in ("flags_conflict", "flags_contradiction"):
        scores["conflict_detection"] = 100 if ambiguities else 50
    else:
        scores["conflict_detection"] = 100  # no conflicts expected

    # Composite score
    weights = {
        "schema_completeness": 0.20,
        "validation_pass_rate": 0.25,
        "repair_effectiveness": 0.15,
        "code_executability": 0.25,
        "clarity_detection": 0.10,
        "conflict_detection": 0.05,
    }
    composite = sum(scores[k] * weights[k] for k in weights)
    scores["composite"] = round(composite, 1)

    return scores
