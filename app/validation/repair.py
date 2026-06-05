"""
Targeted Repair Engine
For each validation error, applies a specific repair strategy.
NEVER regenerates the entire config -- only fixes the specific issue.

Repair strategies:
- api_field_not_in_db: Add column to DB or remove from API
- ui_binding_no_api: Generate the missing API endpoint
- hallucinated_*: Remove the hallucinated item
- auth_*: Fix auth configuration
- reference_integrity: Fix broken references
- type_safety: Coerce to valid type
- auth_missing_matrix: Add role to access matrix with role-appropriate defaults
"""
from typing import Dict, Any, Optional
from app.pipeline.llm import structured_call
from app.validation.consistency import _entity_matches_table

# Small focused schemas for repair operations
FIELD_SCHEMA = {
    "type": "object",
    "required": ["name", "type"],
    "properties": {
        "name": {"type": "string"},
        "type": {"type": "string"},
        "nullable": {"type": "boolean"},
        "unique": {"type": "boolean"},
        "primary_key": {"type": "boolean"},
    }
}

API_ENDPOINT_SCHEMA = {
    "type": "object",
    "required": ["method", "path", "description", "entity"],
    "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "path": {"type": "string"},
        "description": {"type": "string"},
        "entity": {"type": "string"},
        "auth_required": {"type": "boolean"},
        "roles": {"type": "array", "items": {"type": "string"}},
        "response_schema": {"type": "object"},
    }
}


