"""
Unit tests for the App Compiler validation, repair, code generation, and schema modules.
Covers: entity matching, FK derivation, path normalization, cosmetic types,
validation layers, repair strategies, code generation, and schema contracts.
"""
import pytest
import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.validation.consistency import (
    _camel_to_snake,
    _entity_matches_table,
    _entity_matches_any_table,
    _normalize_table_name,
    _extract_resource_names_from_path,
    _check_dangling_resources,
    _check_orphan_tables,
    _check_api_db_consistency,
    check_cross_layer_consistency,
)
from app.validation.validator import validate_config, _validate_references, _validate_logic
from app.validation.repair import RepairEngine
from app.pipeline.refinement import COSMETIC_TYPES, _error_key
from app.pipeline.schema import _normalize_api_paths
from app.generation.codegen import (
    _derive_fk_from_relations,
    _camel_to_snake_codegen,
    _match_entity_to_table,
    _singularize,
    _table_name_to_class_name,
    _map_sql_type_to_python,
    _map_sqlalchemy_type,
)
from app.validation.contracts import (
    INTENT_IR_SCHEMA,
    ARCHITECTURE_IR_SCHEMA,
    COMPLETE_CONFIG_SCHEMA,
    UI_SCHEMA_SCHEMA,
    API_SCHEMA_SCHEMA,
    DB_SCHEMA_SCHEMA,
    AUTH_SCHEMA_SCHEMA,
    BUSINESS_LOGIC_SCHEMA,
)


# ================================================================
# Entity Name Resolution Tests
# ================================================================

class TestCamelToSnake:
    def test_basic_pascal_case(self):
        assert _camel_to_snake("CartItem") == "cart_item"
        assert _camel_to_snake("OrderItem") == "order_item"
        assert _camel_to_snake("Product") == "product"

    def test_multi_word_pascal(self):
        assert _camel_to_snake("OrderLineItem") == "order_line_item"
        assert _camel_to_snake("UserProfile") == "user_profile"

    def test_already_snake_case(self):
        assert _camel_to_snake("cart_items") == "cart_items"
        assert _camel_to_snake("order_id") == "order_id"

    def test_single_word(self):
        assert _camel_to_snake("Task") == "task"
        assert _camel_to_snake("User") == "user"

    def test_acronyms(self):
        assert _camel_to_snake("APIKey") == "apikey"  # CamelCase treats adjacent caps as one word
        assert _camel_to_snake("SSOProvider") == "ssoprovider"

    def test_codegen_version_identical(self):
        """_camel_to_snake_codegen in codegen.py must match consistency.py version."""
        assert _camel_to_snake_codegen("CartItem") == "cart_item"
        assert _camel_to_snake_codegen("LineItem") == "line_item"


class TestEntityMatching:
    def test_exact_match(self):
        assert _entity_matches_table("tasks", "tasks")

    def test_singular_plural(self):
        assert _entity_matches_table("task", "tasks")
        assert _entity_matches_table("tasks", "task")
        assert _entity_matches_table("category", "categories")
        assert _entity_matches_table("categories", "category")

    def test_pascal_case_entity_match(self):
        """CartItem -> cart_item -> cart_items (PascalCase + plural)."""
        assert _entity_matches_table("CartItem", "cart_items")
        assert _entity_matches_table("OrderItem", "order_items")

    def test_case_insensitive(self):
        assert _entity_matches_table("TASKS", "tasks")
        assert _entity_matches_table("Products", "products")

    def test_different_entities_dont_match(self):
        assert not _entity_matches_table("users", "orders")
        assert not _entity_matches_table("CartItem", "products")

    def test_es_plural(self):
        assert _entity_matches_table("box", "boxes")
        assert _entity_matches_table("boxes", "box")

    def test_entity_matches_any_table(self):
        tables = {"cart_items", "orders", "products"}
        assert _entity_matches_any_table("CartItem", tables)
        assert _entity_matches_any_table("Order", tables)
        assert _entity_matches_any_table("Product", tables)
        assert not _entity_matches_any_table("GhostEntity", tables)


class TestSingularize:
    def test_basic_singularize(self):
        assert _singularize("tasks") == "task"
        assert _singularize("products") == "product"
        assert _singularize("categories") == "category"
        assert _singularize("boxes") == "box"

    def test_no_strip_words(self):
        assert _singularize("status") == "status"  # Should not become "statu"
        assert _singularize("class") == "class"


