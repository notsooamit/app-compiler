"""
Cross-layer consistency checker (Layer 5).
Validates: API<->DB field matching, UI<->API data bindings, Auth<->API role coverage,
and crucially: dangling API resources with no DB table (the line_items problem).
"""
import re
from typing import List, Dict, Any, Set


def _camel_to_snake(name: str) -> str:
    """Convert PascalCase/camelCase to snake_case: CartItem -> cart_item."""
    # Insert underscore before uppercase letters preceded by lowercase
    s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    return s1.lower()


# Common path segments that are NOT database tables (actions, views, utilities)
NON_TABLE_PATH_SEGMENTS = frozenset({
    'auth', 'login', 'logout', 'register', 'dashboard', 'search',
    'me', 'profile', 'stats', 'analytics', 'export', 'import', 'health',
    'pdf', 'csv', 'report', 'summary', 'settings', 'config', 'webhook',
    'callback', 'verify', 'reset-password', 'forgot-password', 'upload',
    'download', 'preview', 'publish', 'archive', 'restore', 'bulk',
    'unpaid-revenue', 'revenue', 'metrics', 'overview', 'feed', 'timeline',
    'notifications', 'check-in', 'checkout', 'scan', 'verify-email',
})


def check_cross_layer_consistency(config: dict) -> List[Dict[str, Any]]:
    """
    Check consistency across all 5 schema layers.

    Rules:
    0. Dangling resources: every API endpoint entity must have a DB table
    1. Every API response field must exist in the corresponding DB table
    2. Every UI data_binding must reference a real API endpoint
    3. Every API endpoint with auth_required must have role coverage
    4. Every business rule feature gate must reference real features
    5. Every DB table should have at least one API endpoint (orphan check)
    """
    errors = []

    # Rule 0: Dangling API resources (MOST IMPORTANT — catches missing tables)
    errors.extend(_check_dangling_resources(config))

    # Rule 1: API response fields <-> DB columns
    errors.extend(_check_api_db_consistency(config))

    # Rule 2: UI data bindings <-> API endpoints
    errors.extend(_check_ui_api_consistency(config))

    # Rule 3: Auth coverage for protected endpoints
    errors.extend(_check_auth_api_consistency(config))

    # Rule 4: Business logic references real features
    errors.extend(_check_business_logic_consistency(config))

    # Rule 5: Orphan DB tables
    errors.extend(_check_orphan_tables(config))

    return errors


def _extract_resource_names_from_path(path: str) -> Set[str]:
    """Extract resource nouns from an API path. e.g., /api/invoices/:id/line-items -> {'invoices', 'line-items'}"""
    # Remove query params and path params
    clean = re.sub(r'\?.*', '', path)
    clean = re.sub(r'\{[^}]+\}', '', clean)
    clean = re.sub(r':\w+', '', clean)
    # Filter out path params, numbers (IDs), UUIDs, and common prefix segments
    filtered = []
    for p in [p.strip() for p in clean.split('/') if p.strip() and p.strip() not in ('api', 'v1', 'v2')]:
        if p.isdigit() or p.replace('-', '').isdigit():
            continue  # Skip numeric IDs like '123' or '123-456'
        filtered.append(p)
    return set(filtered)


def _normalize_table_name(name: str) -> str:
    """Normalize resource names for comparison: line-items -> line_items, invoices -> invoices."""
    return name.lower().replace('-', '_').replace(' ', '_')


def _entity_matches_any_table(entity: str, table_names: set) -> bool:
    """Check if an entity name matches any table name (case-insensitive, handles plurals,
    AND PascalCase -> snake_case conversion for entities like CartItem -> cart_items)."""
    e = entity.lower().strip().replace('-', '_').replace(' ', '_')
    # Also try CamelCase -> snake_case: "CartItem" -> "cart_item"
    e_camel = _camel_to_snake(entity).replace('-', '_').replace(' ', '_')
    candidates = {e, e_camel}
    for t in table_names:
        t_norm = t.lower().strip().replace('-', '_').replace(' ', '_')
        for candidate in candidates:
            if candidate == t_norm:
                return True
            if candidate == t_norm + 's' or candidate + 's' == t_norm:
                return True
            if candidate.endswith('ies') and candidate[:-3] + 'y' == t_norm:
                return True
            if t_norm.endswith('ies') and t_norm[:-3] + 'y' == candidate:
                return True
            if candidate.endswith('es') and candidate[:-2] == t_norm:
                return True
            if t_norm.endswith('es') and t_norm[:-2] == candidate:
                return True
    return False


