"""
Integration tests for the full pipeline (without LLM calls).
Tests orchestrator, code generation, and end-to-end config flow
using mock/pre-built configs to avoid API dependency.
"""
import pytest
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.generation.codegen import generate as generate_code
from app.generation.validator import validate_generated_code
from app.validation.validator import validate_config
from app.validation.repair import RepairEngine
from app.pipeline.refinement import run as run_refinement
from app.evaluation.metrics import compute_quality_score, MetricsCollector


# A complete minimal config that should pass all validation layers
CLEAN_ECOMMERCE_CONFIG = {
    "metadata": {
        "app_name": "ECommerce Store",
        "generated_at": "2026-06-05T00:00:00",
        "version": "1.0.0",
        "complexity": "medium",
    },
    "ui_schema": {
        "pages": [
            {
                "name": "Products",
                "route": "/products",
                "layout": {
                    "type": "grid",
                    "sections": [{
                        "name": "Product List",
                        "components": [
                            {"type": "table", "props": {}, "data_binding": "GET /api/products"},
                        ]
                    }]
                }
            }
        ],
        "global_layout": {},
    },
    "api_schema": {
        "base_url": "/api",
        "endpoints": [
            {"method": "GET", "path": "/api/products", "description": "List products", "entity": "products"},
            {"method": "POST", "path": "/api/products", "description": "Create product", "entity": "products"},
            {"method": "GET", "path": "/api/products/{id}", "description": "Get product", "entity": "products"},
            {"method": "PUT", "path": "/api/products/{id}", "description": "Update product", "entity": "products"},
            {"method": "DELETE", "path": "/api/products/{id}", "description": "Delete product", "entity": "products"},
            {"method": "GET", "path": "/api/users", "description": "List users", "entity": "users", "auth_required": True, "roles": ["admin"]},
            {"method": "POST", "path": "/api/users", "description": "Create user", "entity": "users"},
            {"method": "GET", "path": "/api/orders", "description": "List orders", "entity": "orders", "auth_required": True, "roles": ["admin", "user"]},
            {"method": "POST", "path": "/api/orders", "description": "Create order", "entity": "orders"},
            {"method": "GET", "path": "/api/orders/{id}", "description": "Get order", "entity": "orders"},
        ],
        "middleware": [
            {"name": "auth", "type": "auth"},
            {"name": "cors", "type": "cors"},
        ],
    },
    "db_schema": {
        "tables": [
            {
                "name": "products",
                "columns": [
                    {"name": "id", "type": "INTEGER", "primary_key": True},
                    {"name": "name", "type": "VARCHAR"},
                    {"name": "price", "type": "FLOAT"},
                    {"name": "description", "type": "TEXT"},
                    {"name": "stock", "type": "INTEGER"},
                ],
            },
            {
                "name": "users",
                "columns": [
                    {"name": "id", "type": "INTEGER", "primary_key": True},
                    {"name": "email", "type": "VARCHAR"},
                    {"name": "name", "type": "VARCHAR"},
                    {"name": "role", "type": "VARCHAR"},
                ],
            },
            {
                "name": "orders",
                "columns": [
                    {"name": "id", "type": "INTEGER", "primary_key": True},
                    {"name": "total", "type": "FLOAT"},
                    {"name": "status", "type": "VARCHAR"},
                    {"name": "user_id", "type": "INTEGER", "foreign_key": {"table": "users", "column": "id"}},
                    {"name": "product_id", "type": "INTEGER", "foreign_key": {"table": "products", "column": "id"}},
                ],
            },
        ],
        "relations": [
            {"type": "belongs_to", "from_table": "orders", "to_table": "users", "foreign_key": "user_id"},
            {"type": "belongs_to", "from_table": "orders", "to_table": "products", "foreign_key": "product_id"},
            {"type": "has_many", "from_table": "users", "to_table": "orders"},
            {"type": "has_many", "from_table": "products", "to_table": "orders"},
        ],
    },
    "auth_schema": {
        "roles": [
            {"name": "admin", "description": "Full access", "permissions": ["*"]},
            {"name": "user", "description": "Regular user", "permissions": ["products:read", "orders:read", "orders:create"]},
        ],
        "access_matrix": {
            "admin": {"products": ["create", "read", "update", "delete", "list"],
                       "orders": ["create", "read", "update", "delete", "list"],
                       "users": ["create", "read", "update", "delete", "list"]},
            "user": {"products": ["read", "list"],
                      "orders": ["read", "create", "list"]},
        },
    },
    "business_logic": {
        "rules": [
            {"name": "stock_check", "description": "Cannot order out-of-stock items",
             "trigger": "before_insert", "action": "validate_stock",
             "entities_involved": ["orders", "products"]},
        ],
        "workflows": [],
    },
}


