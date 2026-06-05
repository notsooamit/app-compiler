"""
Multi-layer validator for the complete config.
Checks: JSON validity, required fields, types, references, cross-layer consistency,
logical consistency, and hallucination detection.

Returns a list of error dicts, each with:
- layer: which validation layer caught it
- message: human-readable description
- location: where in the config the error is (JSON path)
- severity: "error" or "warning"
- detail: extra context for the repair engine
- error_type: machine-readable error category for repair dispatch
"""
import json
from typing import List, Dict, Any
from jsonschema import Draft7Validator, ValidationError as JsonSchemaError
from .contracts import COMPLETE_CONFIG_SCHEMA
from .consistency import check_cross_layer_consistency, _camel_to_snake
from .hallucination import check_hallucinations


def validate_config(config: dict, architecture_ir: dict = None) -> List[Dict[str, Any]]:
    """
    Run all 7 validation layers on the config.

    Args:
        config: The complete config from Stage 3.
        architecture_ir: The Architecture IR (needed for hallucination detection).

    Returns:
        List of error dicts. Empty list means config is clean.
    """
    errors = []

    # Layer 1: JSON Validity
    errors.extend(_validate_json(config))

    # Layer 2+3: Required Fields + Type Safety (all at once via iter_errors)
    errors.extend(_validate_schema(config))
    errors.extend(_validate_types(config))

    # Layer 4: Reference Integrity
    errors.extend(_validate_references(config))

    # Layer 5: Cross-Layer Consistency
    errors.extend(check_cross_layer_consistency(config))

    # Layer 6: Logical Consistency
    errors.extend(_validate_logic(config))

    # Layer 7: Hallucination Detection
    if architecture_ir:
        errors.extend(check_hallucinations(config, architecture_ir))

    return errors


def _validate_json(config: dict) -> List[Dict]:
    """Layer 1: Basic JSON validity."""
    errors = []
    try:
        json.dumps(config)
    except (TypeError, ValueError) as e:
        errors.append({
            "layer": "json_validity",
            "message": f"Config is not JSON-serializable: {str(e)}",
            "location": "root",
            "severity": "error",
            "detail": str(e),
        })
    return errors


def _validate_schema(config: dict) -> List[Dict]:
    """Layer 2+3: Validate against JSON Schema, collecting ALL errors at once."""
    errors = []
    validator = Draft7Validator(COMPLETE_CONFIG_SCHEMA)
    for e in validator.iter_errors(config):
        location = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
        errors.append({
            "layer": "required_fields",
            "message": e.message,
            "location": location,
            "severity": "error",
            "detail": f"Schema error: {e.message}",
        })
    return errors


def _validate_types(config: dict) -> List[Dict]:
    """Layer 3 extra: Type safety for DB columns and HTTP methods."""
    errors = []

    db_tables = config.get("db_schema", {}).get("tables", [])
    for table in db_tables:
        for col in table.get("columns", []):
            col_type_raw = col.get("type", "").upper()
            # Strip parenthetical length/params: VARCHAR(255) -> VARCHAR, DECIMAL(10,2) -> DECIMAL
            col_type = col_type_raw.split("(")[0].strip() if "(" in col_type_raw else col_type_raw
            valid_types = {"INTEGER", "VARCHAR", "TEXT", "BOOLEAN", "TIMESTAMP",
                          "FLOAT", "DOUBLE", "DATE", "UUID", "JSON", "BLOB", "SERIAL", "BIGINT",
                          "DECIMAL", "NUMERIC", "CHAR", "ENUM"}
            if col_type and col_type not in valid_types:
                errors.append({
                    "layer": "type_safety",
                    "message": f"Column '{col['name']}' in table '{table['name']}' has unrecognized type '{col_type}'",
                    "location": f"db_schema.tables.{table['name']}.columns.{col['name']}",
                    "severity": "warning",
                    "detail": f"Unrecognized SQL type: {col_type}",
                    "error_type": "invalid_sql_type",
                })

    api_endpoints = config.get("api_schema", {}).get("endpoints", [])
    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    for ep in api_endpoints:
        if ep.get("method", "").upper() not in valid_methods:
            errors.append({
                "layer": "type_safety",
                "message": f"Endpoint '{ep.get('path')}' has invalid HTTP method '{ep.get('method')}'",
                "location": f"api_schema.endpoints.{ep.get('path')}",
                "severity": "error",
                "detail": f"Invalid method: {ep.get('method')}",
                "error_type": "invalid_http_method",
            })

    return errors