class TestTableNameToClassName:
    def test_snake_to_pascal(self):
        assert _table_name_to_class_name("cart_items") == "CartItems"
        assert _table_name_to_class_name("order_line_items") == "OrderLineItems"
        assert _table_name_to_class_name("users") == "Users"


# ================================================================
# Path Normalization Tests
# ================================================================

class TestPathNormalization:
    def test_express_to_openapi(self):
        api = {"endpoints": [
            {"path": "/products/:id", "method": "GET"},
            {"path": "/admin/orders/:orderId/status", "method": "PUT"},
        ]}
        result = _normalize_api_paths(api)
        assert result["endpoints"][0]["path"] == "/products/{id}"
        assert result["endpoints"][1]["path"] == "/admin/orders/{orderId}/status"

    def test_already_openapi_unchanged(self):
        api = {"endpoints": [
            {"path": "/products/{id}", "method": "GET"},
            {"path": "/users/{userId}/posts", "method": "GET"},
        ]}
        result = _normalize_api_paths(api)
        assert result["endpoints"][0]["path"] == "/products/{id}"
        assert result["endpoints"][1]["path"] == "/users/{userId}/posts"

    def test_mixed_params(self):
        api = {"endpoints": [
            {"path": "/api/:resource/{action}", "method": "POST"},
        ]}
        result = _normalize_api_paths(api)
        assert result["endpoints"][0]["path"] == "/api/{resource}/{action}"

    def test_no_params(self):
        api = {"endpoints": [{"path": "/health", "method": "GET"}]}
        result = _normalize_api_paths(api)
        assert result["endpoints"][0]["path"] == "/health"


# ================================================================
# FK Derivation Tests
# ================================================================

class TestFKDerivation:
    def test_belongs_to_adds_fk(self):
        config = {
            "db_schema": {
                "tables": [
                    {"name": "products", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                        {"name": "name", "type": "VARCHAR"},
                    ]},
                    {"name": "categories", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                    ]},
                ],
                "relations": [
                    {"type": "belongs_to", "from_table": "products", "to_table": "categories"},
                ]
            }
        }
        result = _derive_fk_from_relations(config)
        products_cols = {c["name"] for c in result["db_schema"]["tables"][0]["columns"]}
        assert "category_id" in products_cols
        # Verify FK annotation
        for c in result["db_schema"]["tables"][0]["columns"]:
            if c["name"] == "category_id":
                assert c.get("foreign_key") == {"table": "categories", "column": "id"}

    def test_has_many_adds_reverse_fk(self):
        config = {
            "db_schema": {
                "tables": [
                    {"name": "users", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                    ]},
                    {"name": "posts", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                    ]},
                ],
                "relations": [
                    {"type": "has_many", "from_table": "users", "to_table": "posts"},
                ]
            }
        }
        result = _derive_fk_from_relations(config)
        posts_cols = {c["name"] for c in result["db_schema"]["tables"][1]["columns"]}
        assert "user_id" in posts_cols

    def test_existing_column_gets_fk_annotation(self):
        """If column exists but without FK annotation, it should be added."""
        config = {
            "db_schema": {
                "tables": [
                    {"name": "products", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                        {"name": "category_id", "type": "INTEGER"},  # No FK annotation
                    ]},
                    {"name": "categories", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                    ]},
                ],
                "relations": [
                    {"type": "belongs_to", "from_table": "products", "to_table": "categories"},
                ]
            }
        }
        result = _derive_fk_from_relations(config)
        for c in result["db_schema"]["tables"][0]["columns"]:
            if c["name"] == "category_id":
                assert c.get("foreign_key") == {"table": "categories", "column": "id"}

    def test_no_relations_no_change(self):
        config = {
            "db_schema": {
                "tables": [
                    {"name": "tasks", "columns": [
                        {"name": "id", "type": "INTEGER", "primary_key": True},
                    ]},
                ],
                "relations": []
            }
        }
        result = _derive_fk_from_relations(config)
        assert len(result["db_schema"]["tables"][0]["columns"]) == 1


# ================================================================
# COSMETIC_TYPES & Refinement Tests
# ================================================================

