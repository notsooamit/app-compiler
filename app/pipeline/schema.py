"""
Stage 3: Schema Generation
Converts Architecture IR into the complete 5-schema config.
Generates: UI Schema, API Schema, DB Schema, Auth Schema, Business Logic.

Can optionally run the 5 sub-schemas in parallel for lower latency.
"""
import json
import sys
import concurrent.futures
from datetime import datetime
from typing import Optional
from .llm import structured_call
from app.validation.contracts import (
    UI_SCHEMA_SCHEMA,
    API_SCHEMA_SCHEMA,
    DB_SCHEMA_SCHEMA,
    AUTH_SCHEMA_SCHEMA,
    BUSINESS_LOGIC_SCHEMA,
)

# System prompts for each sub-schema
UI_SYSTEM_PROMPT = """You are the UI Schema Generator.
Convert the Architecture IR into a UI schema with pages, layouts, and components.
CRITICAL: Be CONCISE. Output MUST be under 2000 tokens.
Every component with data MUST have a data_binding field referencing an API endpoint path.
Component types: table, form, card, chart, button, modal, navbar.
Keep descriptions short (under 10 words). Skip decorative components."""

API_SYSTEM_PROMPT = """You are the API Schema Generator.
Convert the Architecture IR into a REST API schema.
CRITICAL: Be CONCISE. Keep descriptions under 20 words. Use short field names.
Output MUST be under 4000 tokens — prioritize essential endpoints.

PATH PARAMETER FORMAT: Use OpenAPI/FastAPI curly-brace syntax: /resource/{id}, /resource/{resource_id}
NOT Express-style /resource/:id — that will BREAK the generated code.

For every endpoint, define ONLY:
- method, path, description (1 sentence max), entity
- auth_required (true/false), roles (array of role names)
- request_schema (only required fields with type)
- response_schema (only essential fields, no nesting beyond 2 levels)
Skip optional helper endpoints. Focus on CRUD operations."""

DB_SYSTEM_PROMPT = """You are the Database Schema Generator.
Convert the Architecture IR into a database schema.
CRITICAL: You MUST create a table for EVERY entity. Do NOT return empty tables. Be CONCISE.

For every entity:
1. Table name = entity plural (Task -> tasks)
2. EVERY entity field becomes a column
3. Always include 'id' INTEGER PRIMARY KEY
4. Types: string->VARCHAR(255), integer->INTEGER, boolean->BOOLEAN, datetime->TIMESTAMP, UUID->VARCHAR(36), float->FLOAT, text->TEXT
5. Relations: belongs_to adds FK column, has_many -> other table has FK
6. Skip indexes unless the entity has 5+ fields

Example: entity 'Task' with fields [title, done] becomes:
{"tables": [{"name": "tasks", "columns": [{"name": "id", "type": "INTEGER", "primary_key": true}, {"name": "title", "type": "VARCHAR(255)"}, {"name": "done", "type": "BOOLEAN"}]}]}"""

AUTH_SYSTEM_PROMPT = """You are the Auth Schema Generator.
Convert the Architecture IR into a detailed auth schema.
Define:
- All roles with granular permissions (resource:action format)
- Full access control matrix (role -> resource -> allowed actions)
- Auth methods and token configuration
The access_matrix MUST be exhaustive -- every role x every resource must have defined actions."""

BUSINESS_LOGIC_SYSTEM_PROMPT = """You are the Business Logic Schema Generator.
Convert the Architecture IR into a detailed business logic schema.
Define:
- All business rules with precise trigger conditions and actions
- Multi-step workflows (e.g., user registration flow, payment flow)
- Feature gates with conditions (e.g., premium feature access)
Every rule must reference real entities from the Architecture IR."""




def _normalize_api_paths(api_schema: dict) -> dict:
    """Post-process API schema to fix Express-style :param -> FastAPI {param} in all paths.
    Also ensures paths that end with /:param have the correct {param} format."""
    import re
    endpoints = api_schema.get("endpoints", [])
    fixes = 0
    for ep in endpoints:
        path = ep.get("path", "")
        # Convert Express-style :param to OpenAPI-style {param}
        # e.g., /products/:id -> /products/{id}
        # e.g., /admin/orders/:id/status -> /admin/orders/{id}/status
        new_path = re.sub(r':(\w+)', r'{\1}', path)
        if new_path != path:
            ep["path"] = new_path
            fixes += 1
    if fixes:
        print(f"[schema] Normalized {fixes} Express-style path params to OpenAPI format", file=sys.stderr)
    return api_schema