def _validate_references(config: dict) -> List[Dict]:
    """Layer 4: All internal references must resolve to real entities."""
    errors = []

    db_table_names = {t["name"] for t in config.get("db_schema", {}).get("tables", []) if "name" in t}
    entity_names = set(db_table_names)

    # Check FK references
    for table in config.get("db_schema", {}).get("tables", []):
        for col in table.get("columns", []):
            fk = col.get("foreign_key")
            if fk:
                ref_table = fk.get("table")
                if ref_table and ref_table not in db_table_names:
                    errors.append({
                        "layer": "reference_integrity",
                        "message": f"FK in '{table['name']}.{col['name']}' references non-existent table '{ref_table}'",
                        "location": f"db_schema.tables.{table['name']}.columns.{col['name']}",
                        "severity": "error",
                        "detail": f"Missing referenced table: {ref_table}",
                        "error_type": "broken_fk_reference",
                    })

    # Check API entity references (case-insensitive, handles singular/plural, PascalCase->snake_case)
    for ep in config.get("api_schema", {}).get("endpoints", []):
        entity = ep.get("entity")
        if entity:
            # Normalize for comparison: lowercase, try singular forms, try CamelCase->snake_case
            entity_candidates = {
                entity.lower().strip(),
                _camel_to_snake(entity).lower().strip(),
            }
            found = False
            for table_name in entity_names:
                table_norm = table_name.lower().strip()
                for entity_norm in entity_candidates:
                    if entity_norm == table_norm:
                        found = True; break
                    # Handle plural: tasks <-> task, categories <-> category, expenses <-> expense
                    if entity_norm == table_norm + 's' or entity_norm + 's' == table_norm:
                        found = True; break
                    if entity_norm.endswith('ies') and entity_norm[:-3] + 'y' == table_norm:
                        found = True; break
                    if table_norm.endswith('ies') and table_norm[:-3] + 'y' == entity_norm:
                        found = True; break
                    if entity_norm.endswith('es') and entity_norm[:-2] == table_norm:
                        found = True; break
                    if table_norm.endswith('es') and table_norm[:-2] == entity_norm:
                        found = True; break
                if found:
                    break
            if not found:
                errors.append({
                    "layer": "reference_integrity",
                    "message": f"API endpoint '{ep['path']}' references unknown entity '{entity}'",
                    "location": f"api_schema.endpoints.{ep['path']}",
                    "severity": "error",
                    "detail": f"Entity '{entity}' not found in DB schema",
                    "error_type": "api_unknown_entity",
                })

    # Check for Express-style :param in paths (FastAPI requires {param})
    import re as _re
    for ep in config.get("api_schema", {}).get("endpoints", []):
        path = ep.get("path", "")
        express_params = _re.findall(r':(\w+)', path)
        if express_params:
            errors.append({
                "layer": "reference_integrity",
                "message": f"Endpoint '{path}' uses Express-style :param (/{':'.join(express_params)}). Use OpenAPI/FastAPI format: /{{{express_params[0]}}}",
                "location": f"api_schema.endpoints.{path}",
                "severity": "warning",
                "detail": f"Express-style path params detected: {express_params}. The post-processor will auto-fix, but the API prompt may need tuning.",
                "error_type": "express_style_params",
            })

    # Check for duplicate API endpoints (same method + path)
    seen_endpoints = {}
    for i, ep in enumerate(config.get("api_schema", {}).get("endpoints", [])):
        key = (ep.get("method", "").upper(), ep.get("path", ""))
        if key in seen_endpoints:
            errors.append({
                "layer": "reference_integrity",
                "message": f"Duplicate endpoint: {key[0]} {key[1]} (first at index {seen_endpoints[key]}, duplicate at index {i})",
                "location": f"api_schema.endpoints[{i}]",
                "severity": "error",
                "detail": f"Same method+path appears multiple times. Remove duplicates.",
                "error_type": "duplicate_endpoint",
            })
        else:
            seen_endpoints[key] = i

    return errors


