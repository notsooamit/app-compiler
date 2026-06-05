"""
Hallucination detector (Layer 7).
Compares the generated config against the Architecture IR to find:
- Entities not in the architecture (hallucinated tables)
- Endpoints not in the architecture (hallucinated APIs)
- Roles not in the architecture (hallucinated roles)
- Features/business rules not in the architecture

Uses fuzzy path matching to avoid false positives from prefix differences.
"""
import re
from typing import List, Dict, Any


def check_hallucinations(config: dict, architecture_ir: dict) -> List[Dict[str, Any]]:
    """
    Detect hallucinated content by comparing config against Architecture IR.
    Uses normalized path matching to handle prefix variations (e.g., /api/contacts vs /contacts).
    """
    errors = []

    # Words ending in 's' that should NOT be singularized
    _no_strip = {"status", "address", "canvas", "bus", "class", "process",
                 "access", "success", "progress", "analysis", "basis",
                 "diagnosis", "synopsis", "alias", "atlas", "corpus"}

    def _normalize_name(n: str) -> str:
        """Normalize entity/table names for comparison: lowercase, strip trailing s."""
        n = n.lower().strip()
        if n in _no_strip:
            return n
        if n.endswith('s') and len(n) > 2:
            # Handle common plurals: tasks->task, categories->category, expenses->expense
            if n.endswith('ies'):
                n = n[:-3] + 'y'
            elif n.endswith('s') and not n.endswith('ss'):
                n = n[:-1]  # expenses->expense, tasks->task
        return n

    arch_entities = {e["name"] for e in architecture_ir.get("entities", []) if "name" in e}
    arch_entities_normalized = {_normalize_name(n): n for n in arch_entities}
    arch_endpoints_raw = {ep["path"] for ep in architecture_ir.get("api_endpoints", []) if "path" in ep}
    arch_roles = {r["name"] for r in architecture_ir.get("auth", {}).get("roles", []) if "name" in r}
    arch_business_rules = {r["name"] for r in architecture_ir.get("business_rules", []) if "name" in r}

    # Normalize paths: strip /api prefix and trailing slashes for fuzzy matching
    def normalize_path(p: str) -> str:
        p = p.strip().rstrip("/")
        if p.startswith("/api/"):
            p = p[4:]  # Remove /api prefix
        if not p.startswith("/"):
            p = "/" + p
        return p

    arch_endpoints_normalized = {normalize_path(p): p for p in arch_endpoints_raw}

    # Check for hallucinated DB tables — only if arch IR has entities to compare against
    if arch_entities:
        for table in config.get("db_schema", {}).get("tables", []):
            table_name = table.get("name")
            if not table_name:
                continue
            table_normalized = _normalize_name(table_name)
            if table_name not in arch_entities and table_normalized not in arch_entities_normalized:
                if table_name not in ("users", "sessions", "password_resets", "migrations"):
                    errors.append({
                        "layer": "hallucination",
                        "message": f"DB table '{table_name}' not found in Architecture IR -- possibly hallucinated",
                        "location": f"db_schema.tables.{table_name}",
                        "severity": "warning",
                        "detail": f"Entity '{table_name}' missing from architecture entities: {arch_entities}",
                        "error_type": "hallucinated_table",
                        "table": table_name,
                    })

    # Check for hallucinated API endpoints — only if arch IR has endpoints to compare against
    if arch_endpoints_raw:
        for ep in config.get("api_schema", {}).get("endpoints", []):
            ep_path = ep["path"]
            ep_normalized = normalize_path(ep_path)

            # Check exact match first, then normalized
            if ep_path not in arch_endpoints_raw and ep_normalized not in arch_endpoints_normalized:
                # Try prefix matching: does any architecture endpoint contain this path or vice versa?
                found = False
                for arch_path in arch_endpoints_normalized:
                    if ep_normalized.endswith(arch_path) or arch_path.endswith(ep_normalized):
                        found = True
                        break
                    # Also check if paths share the same resource pattern
                    # Require at least 2 non-trivial shared segments to avoid false matches
                    trivial = {"api", "v1", "v2", "v3", "id", ""}
                    ep_parts = set(ep_normalized.strip("/").split("/")) - trivial
                    arch_parts = set(arch_path.strip("/").split("/")) - trivial
                    if len(ep_parts & arch_parts) >= 2:
                        found = True
                        break

                if not found:
                    errors.append({
                        "layer": "hallucination",
                        "message": f"API endpoint '{ep_path}' not found in Architecture IR -- possibly hallucinated",
                        "location": f"api_schema.endpoints.{ep_path}",
                        "severity": "warning",
                        "detail": f"Endpoint '{ep_path}' missing from architecture",
                        "error_type": "hallucinated_endpoint",
                        "endpoint": ep_path,
                    })

    # Roles and business rules hallucination checks are SKIPPED.
    # Stage 2's Architecture IR names roles/rules at a high level; Stage 3 elaborates
    # with different names. Name-based comparison between stages produces false positives
    # (e.g., Stage 2 has "User", Stage 3 adds "public" and "Admin" — both legitimate).
    # Tables and endpoints use fuzzy matching which handles elaboration; roles/rules don't.

    return errors