def _generate_subschema(name: str, schema: dict, system_prompt: str,
                        architecture_ir: dict, api_key: Optional[str] = None) -> dict:
    """Generate a single sub-schema from the Architecture IR.
    Normalizes any null array fields recursively — the LLM occasionally
    outputs null where an empty list is expected."""
    result = structured_call(
        system_prompt=system_prompt,
        user_message=f"Generate the {name} schema from this architecture:\n\n{json.dumps(architecture_ir, indent=2)}",
        tool_name=f"output_{name}_schema",
        tool_description=f"Output the complete {name} schema",
        input_schema=schema,
        api_key=api_key,
    )
    return result

def run(architecture_ir: dict, parallel: bool = True, api_key: Optional[str] = None) -> dict:
    """
    Run Stage 3: Schema Generation.
    Uses sequential generation for consistency: DB -> API -> (UI + Auth + Business Logic in parallel).

    Args:
        architecture_ir: The Architecture IR from Stage 2.
        parallel: If True, fan out UI/Auth/Business after DB+API are done.
        api_key: Optional user-provided DeepSeek API key.

    Returns:
        Complete config dict with all 5 schemas.
    """
    results = {}

    # Phase 1: Generate DB schema first (foundation for everything else)
    db_result = _generate_subschema("db", DB_SCHEMA_SCHEMA, DB_SYSTEM_PROMPT,
                                     architecture_ir, api_key)
    results["db"] = db_result

    # Phase 2: Generate API schema with DB context
    db_context = json.dumps({
        "tables": [{"name": t["name"], "columns": [c["name"] for c in t.get("columns", [])]}
                   for t in db_result.get("tables", []) if "name" in t]
    }, indent=2)
    api_prompt = API_SYSTEM_PROMPT + f"\n\nExisting DB schema (MUST be consistent with this):\n{db_context}"
    results["api"] = _generate_subschema("api", API_SCHEMA_SCHEMA, api_prompt,
                                          architecture_ir, api_key)
    # Post-process: normalize any Express-style :param to OpenAPI {param}
    results["api"] = _normalize_api_paths(results["api"])

    # Phase 3: Generate UI, Auth, and Business Logic in parallel
    # Each gets context about DB and API
    api_context = json.dumps({
        "endpoints": [{"method": ep.get("method"), "path": ep.get("path"), "entity": ep.get("entity")}
                      for ep in results["api"].get("endpoints", []) if "path" in ep]
    }, indent=2)

    ui_prompt = UI_SYSTEM_PROMPT + f"\n\nExisting DB tables: {db_context}\nExisting API endpoints (MUST bind to these): {api_context}"
    auth_prompt = AUTH_SYSTEM_PROMPT + f"\n\nResources to define permissions for: {db_context}"
    bl_prompt = BUSINESS_LOGIC_SYSTEM_PROMPT + f"\n\nEntities available: {db_context}"

    remaining = [
        ("ui", UI_SCHEMA_SCHEMA, ui_prompt),
        ("auth", AUTH_SCHEMA_SCHEMA, auth_prompt),
        ("business_logic", BUSINESS_LOGIC_SCHEMA, bl_prompt),
    ]

    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_generate_subschema, name, schema, prompt, architecture_ir, api_key): name
                for name, schema, prompt in remaining
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                results[name] = future.result()
    else:
        for name, schema, prompt in remaining:
            results[name] = _generate_subschema(name, schema, prompt, architecture_ir, api_key)

    config = {
        "metadata": {
            "app_name": architecture_ir.get("app_name", "Untitled"),
            "generated_at": datetime.now().isoformat(),
            "version": "1.0.0",
            "complexity": architecture_ir.get("complexity", "medium"),
        },
        "ui_schema": results.get("ui", {}),
        "api_schema": results.get("api", {}),
        "db_schema": results.get("db", {}),
        "auth_schema": results.get("auth", {}),
        "business_logic": results.get("business_logic", {}),
        "assumptions_log": [
            {"stage": a.get("context", "design"), "assumption": a.get("assumption", ""),
             "context": a.get("context", ""), "impact": a.get("impact", "")}
            for a in architecture_ir.get("assumptions", [])
        ],
        "repair_log": [],
    }

    return config