def _check_dangling_resources(config: dict) -> List[Dict]:
    """
    Rule 0: Every API endpoint entity and path resource MUST have a corresponding DB table.
    This catches the critical bug: API references 'line_items' but DB has no 'line_items' table.
    """
    errors = []

    db_tables = {_normalize_table_name(t["name"]) for t in config.get("db_schema", {}).get("tables", []) if "name" in t}

    # Also collect table names from DB relations
    for rel in config.get("db_schema", {}).get("relations", []):
        for key in ("from_table", "to_table", "junction_table"):
            if key in rel:
                db_tables.add(_normalize_table_name(rel[key]))

    for ep in config.get("api_schema", {}).get("endpoints", []):
        endpoint_entity = ep.get("entity")
        endpoint_path = ep.get("path", "")

        # Check explicit entity reference (with singular/plural normalization)
        if endpoint_entity:
            entity_normalized = _normalize_table_name(endpoint_entity)
            if not _entity_matches_any_table(endpoint_entity, db_tables):
                errors.append({
                    "layer": "cross_layer",
                    "message": f"API endpoint '{endpoint_path}' references entity '{endpoint_entity}' but no DB table '{entity_normalized}' exists",
                    "location": f"api_schema.endpoints.{endpoint_path}",
                    "severity": "error",
                    "detail": f"Dangling resource: entity '{endpoint_entity}' has no DB table. Existing tables: {sorted(db_tables)}",
                    "error_type": "missing_db_table",
                    "api_path": endpoint_path,
                    "missing_entity": endpoint_entity,
                    "suggested_table_name": entity_normalized,
                })

        # Check all resource names in the path
        path_resources = _extract_resource_names_from_path(endpoint_path)

        # For non-GET endpoints, the last path segment may be an action (verb), not a resource
        endpoint_method = ep.get("method", "").upper()
        path_parts = [p.strip() for p in endpoint_path.split('/') if p.strip() and p.strip() not in ('api', 'v1', 'v2')]
        action_verbs = {'pay', 'refund', 'cancel', 'upgrade', 'downgrade', 'enroll', 'unenroll',
                       'approve', 'reject', 'publish', 'archive', 'restore', 'verify', 'reset',
                       'check-in', 'checkout', 'scan', 'upload', 'download', 'preview',
                       'follow', 'unfollow', 'like', 'unlike', 'share', 'rate', 'review',
                       'assign', 'unassign', 'complete', 'reopen', 'lock', 'unlock',
                       'activate', 'deactivate', 'suspend', 'resume', 'retry', 'execute'}
        if endpoint_method != "GET" and path_parts:
            last_segment = path_parts[-1].lower()
            # Check if last segment is an action verb or looks like one
            if last_segment in action_verbs or last_segment.startswith(('do_', 'perform_')):
                # Skip the action segment from resource checking
                path_resources = {r for r in path_resources if r.lower() != last_segment}

        for resource in path_resources:
            res_normalized = _normalize_table_name(resource)
            # Skip common non-table path segments (actions, views, utilities)
            if res_normalized in NON_TABLE_PATH_SEGMENTS:
                continue
            # Skip if it's the same as the entity we already checked
            if endpoint_entity and res_normalized == _normalize_table_name(endpoint_entity):
                continue
            # Skip if a DB table name matches (prefix or full match at underscore boundary)
            if any(
                t == res_normalized
                or res_normalized.startswith(t + '_')
                or t.startswith(res_normalized + '_')
                for t in db_tables
            ):
                continue
            # Check if this path segment has a DB table
            if res_normalized not in db_tables:
                errors.append({
                    "layer": "cross_layer",
                    "message": f"API path '{endpoint_path}' contains resource '{resource}' but no DB table '{res_normalized}' exists",
                    "location": f"api_schema.endpoints.{endpoint_path}",
                    "severity": "error",
                    "detail": f"Dangling resource in path: '{resource}' has no DB table. Existing tables: {sorted(db_tables)}",
                    "error_type": "missing_db_table",
                    "api_path": endpoint_path,
                    "missing_entity": resource,
                    "suggested_table_name": res_normalized,
                })

    return errors


def _normalize_entity_name(name: str) -> str:
    """Normalize entity/table names: lowercase, strip hyphens/underscores.
    Returns a single normalized string. For singular/plural comparison,
    use _entity_matches_table() or _entity_matches_any_table()."""
    n = name.lower().strip().replace('-', '_')
    return n