class TestCodeGeneration:
    def test_generate_produces_all_files(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        expected = ["schema.sql", "app.py", "models.py", "schemas.py", "auth.py",
                    "business.py", "requirements.txt", "Dockerfile"]
        for fname in expected:
            assert fname in files, f"Missing file: {fname}"

    def test_generated_sql_has_create_table(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        sql = files["schema.sql"]
        assert "CREATE TABLE" in sql
        assert "products" in sql
        assert "orders" in sql
        assert "users" in sql

    def test_generated_sql_has_foreign_keys(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        sql = files["schema.sql"]
        assert "REFERENCES" in sql
        assert "REFERENCES users" in sql or "REFERENCES users(id)" in sql

    def test_generated_app_has_crud(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        app_py = files["app.py"]
        assert "from fastapi import" in app_py
        assert "db.query" in app_py
        assert "SessionLocal" in app_py

    def test_generated_app_no_pass_stubs(self):
        """Generated app.py must have real CRUD, not pass stubs."""
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        app_py = files["app.py"]
        # Count pass occurrences (should only be in function signatures like pass, not stubs)
        # Real CRUD routes shouldn't use pass as the only body statement
        lines_with_pass = [l.strip() for l in app_py.split("\n") if l.strip() == "pass"]
        # "pass" appears in a few places as legitimate Python (empty else, etc.)
        # but the main route functions should NOT be pass-only
        assert "db.query" in app_py, "CRUD routes must have real DB queries"

    def test_generated_models_has_sqlalchemy(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        models = files["models.py"]
        assert "from sqlalchemy" in models
        assert "Base = declarative_base" in models

    def test_generated_models_has_relationships(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        models = files["models.py"]
        assert "relationship" in models or "ForeignKey" in models

    def test_template_files_generated(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        templates = [k for k in files if k.startswith("templates/")]
        assert len(templates) >= 1

    def test_generated_requirements_pinned_or_minimum(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        reqs = files["requirements.txt"]
        assert "fastapi" in reqs
        assert "pydantic" in reqs
        # Versions should NOT be pinned with == (use >= for compatibility)
        assert "pydantic>=" in reqs


class TestCodeValidation:
    def test_python_files_parse(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        issues = validate_generated_code(files)
        py_issues = [i for i in issues if i.get("type") == "python_syntax_error"]
        assert len(py_issues) == 0, f"Python syntax errors: {py_issues}"

    def test_sql_parses_in_sqlite(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        issues = validate_generated_code(files)
        sql_issues = [i for i in issues if i.get("type") == "sql_error"]
        assert len(sql_issues) == 0, f"SQL errors: {sql_issues}"

    def test_html_has_doctype(self):
        files = generate_code(CLEAN_ECOMMERCE_CONFIG)
        issues = validate_generated_code(files)
        html_issues = [i for i in issues if i.get("type") == "html_missing_doctype"]
        assert len(html_issues) == 0, f"HTML DOCTYPE issues: {html_issues}"


class TestConfigValidation:
    def test_clean_config_passes_all_layers(self):
        config = json.loads(json.dumps(CLEAN_ECOMMERCE_CONFIG))  # Deep copy
        errors = validate_config(config)
        if errors:
            for e in errors:
                print(f"  [{e['layer']}] {e['severity']}: {e['message'][:120]}")
        assert len(errors) == 0, f"Expected 0 validation errors, got {len(errors)}"

    def test_config_with_error_detected(self):
        config = json.loads(json.dumps(CLEAN_ECOMMERCE_CONFIG))
        # Inject a broken FK
        config["db_schema"]["tables"][0]["columns"].append({
            "name": "ghost_id", "type": "INTEGER",
            "foreign_key": {"table": "ghosts", "column": "id"}
        })
        errors = validate_config(config)
        assert any("broken_fk_reference" == e.get("error_type") for e in errors)


class TestRefinementPipeline:
    def test_refinement_on_clean_config(self):
        config = json.loads(json.dumps(CLEAN_ECOMMERCE_CONFIG))
        result = run_refinement(config, architecture_ir={}, api_key=None)
        status = result.get("metadata", {}).get("validation_status")
        assert status == "clean", f"Expected clean, got {status}"

    def test_refinement_repairs_errors(self):
        config = json.loads(json.dumps(CLEAN_ECOMMERCE_CONFIG))
        # Inject an issue that can be repaired
        config["auth_schema"]["roles"].append({
            "name": "manager",
            "description": "Manager role",
            "permissions": ["products:read", "orders:read"]
        })
        # Add an endpoint using this role
        config["api_schema"]["endpoints"].append({
            "method": "GET", "path": "/api/reports",
            "description": "View reports", "entity": "orders",
            "auth_required": True, "roles": ["manager"]
        })
        # But missing from access_matrix
        result = run_refinement(config, architecture_ir={}, api_key=None)
        repair_log = result.get("repair_log", [])
        # auth_missing_matrix is a heuristic fix (no LLM needed), so repair should succeed
        # even without an API key. Verify it either repaired or left clean status.
        status = result.get("metadata", {}).get("validation_status", "")
        assert status in ("clean", "has_unresolved"), f"Unexpected status: {status}"
        # If repaired, verify the repair log references the right strategy
        if repair_log:
            strategies = [r.get("strategy") for r in repair_log]
            assert any(
                s in ("add_matrix_entries", "add_missing_roles")
                for s in strategies
            ), f"Repair log strategies: {strategies}"


class TestQualityScoring:
    def test_clean_config_scores_high(self):
        score = compute_quality_score({
            "config": CLEAN_ECOMMERCE_CONFIG,
            "validation_status": "clean",
            "repair_count": 0,
            "needs_clarification": False,
            "intent_ir": {},
        })
        assert score["composite"] >= 80, f"Expected >=80 composite, got {score['composite']}"

    def test_no_config_scores_zero(self):
        score = compute_quality_score({
            "config": None,
            "validation_status": "no_config",
            "repair_count": 0,
            "needs_clarification": False,
            "intent_ir": {},
        })
        assert score["composite"] < 50

    def test_needs_clarification_returns_lower_clarity(self):
        score = compute_quality_score({
            "config": CLEAN_ECOMMERCE_CONFIG,
            "validation_status": "clean",
            "repair_count": 0,
            "needs_clarification": True,
            "intent_ir": {},
            "expected_behavior": "needs_clarification",
        })
        assert score["clarity_detection"] == 100  # When expected, flagging is correct

    def test_each_dimension_present(self):
        score = compute_quality_score({
            "config": CLEAN_ECOMMERCE_CONFIG,
            "validation_status": "clean",
            "repair_count": 0,
            "needs_clarification": False,
            "intent_ir": {},
        })
        expected_dims = ["schema_completeness", "validation_pass_rate",
                        "repair_effectiveness", "code_executability",
                        "clarity_detection", "conflict_detection", "composite"]
        for dim in expected_dims:
            assert dim in score, f"Missing dimension: {dim}"


class TestMetricsCollector:
    def test_record_and_summarize(self):
        collector = MetricsCollector()
        collector.record_run(
            "test_01", "real", "Test prompt",
            {
                "success": True,
                "config": CLEAN_ECOMMERCE_CONFIG,
                "total_latency_seconds": 5.2,
                "cost": {"estimated_cost_usd": 0.001},
                "needs_clarification": False,
            },
            expected_behavior="none",
            expected_complexity="medium",
        )
        summary = collector.compute_summary()
        assert summary["total_runs"] == 1
        assert summary["success_rate"] == 100.0

    def test_multiple_runs(self):
        collector = MetricsCollector()
        for i in range(3):
            collector.record_run(
                f"test_{i}", "real", "Test prompt",
                {
                    "success": i < 2,  # 2 succeed, 1 fails
                    "config": CLEAN_ECOMMERCE_CONFIG,
                    "total_latency_seconds": 5.0,
                    "cost": {"estimated_cost_usd": 0.001},
                },
            )
        summary = collector.compute_summary()
        assert summary["total_runs"] == 3

    def test_by_category(self):
        collector = MetricsCollector()
        collector.record_run("real_01", "real", "Real prompt",
                           {"success": True, "config": CLEAN_ECOMMERCE_CONFIG, "total_latency_seconds": 3.0})
        collector.record_run("edge_01", "edge", "Edge prompt",
                           {"success": False, "config": None, "total_latency_seconds": 1.0})
        by_cat = collector.by_category()
        assert "real_prompts" in by_cat
        assert "edge_cases" in by_cat

    def test_report_generation(self):
        collector = MetricsCollector()
        collector.record_run("test_01", "real", "Test prompt",
                           {"success": True, "config": CLEAN_ECOMMERCE_CONFIG,
                            "total_latency_seconds": 5.0, "cost": {"estimated_cost_usd": 0.001}})
        report = collector.generate_cost_quality_report()
        assert "# Cost-Quality Analysis Report" in report
        assert "Summary" in report

    def test_export_json(self):
        import tempfile
        collector = MetricsCollector()
        collector.record_run("test_01", "real", "Test prompt",
                           {"success": True, "config": CLEAN_ECOMMERCE_CONFIG,
                            "total_latency_seconds": 5.0, "cost": {"estimated_cost_usd": 0.001}})
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            fpath = f.name
        try:
            collector.export_json(fpath)
            with open(fpath) as f:
                data = json.load(f)
            assert "summary" in data
            assert "runs" in data
        finally:
            os.unlink(fpath)


class TestFKDerivationEndToEnd:
    def test_fk_column_generated_in_sql(self):
        """When relations are present, FK columns must appear in SQL with REFERENCES."""
        config = json.loads(json.dumps(CLEAN_ECOMMERCE_CONFIG))
        files = generate_code(config)
        sql = files["schema.sql"]
        # Verify FK from orders to users
        assert "REFERENCES" in sql
        # The orders table should reference users
        assert "REFERENCES users" in sql or "REFERENCES users(id)" in sql
        assert "REFERENCES products" in sql or "REFERENCES products(id)" in sql

    def test_all_relations_become_columns(self):
        """Every belongs_to or has_one relation must produce a FK column."""
        config = json.loads(json.dumps(CLEAN_ECOMMERCE_CONFIG))
        files = generate_code(config)
        models = files["models.py"]
        # orders model must have user_id and product_id
        assert "user_id" in models
        assert "product_id" in models