class RepairEngine:
    """
    Targeted repair engine. Applies surgical fixes to specific errors
    without regenerating the entire configuration.
    """

    def repair(self, error: Dict[str, Any], config: dict, architecture_ir: dict,
               api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Dispatch to the appropriate repair strategy based on error type.

        Returns:
            {"strategy": str, "result": "fixed"|"unresolvable"|"partial", "detail": str}
        """
        error_type = error.get("error_type", "")
        layer = error.get("layer", "")

        strategy_map = {
            "api_field_not_in_db": self._repair_api_field_not_in_db,
            "ui_binding_no_api": self._repair_ui_binding_no_api,
            "hallucinated_table": self._repair_hallucinated,
            "hallucinated_endpoint": self._repair_hallucinated,
            "hallucinated_role": self._repair_hallucinated,
            "hallucinated_rule": self._repair_hallucinated,
            "auth_no_roles": self._repair_auth_no_roles,
            "auth_unknown_roles": self._repair_auth_unknown_roles,
            "auth_missing_matrix": self._repair_auth_missing_matrix,
            "invalid_sql_type": self._repair_sql_type,
            "broken_fk_reference": self._repair_broken_fk,
            "api_unknown_entity": self._repair_api_unknown_entity,
            "missing_db_table": self._repair_missing_db_table,
            "rule_unknown_entity": self._repair_broken_ref,
            "duplicate_endpoint": self._repair_duplicate_endpoint,
            # These are informational — repair can't fix them
            "circular_fk": lambda e, c, a, ak: {"strategy": "none", "result": "unresolvable", "detail": "Circular FK — needs schema redesign"},
            "no_roles_defined": lambda e, c, a, ak: {"strategy": "none", "result": "unresolvable", "detail": "No roles defined — needs regeneration"},
            "auth_extra_matrix_entries": self._repair_extra_matrix_entries,
            "invalid_http_method": lambda e, c, a, ak: {"strategy": "none", "result": "unresolvable", "detail": "Invalid HTTP method — needs regeneration"},
        }

        handler = strategy_map.get(error_type)
        if handler:
            return handler(error, config, architecture_ir, api_key)

        # Fallback by layer
        if layer == "required_fields":
            return {"strategy": "needs_regeneration", "result": "unresolvable",
                    "detail": f"Missing required field: {error.get('message', '')}. Needs targeted regeneration."}
        elif layer == "type_safety":
            return self._repair_type(error, config, architecture_ir)
        elif layer == "reference_integrity":
            return self._repair_broken_ref(error, config, architecture_ir)

        return {"strategy": "none", "result": "unresolvable",
                "detail": f"No strategy for error type: {error_type}"}

    def _repair_api_field_not_in_db(self, error: dict, config: dict, arch: dict,
                                     api_key: Optional[str] = None) -> dict:
        """Add missing DB column for API response field."""
        field = error.get("field", "")
        entity = error.get("entity", "")

        try:
            result = structured_call(
                system_prompt="Generate a single DB column definition for the missing field.",
                user_message=f"Generate a DB column for field '{field}' in table '{entity}'. Context: {error.get('detail', '')}",
                tool_name="output_column",
                tool_description="Output a single database column definition",
                input_schema=FIELD_SCHEMA,
                api_key=api_key,
            )
            for table in config.get("db_schema", {}).get("tables", []):
                if _entity_matches_table(entity, table["name"]):
                    table.setdefault("columns", []).append(result)
                    return {"strategy": "add_db_column", "result": "fixed",
                            "detail": f"Added column '{field}' to table '{table['name']}'"}
            return {"strategy": "add_db_column", "result": "unresolvable",
                    "detail": f"Table matching '{entity}' not found"}
        except Exception as e:
            return {"strategy": "add_db_column", "result": "unresolvable", "detail": str(e)}

    def _repair_ui_binding_no_api(self, error: dict, config: dict, arch: dict,
                                    api_key: Optional[str] = None) -> dict:
        """Generate the missing API endpoint for a UI data binding."""
        data_binding = error.get("data_binding", "")
        try:
            result = structured_call(
                system_prompt="Generate a REST API endpoint for a UI data binding that is missing its backend.",
                user_message=f"Generate missing API endpoint for: {data_binding}",
                tool_name="output_endpoint",
                tool_description="Output a single API endpoint definition",
                input_schema=API_ENDPOINT_SCHEMA,
                api_key=api_key,
            )
            config.setdefault("api_schema", {}).setdefault("endpoints", []).append(result)
            return {"strategy": "generate_endpoint", "result": "fixed",
                    "detail": f"Generated endpoint: {result.get('path', 'unknown')}"}
        except Exception as e:
            return {"strategy": "generate_endpoint", "result": "unresolvable", "detail": str(e)}

    def _repair_hallucinated(self, error: dict, config: dict, arch: dict,
                               api_key: Optional[str] = None) -> dict:
        """Remove hallucinated items AND cascade cleanup all references to them."""
        error_type = error.get("error_type", "")

        if "table" in error_type:
            table_name = error.get("table", "")
            # Remove the table
            tables = config.get("db_schema", {}).get("tables", [])
            config["db_schema"]["tables"] = [t for t in tables if t["name"] != table_name]
            # Cascade: remove relations involving this table
            relations = config.get("db_schema", {}).get("relations", [])
            config["db_schema"]["relations"] = [
                r for r in relations
                if r.get("from_table") != table_name and r.get("to_table") != table_name
                and r.get("junction_table") != table_name
            ]
            # Cascade: remove FK columns in OTHER tables pointing to this table
            for table in config.get("db_schema", {}).get("tables", []):
                table["columns"] = [
                    c for c in table.get("columns", [])
                    if not (c.get("foreign_key", {}).get("table") == table_name)
                ]
            # Cascade: remove API endpoints referencing this entity
            endpoints = config.get("api_schema", {}).get("endpoints", [])
            config["api_schema"]["endpoints"] = [
                e for e in endpoints if e.get("entity") != table_name
            ]
            # Cascade: remove business rules involving this entity (handles duplicates)
            rules = config.get("business_logic", {}).get("rules", [])
            for rule in rules:
                rule["entities_involved"] = [
                    e for e in rule.get("entities_involved", []) if e != table_name
                ]
            return {"strategy": "remove_hallucinated", "result": "fixed",
                    "detail": f"Removed hallucinated table '{table_name}' and all references"}

        elif "endpoint" in error_type:
            ep_path = error.get("endpoint", "")
            endpoints = config.get("api_schema", {}).get("endpoints", [])
            config["api_schema"]["endpoints"] = [e for e in endpoints if e["path"] != ep_path]
            return {"strategy": "remove_hallucinated", "result": "fixed",
                    "detail": f"Removed hallucinated endpoint '{ep_path}'"}
        elif "role" in error_type:
            role_name = error.get("role", "")
            roles = config.get("auth_schema", {}).get("roles", [])
            config["auth_schema"]["roles"] = [r for r in roles if r["name"] != role_name]
            # Also remove from access_matrix
            access_matrix = config.get("auth_schema", {}).get("access_matrix", {})
            if role_name in access_matrix:
                del access_matrix[role_name]
            return {"strategy": "remove_hallucinated", "result": "fixed",
                    "detail": f"Removed hallucinated role '{role_name}'"}
        elif "rule" in error_type:
            rule_name = error.get("rule", "")
            rules = config.get("business_logic", {}).get("rules", [])
            config["business_logic"]["rules"] = [r for r in rules if r["name"] != rule_name]
            return {"strategy": "remove_hallucinated", "result": "fixed",
                    "detail": f"Removed hallucinated rule '{rule_name}'"}
        return {"strategy": "remove_hallucinated", "result": "unresolvable",
                "detail": "Unknown hallucination type"}

    def _repair_auth_no_roles(self, error: dict, config: dict, arch: dict,
                                api_key: Optional[str] = None) -> dict:
        """Add default roles to an unprotected auth-required endpoint."""
        ep_path = error.get("api_path", "")
        roles = config.get("auth_schema", {}).get("roles", [])
        default_roles = [r["name"] for r in roles] if roles else ["user"]
        for ep in config.get("api_schema", {}).get("endpoints", []):
            if ep["path"] == ep_path:
                ep["roles"] = default_roles
                return {"strategy": "add_default_roles", "result": "fixed",
                        "detail": f"Assigned roles {default_roles} to '{ep_path}'"}
        return {"strategy": "add_default_roles", "result": "unresolvable",
                "detail": f"Endpoint '{ep_path}' not found"}

    def _repair_auth_unknown_roles(self, error: dict, config: dict, arch: dict,
                                     api_key: Optional[str] = None) -> dict:
        """Add missing roles to auth schema with default permissions. Deduplicates existing roles."""
        unknown_roles = error.get("unknown_roles", [])
        existing_names = {r["name"] for r in config.get("auth_schema", {}).get("roles", []) if "name" in r}
        added = []
        for role_name in unknown_roles:
            if role_name not in existing_names:
                config.setdefault("auth_schema", {}).setdefault("roles", []).append({
                    "name": role_name,
                    "description": f"Auto-generated role: {role_name}",
                    "permissions": ["read"],
                })
                existing_names.add(role_name)
                added.append(role_name)
        if added:
            return {"strategy": "add_missing_roles", "result": "fixed",
                    "detail": f"Added roles {added} to auth schema"}
        return {"strategy": "add_missing_roles", "result": "fixed",
                "detail": f"Roles {unknown_roles} already exist — skipped"}

    def _repair_auth_missing_matrix(self, error: dict, config: dict, arch: dict,
                                      api_key: Optional[str] = None) -> dict:
        """Add missing roles to the access matrix with role-appropriate defaults."""
        missing_roles = error.get("missing_roles", [])
        access_matrix = config.setdefault("auth_schema", {}).setdefault("access_matrix", {})
        resources = [t["name"] for t in config.get("db_schema", {}).get("tables", [])]

        for role_name in missing_roles:
            # Assign permissions based on role name heuristics
            is_admin = "admin" in role_name.lower()
            is_viewer = "viewer" in role_name.lower() or "read" in role_name.lower()
            if is_admin:
                perms = ["create", "read", "update", "delete", "list"]
            elif is_viewer:
                perms = ["read", "list"]
            else:
                perms = ["read", "list"]  # Safe default for unknown roles

            access_matrix[role_name] = {}
            for resource in resources:
                access_matrix[role_name][resource] = perms

        return {"strategy": "add_matrix_entries", "result": "fixed",
                "detail": f"Added roles {missing_roles} to access matrix with role-appropriate permissions"}

    def _repair_extra_matrix_entries(self, error: dict, config: dict, arch: dict,
                                      api_key: Optional[str] = None) -> dict:
        """Remove access_matrix entries for roles not defined in auth_schema.roles."""
        extra_roles = error.get("extra_roles", [])
        access_matrix = config.get("auth_schema", {}).get("access_matrix", {})
        removed = []
        for role_name in extra_roles:
            if role_name in access_matrix:
                del access_matrix[role_name]
                removed.append(role_name)
        if removed:
            return {"strategy": "remove_extra_matrix", "result": "fixed",
                    "detail": f"Removed orphaned matrix entries for: {removed}"}
        return {"strategy": "remove_extra_matrix", "result": "fixed",
                "detail": "No orphaned matrix entries to remove"}

    def _repair_type(self, error: dict, config: dict, arch: dict,
                      api_key: Optional[str] = None) -> dict:
        """Fix unrecognized type by coercing to a compatible type."""
        loc = error.get("location", "")
        detail = error.get("detail", "")
        # Parse location to find and fix the column with bad type
        parts = loc.split(".")
        if len(parts) >= 5 and parts[0] == "db_schema" and parts[1] == "tables":
            table_name = parts[2]
            col_name = parts[4]
            for table in config.get("db_schema", {}).get("tables", []):
                if table["name"] == table_name:
                    for col in table.get("columns", []):
                        if col["name"] == col_name:
                            col["type"] = "VARCHAR"
                            return {"strategy": "coerce_type", "result": "fixed",
                                    "detail": f"Coerced type of '{col_name}' to VARCHAR"}
        return {"strategy": "coerce_type", "result": "partial",
                "detail": f"Type issue: {detail}. Could not locate column to coerce."}

    def _repair_sql_type(self, error: dict, config: dict, arch: dict,
                          api_key: Optional[str] = None) -> dict:
        """Fix invalid SQL type by coercing to VARCHAR."""
        loc = error.get("location", "")
        # Parse location to find and fix the column
        parts = loc.split(".")
        if len(parts) >= 5:
            table_name = parts[2]
            col_name = parts[4]
            for table in config.get("db_schema", {}).get("tables", []):
                if table["name"] == table_name:
                    for col in table.get("columns", []):
                        if col["name"] == col_name:
                            col["type"] = "VARCHAR"
                            return {"strategy": "fix_sql_type", "result": "fixed",
                                    "detail": f"Changed type of '{col_name}' to VARCHAR"}
        return {"strategy": "fix_sql_type", "result": "partial", "detail": "Could not locate column"}

    def _repair_broken_fk(self, error: dict, config: dict, arch: dict,
                            api_key: Optional[str] = None) -> dict:
        """Remove broken foreign key reference."""
        loc = error.get("location", "")
        parts = loc.split(".")
        if len(parts) >= 5:
            table_name = parts[2]
            col_name = parts[4]
            for table in config.get("db_schema", {}).get("tables", []):
                if table["name"] == table_name:
                    for col in table.get("columns", []):
                        if col["name"] == col_name:
                            col.pop("foreign_key", None)
                            return {"strategy": "remove_broken_fk", "result": "fixed",
                                    "detail": f"Removed broken FK from '{table_name}.{col_name}'"}
        return {"strategy": "remove_broken_fk", "result": "unresolvable", "detail": "Could not locate FK"}

    def _repair_duplicate_endpoint(self, error: dict, config: dict, arch: dict,
                                     api_key: Optional[str] = None) -> dict:
        """Remove duplicate API endpoints, keeping only the first occurrence."""
        endpoints = config.get("api_schema", {}).get("endpoints", [])
        seen = set()
        deduped = []
        removed = 0
        for ep in endpoints:
            key = (ep.get("method", "").upper(), ep.get("path", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(ep)
            else:
                removed += 1
        if removed > 0:
            config["api_schema"]["endpoints"] = deduped
            return {"strategy": "deduplicate_endpoints", "result": "fixed",
                    "detail": f"Removed {removed} duplicate endpoint(s)"}
        return {"strategy": "deduplicate_endpoints", "result": "fixed",
                "detail": "No duplicates found"}

    def _repair_api_unknown_entity(self, error: dict, config: dict, arch: dict,
                                     api_key: Optional[str] = None) -> dict:
        """Fix entity reference — try fuzzy matching first, remove only as fallback."""
        ep_path = error.get("location", "").replace("api_schema.endpoints.", "")
        db_tables = [t["name"] for t in config.get("db_schema", {}).get("tables", []) if "name" in t]
        for ep in config.get("api_schema", {}).get("endpoints", []):
            if ep["path"] == ep_path and ep.get("entity"):
                # Try fuzzy matching against DB tables
                for table_name in db_tables:
                    if _entity_matches_table(ep["entity"], table_name):
                        original_entity = ep["entity"]
                        ep["entity"] = table_name
                        return {"strategy": "fix_entity_ref", "result": "fixed",
                                "detail": f"Matched entity '{original_entity}' to table '{table_name}'"}
                # No match found — remove as last resort
                ep.pop("entity", None)
                return {"strategy": "remove_entity_ref", "result": "fixed",
                        "detail": f"No matching table for '{ep_path}' — removed entity reference"}
        return {"strategy": "remove_entity_ref", "result": "unresolvable", "detail": "Could not find endpoint"}

    def _repair_missing_db_table(self, error: dict, config: dict, arch: dict,
                                   api_key: Optional[str] = None) -> dict:
        """Generate a new DB table for a resource that has API endpoints but no table."""
        missing_entity = error.get("missing_entity", "unknown")
        suggested_name = error.get("suggested_table_name", missing_entity)

        # Check if a matching table already exists (case-insensitive, singular/plural)
        existing_tables = [t.get("name", "") for t in config.get("db_schema", {}).get("tables", [])]
        sn = suggested_name.lower().strip()
        for et in existing_tables:
            et_lower = et.lower().strip()
            if sn == et_lower or sn == et_lower + 's' or sn + 's' == et_lower:
                return {"strategy": "skip_duplicate", "result": "fixed",
                        "detail": f"Table '{et}' already matches '{suggested_name}' (already exists)"}
            if sn.endswith('ies') and sn[:-3] + 'y' == et_lower:
                return {"strategy": "skip_duplicate", "result": "fixed",
                        "detail": f"Table '{et}' already matches '{suggested_name}'"}
            if et_lower.endswith('ies') and et_lower[:-3] + 'y' == sn:
                return {"strategy": "skip_duplicate", "result": "fixed",
                        "detail": f"Table '{et}' already matches '{suggested_name}'"}
        if sn in {t.lower() for t in existing_tables}:
            return {"strategy": "skip_duplicate", "result": "fixed",
                    "detail": f"Table '{suggested_name}' already exists (already repaired)"}

        # Find the Architecture IR entity that matches this missing table
        arch_entities = arch.get("entities", [])
        matching_entity = None
        for e in arch_entities:
            e_name = e.get("name", "").lower().replace(" ", "_").replace("-", "_")
            if e_name == suggested_name or e_name == missing_entity.lower():
                matching_entity = e
                break

        if not matching_entity:
            return {"strategy": "generate_missing_table", "result": "unresolvable",
                    "detail": f"No entity '{missing_entity}' found in Architecture IR to generate table from"}

        # Generate the table using LLM
        try:
            result = structured_call(
                system_prompt="Generate a database table definition for a single entity. Output must be a valid table object with name and columns array.",
                user_message=(
                    f"Generate a DB table for this entity:\n"
                    f"Entity name: {matching_entity.get('name', suggested_name)}\n"
                    f"Fields: {matching_entity.get('fields', [])}\n"
                    f"Suggested table name: {suggested_name}\n\n"
                    f"Output a table object: {{\"name\": \"{suggested_name}\", \"columns\": [...]}}\n"
                    f"Include id INTEGER PRIMARY KEY. Map types: string->VARCHAR(255), integer->INTEGER, boolean->BOOLEAN, datetime->TIMESTAMP."
                ),
                tool_name="output_table",
                tool_description="Output a single database table definition",
                input_schema={
                    "type": "object",
                    "required": ["name", "columns"],
                    "properties": {
                        "name": {"type": "string"},
                        "columns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["name", "type"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {"type": "string"},
                                    "primary_key": {"type": "boolean"},
                                    "nullable": {"type": "boolean"},
                                    "unique": {"type": "boolean"},
                                }
                            }
                        }
                    }
                },
                api_key=api_key,
            )
            config.setdefault("db_schema", {}).setdefault("tables", []).append(result)
            # Also generate relations for the new table from the entity definition
            if matching_entity.get("relations"):
                relations = config.setdefault("db_schema", {}).setdefault("relations", [])
                table_name = result.get("name", suggested_name)
                existing_relations = {(r.get("from_table"), r.get("to_table"), r.get("type")) for r in relations}
                for rel in matching_entity.get("relations", []):
                    rel_type = rel.get("type", "")
                    target = rel.get("target", "")
                    if not target:
                        continue
                    # Normalize target name to match DB table naming
                    target_table = target.lower().replace(" ", "_").replace("-", "_")
                    rel_key = (table_name, target_table, rel_type)
                    # Avoid duplicates
                    if rel_key not in existing_relations:
                        if rel_type in ("belongs_to", "has_one"):
                            relations.append({"type": rel_type, "from_table": table_name, "to_table": target_table,
                                              "foreign_key": f"{target_table}_id"})
                        elif rel_type == "has_many":
                            relations.append({"type": rel_type, "from_table": table_name, "to_table": target_table})
                        elif rel_type == "many_to_many":
                            jt = f"{table_name}_{target_table}"
                            relations.append({"type": rel_type, "from_table": table_name, "to_table": target_table,
                                              "junction_table": jt})
                        existing_relations.add(rel_key)
            return {"strategy": "generate_missing_table", "result": "fixed",
                    "detail": f"Generated missing table '{result.get('name', suggested_name)}' with {len(result.get('columns', []))} columns and relations"}
        except Exception as e:
            return {"strategy": "generate_missing_table", "result": "unresolvable", "detail": str(e)[:100]}

    def _repair_broken_ref(self, error: dict, config: dict, arch: dict,
                             api_key: Optional[str] = None) -> dict:
        """Fix broken reference by identifying and removing it."""
        loc = error.get("location", "")
        error_type = error.get("error_type", "")

        # Handle broken FK references
        if error_type == "broken_fk_reference":
            return self._repair_broken_fk(error, config, arch, api_key)

        # Handle unknown entity reference in API endpoints
        if error_type == "api_unknown_entity":
            return self._repair_api_unknown_entity(error, config, arch, api_key)

        # Handle unknown entity reference in business rules
        if error_type == "rule_unknown_entity":
            rule_name = error.get("location", "").replace("business_logic.rules.", "")
            for rule in config.get("business_logic", {}).get("rules", []):
                if rule["name"] == rule_name:
                    detail_parts = error.get("detail", "").split("'")
                    entity = detail_parts[1] if len(detail_parts) > 1 else ""
                    if entity and entity in rule.get("entities_involved", []):
                        rule["entities_involved"].remove(entity)
                        return {"strategy": "remove_broken_ref", "result": "fixed",
                                "detail": f"Removed broken entity ref '{entity}' from rule '{rule_name}'"}
            return {"strategy": "remove_broken_ref", "result": "unresolvable",
                    "detail": "Could not locate broken reference to remove"}

        return {"strategy": "remove_broken_ref", "result": "partial",
                "detail": "Generic broken reference — removed where possible."}