def _entity_matches_table(entity: str, table_name: str) -> bool:
    """Check if an API entity name matches a DB table name, handling singular/plural
    AND PascalCase -> snake_case conversion (CartItem -> cart_items)."""
    e = _normalize_entity_name(entity)
    e_camel = _camel_to_snake(entity).replace('-', '_')
    t = _normalize_entity_name(table_name)
    for candidate in {e, e_camel}:
        if candidate == t:
            return True
        # Try singular/plural variations
        if candidate == t + 's' or candidate + 's' == t:
            return True
        if candidate.endswith('ies') and candidate[:-3] + 'y' == t:
            return True  # categories <-> category
        if t.endswith('ies') and t[:-3] + 'y' == candidate:
            return True
        if candidate.endswith('es') and candidate[:-2] == t:
            return True  # boxes <-> box
        if t.endswith('es') and t[:-2] == candidate:
            return True  # box <-> boxes (reverse)
    return False


def _check_api_db_consistency(config: dict) -> List[Dict]:
    """API response fields must exist in DB columns. Uses normalized name matching."""
    errors = []

    db_columns_by_table = {}
    db_tables = config.get("db_schema", {}).get("tables", [])
    for table in db_tables:
        if "name" not in table:
            continue
        db_columns_by_table[table["name"]] = {c["name"] for c in table.get("columns", []) if "name" in c}

    for ep in config.get("api_schema", {}).get("endpoints", []):
        entity = ep.get("entity")
        if not entity:
            continue

        # Find matching DB table using normalized comparison
        matched_table = None
        for table_name in db_columns_by_table:
            if _entity_matches_table(entity, table_name):
                matched_table = table_name
                break

        if not matched_table:
            continue  # Let _check_dangling_resources catch missing tables

        db_cols = db_columns_by_table[matched_table]
        response_schema = ep.get("response_schema", {})

        for field_name in _extract_fields(response_schema):
            if field_name in ("id", "created_at", "updated_at", "deleted_at", "status", "error", "message"):
                continue
            if field_name not in db_cols:
                errors.append({
                    "layer": "cross_layer",
                    "message": f"API response field '{field_name}' in endpoint '{ep['path']}' not found in DB table '{entity}'",
                    "location": f"api_schema.endpoints.{ep['path']}.response_schema",
                    "severity": "error",
                    "detail": f"Field '{field_name}' missing from DB table '{entity}'. Columns: {db_cols}",
                    "error_type": "api_field_not_in_db",
                    "api_path": ep["path"],
                    "field": field_name,
                    "entity": entity,
                })

    return errors


def _check_ui_api_consistency(config: dict) -> List[Dict]:
    """UI data bindings must reference real API endpoints."""
    errors = []

    api_paths = {ep["path"] for ep in config.get("api_schema", {}).get("endpoints", [])}
    # Also build a set with /api prefix stripped for fuzzy matching
    api_paths_no_prefix = {p.replace("/api/", "/", 1) if p.startswith("/api/") else p for p in api_paths}
    api_paths_with_prefix = {"/api" + p if not p.startswith("/api") else p for p in api_paths}

    for page in config.get("ui_schema", {}).get("pages", []):
        for section in page.get("layout", {}).get("sections", []):
            for component in section.get("components", []):
                data_binding = component.get("data_binding")
                if data_binding:
                    # Strip HTTP method prefix: "POST /api/expenses" -> "/api/expenses"
                    binding_raw = data_binding.strip()
                    if ' ' in binding_raw:
                        parts = binding_raw.split(' ', 1)
                        if parts[0].upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                            binding_raw = parts[1]
                    # Strip query string and path params
                    binding_path = binding_raw.split("?")[0].split("{")[0].rstrip("/")
                    if not binding_path:
                        continue

                    # Try exact match
                    if binding_path in api_paths:
                        continue
                    # Try without /api prefix
                    binding_no_prefix = binding_path.replace("/api/", "/", 1) if binding_path.startswith("/api/") else binding_path
                    if binding_no_prefix in api_paths_no_prefix:
                        continue
                    # Try with /api prefix
                    binding_with_prefix = "/api" + binding_path if not binding_path.startswith("/api") else binding_path
                    if binding_with_prefix in api_paths_with_prefix:
                        continue
                    # Try prefix matching
                    all_paths = api_paths | api_paths_no_prefix | api_paths_with_prefix
                    matching = [p for p in all_paths if p.startswith(binding_no_prefix) or binding_no_prefix.startswith(p)]
                    if matching:
                        continue

                    errors.append({
                                "layer": "cross_layer",
                                "message": f"UI data binding '{data_binding}' in page '{page.get('name', 'unknown')}' has no matching API endpoint",
                                "location": f"ui_schema.pages.{page.get('name', 'unknown')}",
                                "severity": "error",
                                "detail": f"No API endpoint found for: {data_binding}",
                                "error_type": "ui_binding_no_api",
                                "page": page.get("name", "unknown"),
                                "component": component.get("type"),
                                "data_binding": data_binding,
                            })

    return errors