class TestCosmeticTypes:
    def test_cosmetic_types_are_error_type_based(self):
        """COSMETIC_TYPES must use error_type string keys, NOT layer names."""
        assert "circular_fk" in COSMETIC_TYPES
        assert "orphan_table" in COSMETIC_TYPES
        assert "invalid_http_method" in COSMETIC_TYPES
        assert "no_roles_defined" in COSMETIC_TYPES

    def test_layer_names_not_in_cosmetic_types(self):
        """Layer-based filtering was the original bug — ensure it's fixed."""
        assert "cross_layer" not in COSMETIC_TYPES
        assert "required_fields" not in COSMETIC_TYPES
        assert "logical_consistency" not in COSMETIC_TYPES

    def test_cross_layer_errors_NOT_cosmetic(self):
        """Cross-layer errors (missing_db_table, api_field_not_in_db, etc.)
        should NOT be considered cosmetic — they block clean status."""
        assert "missing_db_table" not in COSMETIC_TYPES
        assert "api_field_not_in_db" not in COSMETIC_TYPES
        assert "ui_binding_no_api" not in COSMETIC_TYPES

    def test_only_blocking_severity_count(self):
        """Even cosmetic types should be checked for severity == 'error'."""
        # A warning-level error should not block clean status
        cosmetic_error = {"error_type": "orphan_table", "severity": "warning"}
        real_error = {"error_type": "missing_db_table", "severity": "error"}
        assert cosmetic_error.get("error_type") in COSMETIC_TYPES
        assert real_error.get("error_type") not in COSMETIC_TYPES


# ================================================================
# Validation Tests
# ================================================================

def _make_minimal_config():
    """Build the smallest valid config for testing."""
    return {
        "metadata": {
            "app_name": "TestApp",
            "generated_at": "2026-01-01T00:00:00",
            "version": "1.0.0",
        },
        "ui_schema": {
            "pages": [{
                "name": "Dashboard",
                "route": "/",
                "layout": {
                    "type": "grid",
                    "sections": [{
                        "name": "Main",
                        "components": [
                            {"type": "table", "props": {}, "data_binding": "GET /api/tasks"},
                        ]
                    }]
                }
            }],
            "global_layout": {},
        },
        "api_schema": {
            "base_url": "/api",
            "endpoints": [
                {"method": "GET", "path": "/api/tasks", "description": "List tasks", "entity": "tasks"},
                {"method": "POST", "path": "/api/tasks", "description": "Create task", "entity": "tasks"},
            ],
            "middleware": [
                {"name": "auth", "type": "auth"},
            ],
        },
        "db_schema": {
            "tables": [{
                "name": "tasks",
                "columns": [
                    {"name": "id", "type": "INTEGER", "primary_key": True},
                    {"name": "title", "type": "VARCHAR"},
                    {"name": "done", "type": "BOOLEAN"},
                ],
            }],
            "relations": [],
        },
        "auth_schema": {
            "roles": [
                {"name": "user", "description": "Regular user", "permissions": ["tasks:read", "tasks:create"]},
            ],
            "access_matrix": {
                "user": {"tasks": ["read", "create", "list"]},
            },
        },
        "business_logic": {
            "rules": [{
                "name": "task_due_date",
                "description": "Due date must be future",
                "trigger": "before_insert",
                "action": "validate_due_date",
            }],
            "workflows": [],
        },
    }