def _validate_logic(config: dict) -> List[Dict]:
    """Layer 6: Logical consistency -- no circular deps, valid conditions, etc."""
    errors = []

    # Check for circular FK references (DFS with 3-color marking)
    fk_graph = {}
    for table in config.get("db_schema", {}).get("tables", []):
        table_name = table["name"]
        fk_graph[table_name] = []
        for col in table.get("columns", []):
            fk = col.get("foreign_key")
            if fk and fk.get("table"):
                fk_graph[table_name].append(fk["table"])

    # DFS cycle detection: WHITE=unvisited, GRAY=in current path, BLACK=done
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in fk_graph}
    cycle_path = []

    def dfs_cycle(node, path):
        color[node] = GRAY
        path.append(node)
        for neighbor in fk_graph.get(node, []):
            if neighbor not in color:
                continue  # External reference (table doesn't exist)
            if color[neighbor] == GRAY:
                # Found cycle -- record just the cycle portion
                cycle_start = path.index(neighbor) if neighbor in path else 0
                cycle_path.extend(path[cycle_start:] + [neighbor])
                return True
            if color[neighbor] == WHITE:
                if dfs_cycle(neighbor, path):
                    return True
        path.pop()
        color[node] = BLACK
        return False

    for node in fk_graph:
        if color[node] == WHITE:
            if dfs_cycle(node, []):
                cycle_str = " -> ".join(cycle_path[:8])  # Limit to 8 nodes
                if len(cycle_path) > 8:
                    cycle_str += " -> ..."
                errors.append({
                    "layer": "logical_consistency",
                    "message": f"Circular FK reference detected: {cycle_str}",
                    "location": f"db_schema.tables.{cycle_path[0] if cycle_path else 'unknown'}",
                    "severity": "warning",
                    "detail": f"Cycle of length {len(set(cycle_path))} detected in FK graph",
                    "error_type": "circular_fk",
                })
                break  # One cycle is enough to flag

    # Check that at least one role exists
    roles = config.get("auth_schema", {}).get("roles", [])
    if not roles:
        errors.append({
            "layer": "logical_consistency",
            "message": "No auth roles defined -- app will have no access control",
            "location": "auth_schema.roles",
            "severity": "warning",
            "detail": "No roles defined",
            "error_type": "no_roles_defined",
        })

    # Check that access_matrix covers all roles (defined roles MUST be in matrix)
    access_matrix = config.get("auth_schema", {}).get("access_matrix", {})
    role_names = {r["name"] for r in roles if "name" in r}
    matrix_roles = set(access_matrix.keys())
    missing_from_matrix = role_names - matrix_roles
    if missing_from_matrix:
        errors.append({
            "layer": "logical_consistency",
            "message": f"Roles {missing_from_matrix} are defined but missing from access_matrix",
            "location": "auth_schema.access_matrix",
            "severity": "error",
            "detail": f"Missing matrix entries for: {missing_from_matrix}",
            "error_type": "auth_missing_matrix",
            "missing_roles": list(missing_from_matrix),
        })

    # Reverse check: entries in access_matrix MUST have corresponding roles defined
    extra_in_matrix = matrix_roles - role_names
    if extra_in_matrix:
        errors.append({
            "layer": "logical_consistency",
            "message": f"Roles {extra_in_matrix} are in access_matrix but not defined in auth_schema.roles",
            "location": "auth_schema.access_matrix",
            "severity": "error",
            "detail": f"Access matrix entries without role definitions: {extra_in_matrix}",
            "error_type": "auth_extra_matrix_entries",
            "extra_roles": list(extra_in_matrix),
        })

    return errors
