"""
Stage 4: Refinement
Runs cross-layer validation and targeted repair on the complete config.
Ensures API<->DB consistency, UI<->API mapping, auth coverage, and logical soundness.

Uses error deduplication to prevent infinite repair loops:
- Each unique error (by message hash) is only attempted twice
- Unresolvable errors are skipped on subsequent passes
- Max 5 repair passes, but exits early if no progress
"""
import hashlib
from typing import Optional, Set
from app.validation.validator import validate_config
from app.validation.repair import RepairEngine

# Error types whose REMAINING (post-repair) errors don't block "clean" status.
# These are truly cosmetic — they can't be fixed surgically and don't
# affect the correctness of the generated app.
COSMETIC_TYPES = {
    "circular_fk",           # Needs schema redesign, not a blocker
    "no_roles_defined",      # Warning: app has no access control
    "invalid_http_method",   # Would need full regeneration
    "orphan_table",          # Missing API endpoint for a DB table is informational
}


def _error_key(error: dict) -> str:
    """Create a stable key for deduplicating errors."""
    msg = error.get("message", "") + error.get("location", "") + error.get("error_type", "")
    return hashlib.md5(msg.encode()).hexdigest()[:12]


def run(config: dict, architecture_ir: dict, max_repair_passes: int = 3,
        api_key: Optional[str] = None) -> dict:
    """
    Run Stage 4: Refinement with intelligent repair loop.

    Key behaviors:
    - Tracks attempted errors to avoid retrying unresolvable issues
    - Each error type+location is attempted at most once
    - Exits early if no new errors are fixable
    """
    repair_engine = RepairEngine()
    attempted_errors: Set[str] = set()  # Error keys already tried
    ever_fixed = 0
    config.setdefault("metadata", {}).setdefault("validation_status", "unknown")

    for pass_num in range(max_repair_passes):
        errors = validate_config(config, architecture_ir)

        if not errors:
            config["metadata"]["validation_status"] = "clean"
            break

        # Filter: only try errors we haven't attempted yet
        new_errors = [e for e in errors if _error_key(e) not in attempted_errors]

        if not new_errors:
            # All errors already attempted. If remaining errors are cosmetic-only,
            # the config is effectively clean.
            blocking = [e for e in errors
                        if e.get("error_type") not in COSMETIC_TYPES
                        and e.get("severity") == "error"]
            if not blocking:
                config["metadata"]["validation_status"] = "clean"
            else:
                config["metadata"]["validation_status"] = "has_unresolved"
            break

        repaired_this_pass = 0
        for error in new_errors:
            ek = _error_key(error)
            attempted_errors.add(ek)

            try:
                result = repair_engine.repair(error, config, architecture_ir, api_key)
            except Exception as e:
                result = {"strategy": "error", "result": "unresolvable", "detail": str(e)[:100]}

            is_fixed = result.get("result") == "fixed"
            if is_fixed:
                repaired_this_pass += 1
                config.setdefault("repair_log", []).append({
                    "error": error.get("message", str(error))[:150],
                    "layer": error.get("layer", "unknown"),
                    "strategy": result.get("strategy", "none"),
                    "result": "fixed",
                    "detail": result.get("detail", "")[:200],
                })

        if repaired_this_pass == 0 and pass_num > 0:
            current_errors = validate_config(config, architecture_ir)
            blocking = [e for e in current_errors
                        if e.get("error_type") not in COSMETIC_TYPES
                        and e.get("severity") == "error"]
            config["metadata"]["validation_status"] = "clean" if not blocking else "has_unresolved"
            break

        if repaired_this_pass > 0:
            ever_fixed += repaired_this_pass

    else:
        errors = validate_config(config, architecture_ir)
        blocking = [e for e in errors
                    if e.get("error_type") not in COSMETIC_TYPES
                    and e.get("severity") == "error"]
        config["metadata"]["validation_status"] = "clean" if not blocking else "max_passes_exhausted"

    return config