class TestValidation:
    def test_minimal_config_validates_clean(self):
        config = _make_minimal_config()
        errors = validate_config(config)
        if errors:
            # Diagnostic: print what failed
            for e in errors:
                print(f"  Layer={e['layer']} Severity={e['severity']} Type={e.get('error_type', 'N/A')} Message={e['message'][:100]}")
        assert len(errors) == 0, f"Expected 0 errors but got {len(errors)}"

    def test_missing_db_table_detected(self):
        config = _make_minimal_config()
        # API references non-existent entity
        config["api_schema"]["endpoints"].append({
            "method": "GET", "path": "/api/products", "description": "Get products", "entity": "products"
        })
        config["ui_schema"]["pages"][0]["layout"]["sections"][0]["components"].append({
            "type": "card", "props": {}, "data_binding": "GET /api/products"
        })
        errors = validate_config(config)
        error_types = {e.get("error_type") for e in errors}
        assert "missing_db_table" in error_types

    def test_circular_fk_detected(self):
        config = _make_minimal_config()
        config["db_schema"]["tables"].append({
            "name": "categories",
            "columns": [
                {"name": "id", "type": "INTEGER", "primary_key": True},
                {"name": "task_id", "type": "INTEGER", "foreign_key": {"table": "tasks", "column": "id"}},
            ],
        })
        config["db_schema"]["tables"][0]["columns"].append({
            "name": "category_id", "type": "INTEGER", "foreign_key": {"table": "categories", "column": "id"},
        })
        errors = _validate_logic(config)
        error_types = {e.get("error_type") for e in errors}
        assert "circular_fk" in error_types

    def test_orphan_table_detected(self):
        config = _make_minimal_config()
        config["db_schema"]["tables"].append({
            "name": "audit_logs",
            "columns": [
                {"name": "id", "type": "INTEGER", "primary_key": True},
                {"name": "message", "type": "TEXT"},
            ],
        })
        errors = _check_orphan_tables(config)
        error_types = {e.get("error_type") for e in errors}
        assert "orphan_table" in error_types

    def test_express_style_params_detected(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"][0]["path"] = "/api/tasks/:id"
        errors = _validate_references(config)
        error_types = {e.get("error_type") for e in errors}
        assert "express_style_params" in error_types

    def test_duplicate_endpoint_detected(self):
        config = _make_minimal_config()
        # Add duplicate endpoint
        config["api_schema"]["endpoints"].append(
            {"method": "GET", "path": "/api/tasks", "description": "Duplicate", "entity": "tasks"}
        )
        errors = _validate_references(config)
        error_types = {e.get("error_type") for e in errors}
        assert "duplicate_endpoint" in error_types

    def test_broken_fk_detected(self):
        config = _make_minimal_config()
        config["db_schema"]["tables"][0]["columns"].append({
            "name": "ghost_id", "type": "INTEGER",
            "foreign_key": {"table": "ghosts", "column": "id"},
        })
        errors = _validate_references(config)
        error_types = {e.get("error_type") for e in errors}
        assert "broken_fk_reference" in error_types

    def test_api_unknown_entity_detected(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"].append({
            "method": "GET", "path": "/api/ghosts", "description": "Get ghosts", "entity": "Ghost"
        })
        errors = _validate_references(config)
        error_types = {e.get("error_type") for e in errors}
        assert "api_unknown_entity" in error_types

    def test_invalid_http_method(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"].append({
            "method": "INVALID", "path": "/api/bad", "description": "Bad method", "entity": "tasks"
        })
        errors = validate_config(config)
        error_types = {e.get("error_type") for e in errors}
        assert "invalid_http_method" in error_types

    def test_auth_no_roles(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"].append({
            "method": "GET", "path": "/api/secret", "description": "Secret",
            "entity": "tasks", "auth_required": True, "roles": []
        })
        errors = validate_config(config)
        error_types = {e.get("error_type") for e in errors}
        assert "auth_no_roles" in error_types

    def test_auth_unknown_roles(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"].append({
            "method": "GET", "path": "/api/secret", "description": "Secret",
            "entity": "tasks", "auth_required": True, "roles": ["superadmin"]
        })
        errors = validate_config(config)
        error_types = {e.get("error_type") for e in errors}
        assert "auth_unknown_roles" in error_types

    def test_auth_missing_matrix(self):
        config = _make_minimal_config()
        config["auth_schema"]["roles"].append({
            "name": "admin", "description": "Admin", "permissions": ["*"]
        })
        errors = _validate_logic(config)
        error_types = {e.get("error_type") for e in errors}
        assert "auth_missing_matrix" in error_types

    def test_no_roles_defined(self):
        config = _make_minimal_config()
        config["auth_schema"]["roles"] = []
        errors = _validate_logic(config)
        error_types = {e.get("error_type") for e in errors}
        assert "no_roles_defined" in error_types


# ================================================================
# Repair Engine Tests
# ================================================================