def _check_auth_api_consistency(config: dict) -> List[Dict]:
    """Every protected API endpoint must have role coverage defined."""
    errors = []

    role_names = {r["name"] for r in config.get("auth_schema", {}).get("roles", []) if "name" in r}

    for ep in config.get("api_schema", {}).get("endpoints", []):
        if ep.get("auth_required"):
            ep_roles = set(ep.get("roles", []))
            if not ep_roles:
                errors.append({
                    "layer": "cross_layer",
                    "message": f"Protected endpoint '{ep['path']}' has no roles specified",
                    "location": f"api_schema.endpoints.{ep['path']}",
                    "severity": "warning",
                    "detail": "Endpoint requires auth but no roles are assigned",
                    "error_type": "auth_no_roles",
                    "api_path": ep["path"],
                })
            unknown_roles = ep_roles - role_names
            if unknown_roles:
                errors.append({
                    "layer": "cross_layer",
                    "message": f"Endpoint '{ep['path']}' references unknown roles: {unknown_roles}",
                    "location": f"api_schema.endpoints.{ep['path']}",
                    "severity": "error",
                    "detail": f"Roles {unknown_roles} not defined in auth schema",
                    "error_type": "auth_unknown_roles",
                    "api_path": ep["path"],
                    "unknown_roles": list(unknown_roles),
                })

    return errors


def _check_business_logic_consistency(config: dict) -> List[Dict]:
    """Business rules must reference real entities and features."""
    errors = []

    entity_names = {t["name"] for t in config.get("db_schema", {}).get("tables", []) if "name" in t}
    feature_names = {f["feature"] for f in config.get("business_logic", {}).get("feature_gates", []) if "feature" in f}

    for rule in config.get("business_logic", {}).get("rules", []):
        for entity in rule.get("entities_involved", []):
            # Use normalized matching: "User" should match "users", "Expense" matches "expenses"
            if not _entity_matches_any_table(entity, entity_names):
                errors.append({
                    "layer": "cross_layer",
                    "message": f"Business rule '{rule['name']}' references unknown entity '{entity}'",
                    "location": f"business_logic.rules.{rule['name']}",
                    "severity": "warning",
                    "detail": f"Entity '{entity}' not found in DB schema",
                    "error_type": "rule_unknown_entity",
                })

    return errors


def _check_orphan_tables(config: dict) -> List[Dict]:
    """DB tables with no API endpoint referencing them are orphaned."""
    errors = []

    table_names = {t["name"] for t in config.get("db_schema", {}).get("tables", []) if "name" in t}
    api_entities_raw = {ep.get("entity") for ep in config.get("api_schema", {}).get("endpoints", []) if ep.get("entity")}

    # Match entities to tables with full normalization (includes PascalCase->snake_case)
    orphans = set(table_names)
    for entity in api_entities_raw:
        for table_name in list(orphans):
            if _entity_matches_table(entity, table_name):
                orphans.discard(table_name)

    for orphan in orphans:
        if orphan.startswith("_") or "junction" in orphan.lower() or orphan in ("users", "sessions"):
            continue
        errors.append({
            "layer": "cross_layer",
            "message": f"DB table '{orphan}' has no API endpoint -- orphaned table",
            "location": f"db_schema.tables.{orphan}",
            "severity": "warning",
            "detail": f"Table '{orphan}' is not exposed via any API endpoint",
            "error_type": "orphan_table",
        })

    return errors


def _extract_fields(schema: dict, prefix: str = "") -> list:
    """Recursively extract field names from a JSON schema-like dict."""
    fields = []
    if not isinstance(schema, dict):
        return fields

    # Fall back to schema itself if no "properties" wrapper (LLM often outputs flat dicts)
    props = schema.get("properties")
    if props is None and any(isinstance(v, str) for v in schema.values()):
        props = {k: v for k, v in schema.items() if isinstance(v, str)}
    if props is None:
        props = {}
    for key in props:
        fields.append(key)
        if isinstance(props[key], dict) and "properties" in props[key]:
            fields.extend(_extract_fields(props[key], f"{prefix}{key}."))

    return fields