class TestRepairEngine:
    def setup_method(self):
        self.repair = RepairEngine()

    def test_repair_duplicate_endpoint(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"].append(
            {"method": "GET", "path": "/api/tasks", "description": "Duplicate"}
        )
        error = {
            "error_type": "duplicate_endpoint",
            "layer": "reference_integrity",
            "location": "api_schema.endpoints[/api/tasks]",
            "severity": "error",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "fixed"
        assert len(config["api_schema"]["endpoints"]) == 2  # Original 2 unique endpoints

    def test_repair_auth_no_roles(self):
        config = _make_minimal_config()
        error = {
            "error_type": "auth_no_roles",
            "layer": "cross_layer",
            "api_path": "/api/tasks",
            "severity": "warning",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "fixed"
        # Should have assigned default "user" role to at least one matching endpoint
        any_has_role = any(
            "user" in ep.get("roles", [])
            for ep in config["api_schema"]["endpoints"]
            if ep.get("path") == "/api/tasks"
        )
        assert any_has_role, "No endpoint at /api/tasks got the 'user' role"

    def test_repair_api_unknown_entity(self):
        config = _make_minimal_config()
        config["api_schema"]["endpoints"].append({
            "method": "GET", "path": "/api/task", "description": "Get task",
            "entity": "task"  # "task" singular matches "tasks" table
        })
        error = {
            "error_type": "api_unknown_entity",
            "layer": "reference_integrity",
            "location": "api_schema.endpoints./api/task",
            "severity": "error",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "fixed"

    def test_repair_circular_fk_unresolvable(self):
        config = _make_minimal_config()
        error = {
            "error_type": "circular_fk",
            "layer": "logical_consistency",
            "severity": "warning",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "unresolvable"

    def test_repair_invalid_sql_type(self):
        config = _make_minimal_config()
        config["db_schema"]["tables"][0]["columns"][1]["type"] = "WEIRD_TYPE"
        error = {
            "error_type": "invalid_sql_type",
            "layer": "type_safety",
            "location": "db_schema.tables.tasks.columns.title",
            "severity": "warning",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "fixed"
        # Should coerce to VARCHAR
        assert config["db_schema"]["tables"][0]["columns"][1]["type"] == "VARCHAR"

    def test_repair_broken_fk(self):
        config = _make_minimal_config()
        config["db_schema"]["tables"][0]["columns"].append({
            "name": "ghost_id", "type": "INTEGER",
            "foreign_key": {"table": "ghosts", "column": "id"},
        })
        error = {
            "error_type": "broken_fk_reference",
            "layer": "reference_integrity",
            "location": "db_schema.tables.tasks.columns.ghost_id",
            "severity": "error",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "fixed"
        for c in config["db_schema"]["tables"][0]["columns"]:
            if c["name"] == "ghost_id":
                assert "foreign_key" not in c

    def test_repair_unknown_error_type(self):
        config = _make_minimal_config()
        error = {
            "error_type": "some_unknown_type",
            "layer": "unknown",
            "severity": "error",
        }
        result = self.repair.repair(error, config, {})
        assert result["result"] == "unresolvable"
        assert "No strategy" in result["detail"]


# ================================================================
# Resource Name Extraction Tests
# ================================================================

class TestResourceExtraction:
    def test_simple_path(self):
        names = _extract_resource_names_from_path("/api/tasks")
        assert "tasks" in names

    def test_path_with_param(self):
        names = _extract_resource_names_from_path("/api/tasks/{id}")
        assert "tasks" in names
        assert "id" not in names  # Path params are stripped

    def test_path_with_express_param(self):
        names = _extract_resource_names_from_path("/api/tasks/:id")
        assert "tasks" in names

    def test_nested_path(self):
        names = _extract_resource_names_from_path("/api/projects/{pid}/tasks/{tid}")
        assert "projects" in names
        assert "tasks" in names

    def test_common_prefixes_filtered(self):
        names = _extract_resource_names_from_path("/api/v1/products")
        assert "products" in names
        assert "api" not in names
        assert "v1" not in names


# ================================================================
# Schema Contract Tests
# ================================================================

class TestSchemaContracts:
    def test_intent_ir_schema_valid(self):
        """Verify every schema is a valid JSON Schema with required fields."""
        from jsonschema import Draft7Validator
        # Test that schemas can validate their own structure
        meta_schema = Draft7Validator.META_SCHEMA
        Draft7Validator.check_schema(INTENT_IR_SCHEMA)
        Draft7Validator.check_schema(ARCHITECTURE_IR_SCHEMA)
        Draft7Validator.check_schema(COMPLETE_CONFIG_SCHEMA)
        Draft7Validator.check_schema(UI_SCHEMA_SCHEMA)
        Draft7Validator.check_schema(API_SCHEMA_SCHEMA)
        Draft7Validator.check_schema(DB_SCHEMA_SCHEMA)
        Draft7Validator.check_schema(AUTH_SCHEMA_SCHEMA)
        Draft7Validator.check_schema(BUSINESS_LOGIC_SCHEMA)

    def test_complete_config_schema_references_sub_schemas(self):
        """Complete config schema must embed sub-schema definitions."""
        assert "ui_schema" in COMPLETE_CONFIG_SCHEMA["properties"]
        assert "api_schema" in COMPLETE_CONFIG_SCHEMA["properties"]
        assert "db_schema" in COMPLETE_CONFIG_SCHEMA["properties"]
        assert "auth_schema" in COMPLETE_CONFIG_SCHEMA["properties"]
        assert "business_logic" in COMPLETE_CONFIG_SCHEMA["properties"]

    def test_intent_ir_required_fields(self):
        required = INTENT_IR_SCHEMA["required"]
        assert "app_name" in required
        assert "features" in required
        assert "entities" in required
        assert "roles" in required


# ================================================================
# SQL Type Mapping Tests
# ================================================================

class TestTypeMapping:
    def test_sql_to_python(self):
        assert _map_sql_type_to_python("INTEGER") == "int"
        assert _map_sql_type_to_python("VARCHAR(255)") == "str"
        assert _map_sql_type_to_python("BOOLEAN") == "bool"
        assert _map_sql_type_to_python("TEXT") == "str"
        assert _map_sql_type_to_python("FLOAT") == "float"
        assert _map_sql_type_to_python("UNKNOWN_TYPE") == "str"  # Fallback

    def test_sql_to_sqlalchemy(self):
        assert _map_sqlalchemy_type("INTEGER") == "Integer"
        assert _map_sqlalchemy_type("VARCHAR(255)") == "String"
        assert _map_sqlalchemy_type("BOOLEAN") == "Boolean"
        assert _map_sqlalchemy_type("TEXT") == "Text"
        assert _map_sqlalchemy_type("FLOAT") == "Float"

    def test_parenthetical_strip(self):
        assert _map_sqlalchemy_type("DECIMAL(10,2)") == "Float"
        assert _map_sqlalchemy_type("VARCHAR(255)") == "String"
        assert _map_sql_type_to_python("NUMERIC(10,2)") == "float"


# ================================================================
# Error Key Deduplication Tests
# ================================================================

class TestErrorKey:
    def test_same_error_same_key(self):
        e1 = {"message": "Broken FK", "location": "db.t1.c1", "error_type": "broken_fk"}
        e2 = {"message": "Broken FK", "location": "db.t1.c1", "error_type": "broken_fk"}
        assert _error_key(e1) == _error_key(e2)

    def test_different_error_different_key(self):
        e1 = {"message": "Broken FK", "location": "db.t1.c1", "error_type": "broken_fk"}
        e2 = {"message": "Missing table", "location": "api.endpoint", "error_type": "missing_db_table"}
        assert _error_key(e1) != _error_key(e2)


# ================================================================
# Match Entity To Table (codegen) Tests
# ================================================================

class TestMatchEntityToTable:
    def test_direct_match(self):
        tables = [{"name": "tasks"}, {"name": "users"}]
        assert _match_entity_to_table("tasks", tables) == "tasks"
        assert _match_entity_to_table("users", tables) == "users"

    def test_singular_to_plural(self):
        tables = [{"name": "tasks"}, {"name": "users"}]
        assert _match_entity_to_table("task", tables) == "tasks"
        assert _match_entity_to_table("user", tables) == "users"

    def test_pascal_case_to_snake_plural(self):
        tables = [{"name": "cart_items"}, {"name": "order_items"}]
        assert _match_entity_to_table("CartItem", tables) == "cart_items"
        assert _match_entity_to_table("OrderItem", tables) == "order_items"

    def test_fallback(self):
        tables = [{"name": "tasks"}]
        assert _match_entity_to_table("UnknownEntity", tables) == "UnknownEntity"


# ================================================================
# Normalize Table Name Tests
# ================================================================

class TestNormalizeTableName:
    def test_hyphen_to_underscore(self):
        assert _normalize_table_name("line-items") == "line_items"
        assert _normalize_table_name("cart-items") == "cart_items"

    def test_space_to_underscore(self):
        assert _normalize_table_name("line items") == "line_items"

    def test_lowercase(self):
        assert _normalize_table_name("PRODUCTS") == "products"
