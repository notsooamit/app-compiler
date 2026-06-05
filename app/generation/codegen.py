"""
Code Generator
Consumes the final validated config and generates working application code:
- schema.sql (SQLite-compatible DDL)
- app.py (FastAPI routes with real CRUD)
- models.py (SQLAlchemy models with relationships)
- schemas.py (Pydantic request/response schemas)
- business.py (Business logic validation)
- auth.py (Auth middleware)
- Dockerfile (Container configuration)
- templates/*.html (Dashboard template with live JS)

This PROVES the config is specific and consistent enough to drive real code generation.
"""
import json
import re
import secrets
from typing import Dict, List
from datetime import datetime


def generate(config: dict) -> Dict[str, str]:
    """
    Generate all code files from the config.

    Returns:
        Dict mapping filename to file contents.
    """
    # Pre-process: ensure FK columns exist for all relations
    config = _derive_fk_from_relations(config)
    # Collect junction table names (shared across generators)
    junction_tables = set()
    for rel in config.get("db_schema", {}).get("relations", []):
        if rel.get("type") == "many_to_many" and rel.get("junction_table"):
            junction_tables.add(rel["junction_table"])
    config["_junction_tables"] = junction_tables

    files = {}

    files["schema.sql"] = _generate_sql(config)
    files["app.py"] = _generate_app(config)
    files["models.py"] = _generate_models(config)
    files["schemas.py"] = _generate_schemas(config)
    files["auth.py"] = _generate_auth(config)
    files["business.py"] = _generate_business_logic(config)
    files["requirements.txt"] = _generate_requirements(config)
    files["Dockerfile"] = _generate_dockerfile(config)

    # Generate HTML templates for each page
    for page in config.get("ui_schema", {}).get("pages", []):
        page_name = page["name"].lower().replace(" ", "_")
        files[f"templates/{page_name}.html"] = _generate_page_template(page, config)

    # Clean up internal keys to prevent state pollution on shared configs
    config.pop("_junction_tables", None)
    return files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _singularize(name: str) -> str:
    """Simple singularization for English words (for relationship naming)."""
    _no_strip = {"status", "address", "canvas", "bus", "class", "process",
                 "access", "success", "progress", "analysis", "basis",
                 "diagnosis", "synopsis", "alias", "atlas", "corpus"}
    n = name.lower()
    if n in _no_strip:
        return name
    if n.endswith("ies") and len(n) > 3:
        return name[:-3] + "y"
    if n.endswith("ses") or n.endswith("xes") or n.endswith("ches") or n.endswith("shes"):
        return name[:-2]
    if n.endswith("s") and not n.endswith("ss") and len(n) > 2:
        return name[:-1]
    return name


def _table_name_to_class_name(table_name: str) -> str:
    """Convert snake_case table name to PascalCase class name."""
    return table_name.replace("_", " ").title().replace(" ", "")


def _camel_to_snake_codegen(name: str) -> str:
    """Convert PascalCase/camelCase to snake_case: CartItem -> cart_item."""
    s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    return s1.lower()


def _match_entity_to_table(entity: str, tables: List[dict]) -> str:
    """
    Find the table name that matches an API entity name.
    Handles singular/plural variations, case differences, AND PascalCase->snake_case.
    Returns the table name, or *entity* as fallback.
    """
    e = entity.lower().strip().replace("-", "_")
    # Also try CamelCase -> snake_case: "CartItem" -> "cart_item"
    e_camel = _camel_to_snake_codegen(entity).replace("-", "_")
    candidates = [e, e_camel]

    for candidate in candidates:
        # Direct match first
        for t in tables:
            tn = t["name"].lower().strip()
            if candidate == tn:
                return t["name"]

        # Singular/plural matching
        for t in tables:
            tn = t["name"].lower().strip()
            if candidate == tn + "s" or candidate + "s" == tn:
                return t["name"]
            if candidate.endswith("ies") and candidate[:-3] + "y" == tn:
                return t["name"]
            if tn.endswith("ies") and tn[:-3] + "y" == candidate:
                return t["name"]
            if candidate.endswith("es") and candidate[:-2] == tn:
                return t["name"]
            if tn.endswith("es") and tn[:-2] == candidate:
                return t["name"]

    return entity  # fallback


def _infer_pk_type_for_entity(entity: str, tables: List[dict]) -> str:
    """Look up the primary key column type for an entity and return the Python type.
    Defaults to 'int' if no PK column is found."""
    if not entity:
        return "int"
    table_name = _match_entity_to_table(entity, tables)
    for t in tables:
        if t.get("name") == table_name:
            for col in t.get("columns", []):
                if col.get("primary_key"):
                    sql_type = col.get("type", "").upper()
                    # UUID/VARCHAR/CHAR/TEXT PKs should be str in route params
                    if any(t in sql_type for t in ("UUID", "VARCHAR", "CHAR", "TEXT")):
                        return "str"
                    return "int"
    return "int"


def _map_sql_type_to_python(sql_type: str) -> str:
    """Map SQL column type to Python type annotation for Pydantic fields."""
    mapping = {
        "INTEGER": "int",
        "BIGINT": "int",
        "SERIAL": "int",
        "VARCHAR": "str",
        "CHAR": "str",
        "TEXT": "str",
        "BOOLEAN": "bool",
        "TIMESTAMP": "str",
        "FLOAT": "float",
        "DOUBLE": "float",
        "DATE": "str",
        "UUID": "str",
        "JSON": "str",
        "BLOB": "str",
        "DECIMAL": "float",
        "NUMERIC": "float",
        "ENUM": "str",
    }
    clean_type = sql_type.upper().split("(")[0].strip()
    return mapping.get(clean_type, "str")


def _map_sqlalchemy_type(sql_type: str) -> str:
    """Map SQL type to SQLAlchemy Column type."""
    mapping = {
        "INTEGER": "Integer",
        "BIGINT": "Integer",
        "SERIAL": "Integer",
        "VARCHAR": "String",
        "CHAR": "String",
        "TEXT": "Text",
        "BOOLEAN": "Boolean",
        "TIMESTAMP": "DateTime",
        "FLOAT": "Float",
        "DOUBLE": "Float",
        "DATE": "DateTime",
        "UUID": "String",
        "JSON": "Text",
        "BLOB": "Text",
        "DECIMAL": "Float",
        "NUMERIC": "Float",
        "ENUM": "String",
    }
    # Strip parenthetical suffixes: VARCHAR(255) -> VARCHAR, DECIMAL(10,2) -> DECIMAL
    clean_type = sql_type.upper().split("(")[0].strip()
    return mapping.get(clean_type, "String")


def _dict_to_python(d: dict, indent: int = 4) -> str:
    """Convert a dict to Python-literal string (for code generation).
    Uses repr() for correct Python True/False/None literals, not JSON true/false/null."""
    return _repr_value(d, indent, 0)


def _repr_value(value, indent: int = 4, depth: int = 0) -> str:
    """Recursively convert a Python value to its repr string, with proper indentation."""
    pad = " " * indent * depth
    pad_inner = " " * indent * (depth + 1)

    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad_inner}{repr(k)}: {_repr_value(v, indent, depth + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    elif isinstance(value, list):
        if not value:
            return "[]"
        items = [f"{pad_inner}{_repr_value(v, indent, depth + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    elif isinstance(value, bool):
        return "True" if value else "False"
    elif value is None:
        return "None"
    elif isinstance(value, str):
        return repr(value)
    elif isinstance(value, (int, float)):
        return repr(value)
    else:
        return repr(value)


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

def _derive_fk_from_relations(config: dict) -> dict:
    """Ensure every relation has a corresponding FK column on the from_table.
    Mutates the config in place and returns it.
    e.g., relation {from: products, to: categories} ensures products has
    a category_id INTEGER column with foreign_key to categories(id)."""
    tables = config.get("db_schema", {}).get("tables", [])
    relations = config.get("db_schema", {}).get("relations", [])

    # Build lookup: table_name -> {column names}
    table_columns = {}
    for t in tables:
        table_columns[t["name"]] = {c["name"] for c in t.get("columns", [])}

    for rel in relations:
        rtype = rel.get("type")
        ft = rel.get("from_table")
        tt = rel.get("to_table")
        if not ft or not tt:
            continue

        fk_col_name = rel.get("foreign_key", f"{_singularize(tt)}_id")

        # Only belongs_to and has_one add FK to the from_table
        if rtype in ("belongs_to", "has_one"):
            for t in tables:
                if t["name"] == ft:
                    # Check if FK column already exists
                    existing_col = None
                    for c in t.get("columns", []):
                        if c["name"] == fk_col_name:
                            existing_col = c
                            break
                    if existing_col:
                        # Column exists but might lack FK annotation — add it
                        if "foreign_key" not in existing_col:
                            existing_col["foreign_key"] = {"table": tt, "column": "id"}
                    else:
                        # Add a new FK column
                        t.setdefault("columns", []).append({
                            "name": fk_col_name,
                            "type": "INTEGER",
                            "foreign_key": {"table": tt, "column": "id"},
                        })
                    table_columns.setdefault(ft, set()).add(fk_col_name)
                    break

        # For has_many: FK goes on the to_table (reversed)
        elif rtype == "has_many":
            reverse_fk = rel.get("foreign_key", f"{_singularize(ft)}_id")
            for t in tables:
                if t["name"] == tt:
                    existing_col = None
                    for c in t.get("columns", []):
                        if c["name"] == reverse_fk:
                            existing_col = c
                            break
                    if existing_col:
                        if "foreign_key" not in existing_col:
                            existing_col["foreign_key"] = {"table": ft, "column": "id"}
                    else:
                        t.setdefault("columns", []).append({
                            "name": reverse_fk,
                            "type": "INTEGER",
                            "foreign_key": {"table": ft, "column": "id"},
                        })
                    table_columns.setdefault(tt, set()).add(reverse_fk)
                    break

    return config


def _generate_sql(config: dict) -> str:
    """Generate CREATE TABLE statements from DB schema."""
    lines = [
        "-- Generated by App Compiler",
        f"-- App: {config.get('metadata', {}).get('app_name', 'Untitled')}",
        f"-- Generated at: {datetime.now().isoformat()}",
        "",
    ]

    for table in config.get("db_schema", {}).get("tables", []):
        table_name = table["name"]
        lines.append(f"-- Table: {table_name}")
        lines.append(f"CREATE TABLE IF NOT EXISTS {table_name} (")

        col_defs = []
        for col in table.get("columns", []):
            col_def = f"    {col['name']} {col['type']}"
            if col.get("primary_key"):
                col_def += " PRIMARY KEY"
            if col.get("nullable") is False:
                col_def += " NOT NULL"
            if col.get("unique"):
                col_def += " UNIQUE"
            if col.get("default") is not None:
                default_val = col['default']
                if isinstance(default_val, bool):
                    col_def += f" DEFAULT {1 if default_val else 0}"
                elif isinstance(default_val, str):
                    col_def += f" DEFAULT '{default_val}'"
                else:
                    col_def += f" DEFAULT {default_val}"
            # Foreign keys
            fk = col.get("foreign_key")
            if fk:
                col_def += f" REFERENCES {fk['table']}({fk.get('column', 'id')})"
            col_defs.append(col_def)

        lines.append(",\n".join(col_defs))
        lines.append(");\n")

    # Generate junction tables for many_to_many relations
    for rel in config.get("db_schema", {}).get("relations", []):
        if rel.get("type") == "many_to_many":
            jt = rel.get("junction_table", f"{rel['from_table']}_{rel['to_table']}")
            lines.append(f"-- Junction table for {rel['from_table']} <-> {rel['to_table']}")
            lines.append(f"CREATE TABLE IF NOT EXISTS {jt} (")
            lines.append(f"    {rel['from_table']}_id INTEGER REFERENCES {rel['from_table']}(id),")
            lines.append(f"    {rel['to_table']}_id INTEGER REFERENCES {rel['to_table']}(id),")
            lines.append(f"    PRIMARY KEY ({rel['from_table']}_id, {rel['to_table']}_id)")
            lines.append(");\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Models  (enhanced with relationships)
# ---------------------------------------------------------------------------

def _generate_models(config: dict) -> str:
    """Generate SQLAlchemy models from DB schema with relationships."""
    lines = [
        '"""SQLAlchemy ORM models -- generated from config."""',
        "from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, Table",
        "from sqlalchemy.orm import relationship, declarative_base",
        "",
        "Base = declarative_base()",
        "",
    ]

    tables = config.get("db_schema", {}).get("tables", [])
    relations = config.get("db_schema", {}).get("relations", [])
    table_names = {t["name"] for t in tables}

    # ------------------------------------------------------------------ #
    # Association tables for many_to_many relations (emitted before models)
    # ------------------------------------------------------------------ #
    m2m_variable_names = {}  # (from_table, to_table) -> variable_name
    _generated_junction_tables = set()  # Avoid duplicate Table definitions
    for rel in relations:
        if rel.get("type") == "many_to_many":
            from_t = rel["from_table"]
            to_t = rel["to_table"]
            jt = rel.get("junction_table", f"{from_t}_{to_t}")
            if jt in _generated_junction_tables:
                # Already defined — reuse the variable name
                for (ft, tt), vname in m2m_variable_names.items():
                    if rel.get("junction_table") == jt or f"{ft}_{tt}" == jt:
                        m2m_variable_names[(from_t, to_t)] = vname
                        break
                continue
            _generated_junction_tables.add(jt)
            var = f"_{from_t}_{to_t}_table"
            m2m_variable_names[(from_t, to_t)] = var

            from_fk = f"{from_t}_id"
            to_fk = f"{to_t}_id"
            lines.append(f"# Association table for {from_t} <-> {to_t}")
            lines.append(f"{var} = Table(")
            lines.append(f'    "{jt}",')
            lines.append("    Base.metadata,")
            lines.append(f'    Column("{from_fk}", Integer, ForeignKey("{from_t}.id"), primary_key=True),')
            lines.append(f'    Column("{to_fk}", Integer, ForeignKey("{to_t}.id"), primary_key=True),')
            lines.append(")")
            lines.append("")

    # ------------------------------------------------------------------ #
    # Build relationship metadata so we can emit both sides correctly
    # ------------------------------------------------------------------ #
    # For each table we collect (attr_name, relationship_call_string) pairs.
    table_relations: Dict[str, list] = {t["name"]: [] for t in tables}

    # helper: generate a variable name for the association table
    def _m2m_var(a, b):
        if (a, b) in m2m_variable_names:
            return m2m_variable_names[(a, b)]
        if (b, a) in m2m_variable_names:
            return m2m_variable_names[(b, a)]
        return None

    for rel in relations:
        rtype = rel.get("type")
        if rtype == "belongs_to":
            ft, tt = rel["from_table"], rel["to_table"]
            if ft in table_names and tt in table_names:
                to_class = _table_name_to_class_name(tt)
                to_sing = _singularize(tt)
                from_plural = ft  # e.g. "tasks"

                # from_table side: e.g. project = relationship("Project", back_populates="tasks")
                table_relations.setdefault(ft, []).append(
                    (to_sing, f'relationship("{to_class}", back_populates="{from_plural}")')
                )
                # to_table side: e.g. tasks = relationship("Task", back_populates="project")
                from_class = _table_name_to_class_name(ft)
                table_relations.setdefault(tt, []).append(
                    (from_plural, f'relationship("{from_class}", back_populates="{to_sing}")')
                )

        elif rtype == "has_many":
            ft, tt = rel["from_table"], rel["to_table"]
            if ft in table_names and tt in table_names:
                to_class = _table_name_to_class_name(tt)
                from_sing = _singularize(ft)
                to_plural = tt

                # from_table side: tasks = relationship("Task", back_populates="project")
                table_relations.setdefault(ft, []).append(
                    (to_plural, f'relationship("{to_class}", back_populates="{from_sing}")')
                )
                # to_table side: project = relationship("Project", back_populates="tasks")
                to_class_rev = _table_name_to_class_name(ft)
                table_relations.setdefault(tt, []).append(
                    (from_sing, f'relationship("{to_class_rev}", back_populates="{to_plural}")')
                )

        elif rtype == "has_one":
            ft, tt = rel["from_table"], rel["to_table"]
            if ft in table_names and tt in table_names:
                from_class = _table_name_to_class_name(ft)
                to_class = _table_name_to_class_name(tt)
                from_sing = _singularize(ft)
                to_sing = _singularize(tt)

                table_relations.setdefault(ft, []).append(
                    (to_sing, f'relationship("{to_class}", uselist=False, back_populates="{from_sing}")')
                )
                table_relations.setdefault(tt, []).append(
                    (from_sing, f'relationship("{from_class}", back_populates="{to_sing}")')
                )

        elif rtype == "many_to_many":
            ft, tt = rel["from_table"], rel["to_table"]
            if ft in table_names and tt in table_names:
                var = _m2m_var(ft, tt)
                if var:
                    to_class_f = _table_name_to_class_name(tt)
                    to_class_t = _table_name_to_class_name(ft)
                    f_plural = tt
                    t_plural = ft

                    table_relations.setdefault(ft, []).append(
                        (f_plural, f'relationship("{to_class_f}", secondary={var}, back_populates="{t_plural}")')
                    )
                    table_relations.setdefault(tt, []).append(
                        (t_plural, f'relationship("{to_class_t}", secondary={var}, back_populates="{f_plural}")')
                    )

    # ------------------------------------------------------------------ #
    # Emit model classes (skip junction tables — handled as association Tables)
    # ------------------------------------------------------------------ #
    _junction_tables = config.get("_junction_tables", set())
    for table in tables:
        table_name = table["name"]
        if table_name in _junction_tables:
            continue  # Junction tables are handled as association Tables above
        class_name = _table_name_to_class_name(table_name)
        lines.append(f"class {class_name}(Base):")
        lines.append(f'    __tablename__ = "{table_name}"')
        lines.append("")

        for col in table.get("columns", []):
            col_type = _map_sqlalchemy_type(col["type"])
            pos_args = []    # Positional: ForeignKey goes here (must come before keyword args)
            kw_args = []     # Keyword: primary_key, nullable, unique
            if col.get("foreign_key"):
                fk = col["foreign_key"]
                pos_args.append(f'ForeignKey("{fk["table"]}.{fk.get("column", "id")}")')
            if col.get("primary_key"):
                kw_args.append("primary_key=True")
            if col.get("unique"):
                kw_args.append("unique=True")
            if col.get("nullable") is False and not col.get("primary_key"):
                kw_args.append("nullable=False")

            all_args = ", ".join(pos_args + kw_args)
            lines.append(f"    {col['name']} = Column({col_type}{', ' + all_args if all_args else ''})")

        # Relationship declarations
        seen_attrs = set()
        for attr_name, rel_str in table_relations.get(table_name, []):
            if attr_name not in seen_attrs:
                lines.append(f"    {attr_name} = {rel_str}")
                seen_attrs.add(attr_name)

        # Use the first column name for __repr__ (not all tables have "id")
        first_col = table.get("columns", [{"name": "id"}])[0].get("name", "id") if table.get("columns") else "id"
        lines.append("")
        lines.append(f"    def __repr__(self):")
        lines.append(f'        return f"<{class_name} {{self.{first_col}}}>"')
        lines.append("\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# App  (complete rewrite with real CRUD)
# ---------------------------------------------------------------------------

def _generate_seed_data(config: dict) -> list:
    """Generate a seed_database() function that inserts sample rows into every table.
    Data is generated dynamically from column types — works for ANY LLM-generated schema."""
    tables = config.get("db_schema", {}).get("tables", [])
    if not tables:
        return []

    lines = [
        "",
        "# ---- Database seeding ----",
        "def seed_database():",
        '    """Insert sample data on first run so the app feels alive."""',
        "    db = SessionLocal()",
        "    try:",
        "        pass  # No seedable tables found",
    ]

    # Collect all FK references so we can fill them with valid values
    all_fks = {}
    for table in tables:
        for col in table.get("columns", []):
            fk = col.get("foreign_key")
            if fk:
                all_fks[(table["name"], col["name"])] = fk

    _junction_tables = config.get("_junction_tables", set())
    for table in tables:
        tname = table.get("name", "")
        if tname in _junction_tables:
            continue  # Skip junction tables in seed data
        class_name = _table_name_to_class_name(tname)
        columns = table.get("columns", [])
        if not columns:
            continue

        # Build the seed data dynamically from column types
        non_pk_cols = [c for c in columns if not c.get("primary_key")]
        if not non_pk_cols:
            continue

        lines.append(f"        # Seed table: {tname}")
        lines.append(f"        if db.query({class_name}).first() is None:")

        # Generate 3 rows
        for row_idx in range(3):
            fields = {}
            for col in non_pk_cols:
                cname = col["name"]
                ctype = col.get("type", "").upper()
                nullable = col.get("nullable", True)
                has_fk = col.get("foreign_key")

                if has_fk:
                    # FK column: use 1 for the first seed row (assumes referenced table has id=1)
                    fields[cname] = 1 if row_idx == 0 else (row_idx + 1)
                elif "VARCHAR" in ctype or "CHAR" in ctype or "TEXT" in ctype:
                    if "name" in cname.lower():
                        fields[cname] = f'Sample {_table_name_to_class_name(tname)} {row_idx + 1}'
                    elif "email" in cname.lower():
                        fields[cname] = f'user{row_idx + 1}@example.com'
                    elif "title" in cname.lower():
                        fields[cname] = f'Sample Title {row_idx + 1}'
                    elif "description" in cname.lower() or "content" in cname.lower():
                        fields[cname] = f'This is sample item {row_idx + 1} for {tname}.'
                    elif "status" in cname.lower():
                        fields[cname] = 'active'
                    elif "password" in cname.lower():
                        fields[cname] = f'password{row_idx + 1}'
                    elif "role" in cname.lower():
                        fields[cname] = 'user'
                    elif "phone" in cname.lower():
                        fields[cname] = f'555-0{100 + row_idx}'
                    elif "location" in cname.lower() or "address" in cname.lower():
                        fields[cname] = f'Sample Location {row_idx + 1}'
                    elif "username" in cname.lower():
                        fields[cname] = f'user{row_idx + 1}'
                    elif "url" in cname.lower() or "link" in cname.lower():
                        fields[cname] = f'https://example.com/item{row_idx + 1}'
                    elif "image" in cname.lower() or "photo" in cname.lower() or "avatar" in cname.lower():
                        fields[cname] = 'https://picsum.photos/200'
                    elif "token" in cname.lower() or "key" in cname.lower() or "secret" in cname.lower():
                        fields[cname] = f'random_token_{row_idx + 1}'
                    elif cname.endswith("_id") or cname.endswith("Id"):
                        fields[cname] = 1
                    elif nullable is False or "VARCHAR" in ctype:
                        fields[cname] = f'value_{row_idx + 1}'
                    else:
                        continue  # Skip nullable text columns without obvious purpose
                elif "BOOLEAN" in ctype:
                    fields[cname] = row_idx % 2 == 0
                elif "INT" in ctype or "BIGINT" in ctype or "SERIAL" in ctype:
                    if "age" in cname.lower():
                        fields[cname] = 20 + row_idx * 10
                    elif "year" in cname.lower():
                        fields[cname] = 2024 + row_idx
                    elif "qty" in cname.lower() or "quantity" in cname.lower() or "count" in cname.lower():
                        fields[cname] = 1 + row_idx * 2
                    elif "stock" in cname.lower():
                        fields[cname] = 10 + row_idx * 50
                    elif cname.endswith("_id") or cname.endswith("Id"):
                        fields[cname] = 1
                    else:
                        fields[cname] = row_idx * 100
                elif "FLOAT" in ctype or "DOUBLE" in ctype or "DECIMAL" in ctype or "NUMERIC" in ctype:
                    if "price" in cname.lower() or "cost" in cname.lower() or "amount" in cname.lower():
                        fields[cname] = round(9.99 + row_idx * 10, 2)
                    elif "total" in cname.lower():
                        fields[cname] = round(19.99 + row_idx * 25, 2)
                    elif "rate" in cname.lower() or "rating" in cname.lower():
                        fields[cname] = round(3.0 + row_idx * 0.5, 1)
                    else:
                        fields[cname] = round(1.0 + row_idx * 0.5, 2)
                elif "TIMESTAMP" in ctype or "DATE" in ctype or "DATETIME" in ctype:
                    fields[cname] = f'2026-06-0{5 - row_idx}T10:00:00'
                elif "UUID" in ctype:
                    fields[cname] = f'uuid-{row_idx + 1}-0000-0000-000000000000'

            if fields:
                row_parts = ", ".join(f"{k}={repr(v)}" for k, v in fields.items())
                lines.append(f"            db.add({class_name}({row_parts}))")

        lines.append(f"            db.commit()")
        lines.append("")

    lines.append("    except Exception:")
    lines.append("        db.rollback()  # Seed failure never blocks app startup")
    lines.append("    finally:")
    lines.append("        db.close()")
    lines.append("")

    return lines


def _generate_app(config: dict) -> str:
    """Generate FastAPI application with real CRUD routes from config."""
    app_name = config.get("metadata", {}).get("app_name", "app")
    tables = config.get("db_schema", {}).get("tables", [])
    endpoints = config.get("api_schema", {}).get("endpoints", [])
    business_rules = config.get("business_logic", {}).get("rules", [])

    # Collect entity names referenced by business rules
    biz_entities = set()
    for rule in business_rules:
        for ent in rule.get("entities_involved", []):
            biz_entities.add(ent.lower())

    pages = config.get("ui_schema", {}).get("pages", [])

    lines = [
        f'"""{app_name} -- FastAPI application generated from config."""',
        "from fastapi import FastAPI, HTTPException, Depends, Query, Form",
        "from fastapi.responses import HTMLResponse, RedirectResponse",
        "from typing import List, Optional",
        "from sqlalchemy import create_engine",
        "from sqlalchemy.orm import sessionmaker, Session",
        "from models import Base",
        "from auth import get_current_user, require_role",
        "import uvicorn",
        "import os",
        "import uuid",
        "from datetime import datetime",
        "",
        "# ---- Dynamic model imports ----",
    ]

    # Import model and schema classes for every table
    _junction_tables = config.get("_junction_tables", set())
    model_imports = []
    schema_imports = []
    for t in tables:
        if t.get("name") in _junction_tables:
            continue  # Junction tables use association Table, not models
        cn = _table_name_to_class_name(t["name"])
        model_imports.append(cn)
        schema_imports.extend([f"{cn}Create", f"{cn}Update", f"{cn}Response"])

    lines.append(f"from models import {', '.join(model_imports)}")
    lines.append(f"from schemas import {', '.join(schema_imports)}")

    # Import business logic validation dispatch
    if business_rules:
        lines.append("from business import validate_entity")

    lines.append("")
    lines.append("# ---- Database setup ----")
    lines.append('DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./app.db")')
    lines.append('engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})')
    lines.append("SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)")
    lines.append("Base.metadata.create_all(bind=engine)")
    lines.append("")
    lines.append("")
    lines.append("def get_db():")
    lines.append("    db = SessionLocal()")
    lines.append("    try:")
    lines.append("        yield db")
    lines.append("    finally:")
    lines.append("        db.close()")
    lines.append("")
    lines.append("")
    lines.append(f'app = FastAPI(title="{app_name}", version="1.0.0")')
    lines.append("")

    # Root route: redirect to first UI page, or Swagger if no pages
    if pages:
        first_route = pages[0].get("route", "/")
        if first_route != "/":
            lines.append("")
            lines.append('@app.get("/")')
            lines.append("async def root():")
            lines.append(f'    return RedirectResponse(url="{first_route}")')
    else:
        lines.append("")
        lines.append('@app.get("/")')
        lines.append("async def root():")
        lines.append('    return RedirectResponse(url="/docs")')
    lines.append("")

    # ------------------------------------------------------------------ #
    # Route generation
    # ------------------------------------------------------------------ #
    for ep in endpoints:
        method = ep.get("method", "GET").lower()
        # Sanitize path: strip query strings (they're not part of the route)
        path = ep["path"].split('?')[0]
        entity = ep.get("entity", "")

        # Build a safe function name from method + path
        # Sanitize path for use as Python identifier: strip query, replace special chars
        safe_path = path.split('?')[0]  # Strip query string
        safe_path = ''.join(c if c.isalnum() or c in '/{}' else '_' for c in safe_path)
        safe_path = safe_path.replace('/', '_').replace('{', '').replace('}', '')
        func_name = f"{method}_{safe_path}".strip('_')

        # Extract path parameters: e.g. "/contacts/{id}" -> ["id"]
        path_params = re.findall(r"\{(\w+)\}", path)

        # Determine CRUD operation type
        has_id_param = bool(path_params)
        is_list = method == "get" and not has_id_param
        is_get_one = method == "get" and has_id_param
        is_create = method == "post" and not has_id_param
        is_update = method in ("put", "patch") and has_id_param
        is_delete = method == "delete" and has_id_param

        # Map entity to model class & table
        table_name = _match_entity_to_table(entity, tables) if entity else None
        class_name = _table_name_to_class_name(table_name) if table_name else None

        # Determine schema names
        create_schema = f"{class_name}Create" if class_name else "dict"
        update_schema = f"{class_name}Update" if class_name else "dict"
        response_schema = f"{class_name}Response" if class_name else "dict"

        # Extract the path parameter name (usually "id" but could be "{contact_id}" etc.)
        id_param = path_params[0] if path_params else "id"

        # ------------------------------------------------------------------ #
        # Decorator
        # ------------------------------------------------------------------ #
        if is_delete:
            lines.append(f'@app.{method}("{path}", status_code=204)')
        else:
            lines.append(f'@app.{method}("{path}")')

        # ------------------------------------------------------------------ #
        # Function signature
        # ------------------------------------------------------------------ #
        # CRITICAL: Non-default params (path, body) MUST come before
        # default params (auth Depends, query, db Depends) for valid Python.
        non_default = []
        default = []

        # Path parameters (no defaults — must come first)
        # Infer Python type from the table's PK column (could be UUID/VARCHAR, not just int)
        pk_python_type = _infer_pk_type_for_entity(entity, tables)
        for pp in path_params:
            non_default.append(f"    {pp}: {pk_python_type},")

        # Body parameters (no defaults for POST/PUT — must come before Depends)
        if is_create:
            non_default.append(f"    item: {create_schema},")
        elif is_update:
            non_default.append(f"    item: {update_schema},")

        # Query params have defaults (skip/limit for list endpoints)
        if is_list:
            default.append("    skip: int = Query(0, ge=0),")
            default.append("    limit: int = Query(100, ge=1, le=1000),")

        # Auth dependencies (have defaults via Depends())
        if ep.get("auth_required"):
            roles = ep.get("roles", ["user"])
            default.append("    current_user = Depends(get_current_user),")
            default.append(f"    _ = Depends(require_role({roles})),")

        # DB dependency (has default via Depends())
        default.append("    db: Session = Depends(get_db),")

        sig_parts = non_default + default

        # Remove trailing comma from last sig part
        sig_str = "\n".join(sig_parts)

        lines.append(f"async def {func_name}(")
        lines.append(sig_str)
        lines.append("):")
        lines.append(f'    """{ep.get("description", "")}"""')

        # ------------------------------------------------------------------ #
        # CRUD body
        # ------------------------------------------------------------------ #
        indent = "    "

        if not class_name:
            # No matching entity -- generate a stub
            lines.append(f"{indent}# TODO: Implement logic for {path}")
            lines.append(f"{indent}return {{\"message\": \"{path} endpoint\"}}")
            lines.append("")
            continue

        if is_list:
            lines.append(f"{indent}# List all {table_name}")
            lines.append(f"{indent}items = db.query({class_name}).offset(skip).limit(limit).all()")
            lines.append(f"{indent}return items")
            lines.append("")

        elif is_get_one:
            lines.append(f"{indent}# Get one {table_name} by {id_param}")
            lines.append(f"{indent}item = db.query({class_name}).filter({class_name}.{id_param} == {id_param}).first()")
            lines.append(f"{indent}if not item:")
            lines.append(f'{indent}    raise HTTPException(status_code=404, detail="{class_name} not found")')
            lines.append(f"{indent}return item")
            lines.append("")

        elif is_create:
            lines.append(f"{indent}# Create a new {class_name}")
            if business_rules and entity.lower() in biz_entities:
                lines.append(f"{indent}# Apply business logic validations")
                lines.append(f"{indent}try:")
                lines.append(f'{indent}    validate_entity("{entity.lower()}", "create", item.model_dump())')
                lines.append(f"{indent}except HTTPException:")
                lines.append(f"{indent}    raise")
                lines.append("")
            lines.append(f"{indent}db_item = {class_name}(**item.model_dump())")
            lines.append(f"{indent}db.add(db_item)")
            lines.append(f"{indent}db.commit()")
            lines.append(f"{indent}db.refresh(db_item)")
            lines.append(f"{indent}return db_item")
            lines.append("")

        elif is_update:
            lines.append(f"{indent}# Update an existing {class_name}")
            lines.append(f"{indent}db_item = db.query({class_name}).filter({class_name}.{id_param} == {id_param}).first()")
            lines.append(f'{indent}if not db_item:')
            lines.append(f'{indent}    raise HTTPException(status_code=404, detail="{class_name} not found")')
            if business_rules and entity.lower() in biz_entities:
                lines.append("")
                lines.append(f"{indent}# Apply business logic validations")
                lines.append(f"{indent}try:")
                lines.append(f'{indent}    validate_entity("{entity.lower()}", "update", item.model_dump(exclude_unset=True))')
                lines.append(f"{indent}except HTTPException:")
                lines.append(f'{indent}    raise')
            lines.append("")
            lines.append(f"{indent}for key, value in item.model_dump(exclude_unset=True).items():")
            lines.append(f"{indent}    setattr(db_item, key, value)")
            lines.append(f"{indent}db.commit()")
            lines.append(f"{indent}db.refresh(db_item)")
            lines.append(f"{indent}return db_item")
            lines.append("")

        elif is_delete:
            lines.append(f"{indent}# Delete a {class_name}")
            lines.append(f"{indent}db_item = db.query({class_name}).filter({class_name}.{id_param} == {id_param}).first()")
            lines.append(f'{indent}if not db_item:')
            lines.append(f'{indent}    raise HTTPException(status_code=404, detail="{class_name} not found")')
            lines.append(f"{indent}db.delete(db_item)")
            lines.append(f"{indent}db.commit()")
            lines.append("")

        else:
            # Fallback for non-standard methods
            lines.append(f"{indent}# TODO: Implement custom logic for {method.upper()} {path}")
            lines.append(f"{indent}return {{\"message\": \"{method.upper()} {path}\"}}")
            lines.append("")

    # ------------------------------------------------------------------ #
    # HTML page routes (SERVER-SIDE RENDERED — no JS needed)
    # ------------------------------------------------------------------ #
    if pages:
        lines.append("")
        lines.append("# ---- UI Page Routes (server-rendered with live data) ----")
        for page in pages:
            page_name = page.get("name", "page")
            page_route = page.get("route", "/")
            safe_func = _safe_func(page)
            # Build server-side HTML renderer for this page
            _get_body, _extra_routes = _build_server_page(page, config)
            lines.append("")
            lines.append(f'@app.get("{page_route}", response_class=HTMLResponse)')
            lines.append(f"async def {safe_func}(db: Session = Depends(get_db)):")
            lines.append(f'    """Server-rendered {page_name} page with live data."""')
            for pline in _get_body:
                lines.append(f"    {pline}")
            lines.append("")
            # Extra routes (e.g., POST handler) go OUTSIDE the GET function
            for pline in _extra_routes:
                lines.append(pline)
            if _extra_routes:
                lines.append("")

    # ------------------------------------------------------------------ #
    # Database seeding (sample data so the app feels alive)
    # ------------------------------------------------------------------ #
    _seed_lines = _generate_seed_data(config)
    lines.extend(_seed_lines)

    # Startup
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    # Seed sample data on first run")
    lines.append("    seed_database()")
    lines.append('    port = int(os.environ.get("PORT", "8000"))')
    lines.append('    uvicorn.run(app, host="0.0.0.0", port=port)')
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schemas  (Pydantic models)
# ---------------------------------------------------------------------------

def _generate_schemas(config: dict) -> str:
    """Generate Pydantic BaseModel schemas for each DB table (Create / Update / Response)."""
    lines = [
        '"""Pydantic schemas -- generated from config."""',
        "from pydantic import BaseModel, Field",
        "from typing import Optional, List",
        "from datetime import datetime",
        "",
    ]

    _jt = config.get("_junction_tables", set())
    for table in config.get("db_schema", {}).get("tables", []):
        if table.get("name") in _jt:
            continue  # Junction tables don't need Pydantic schemas
        class_name = _table_name_to_class_name(table["name"])
        columns = table.get("columns", [])

        pk_col = None
        for col in columns:
            if col.get("primary_key"):
                pk_col = col
                break

        # ---- Create schema ----
        lines.append(f"class {class_name}Create(BaseModel):")
        create_fields = []
        for col in columns:
            if col.get("primary_key"):
                continue  # skip auto-generated PK
            py_type = _map_sql_type_to_python(col["type"])
            nullable = col.get("nullable") is False
            if nullable:
                create_fields.append(f"    {col['name']}: {py_type}")
            else:
                create_fields.append(f"    {col['name']}: Optional[{py_type}] = None")

        if create_fields:
            lines.extend(create_fields)
        else:
            lines.append("    pass")
        lines.append("")

        # ---- Update schema ----
        lines.append(f"class {class_name}Update(BaseModel):")
        update_fields = []
        for col in columns:
            if col.get("primary_key"):
                continue
            py_type = _map_sql_type_to_python(col["type"])
            update_fields.append(f"    {col['name']}: Optional[{py_type}] = None")

        if update_fields:
            lines.extend(update_fields)
        else:
            lines.append("    pass")
        lines.append("")

        # ---- Response schema ----
        lines.append(f"class {class_name}Response(BaseModel):")
        response_fields = []
        for col in columns:
            py_type = _map_sql_type_to_python(col["type"])
            response_fields.append(f"    {col['name']}: {py_type}")

        if response_fields:
            lines.append("    class Config:")
            lines.append("        from_attributes = True")
            lines.append("")
            lines.extend(response_fields)
        else:
            lines.append("    pass")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _generate_auth(config: dict) -> str:
    """Generate auth middleware from Auth schema."""
    auth = config.get("auth_schema", {})
    roles = auth.get("roles", [])
    methods = auth.get("auth_methods", ["jwt"])
    token_config = auth.get("token_config", {"type": "jwt", "expires_in": "24h"})

    lines = [
        '"""Authentication and Authorization -- generated from config."""',
        "from fastapi import HTTPException, Depends, Security",
        "from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials",
        "import jwt",
        "from datetime import datetime, timedelta",
        "",
        "security = HTTPBearer()",
        "",
        f"# Roles: {[r['name'] for r in roles]}",
        f"# Methods: {methods}",
        f"# Token: {token_config}",
        "",
        "# Access Control Matrix",
    ]

    # Access matrix
    access_matrix = auth.get("access_matrix", {})
    lines.append("ACCESS_MATRIX = " + _dict_to_python(access_matrix))
    lines.append("")

    # Generate a cryptographically random secret key as default fallback
    generated_secret = secrets.token_hex(32)
    lines.append(f"""
import os

JWT_SECRET = os.environ.get("JWT_SECRET", "{generated_secret}")

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    \"\"\"Validate JWT token and return current user.\"\"\"
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_role(allowed_roles: list):
    \"\"\"Dependency factory: require one of the given roles.\"\"\"
    async def role_checker(current_user = Depends(get_current_user)):
        user_role = current_user.get("role", "")
        if user_role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user_role
    return role_checker
""")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def _generate_business_logic(config: dict) -> str:
    """Generate business logic validation functions from business_logic.rules."""
    rules = config.get("business_logic", {}).get("rules", [])
    lines = [
        '"""Business logic validation -- generated from config."""',
        "from fastapi import HTTPException",
        "",
    ]

    # Build a mapping: entity -> list of (action, function_name)
    entity_validators = {}  # entity_lower -> [(action, func_name)]

    for rule in rules:
        rule_name = rule.get("name", "unnamed_rule")
        description = rule.get("description", "")
        entities = rule.get("entities_involved", [])
        trigger = rule.get("trigger", "").lower()

        # Determine which actions this rule applies to
        actions = []
        if "create" in trigger or "save" in trigger or trigger in ("", "before_insert"):
            actions.append("create")
        if "update" in trigger or "modify" in trigger or trigger in ("", "before_update"):
            actions.append("update")
        if "delete" in trigger or "remove" in trigger:
            actions.append("delete")
        if not actions:
            actions = ["create", "update"]  # default

        func_name = f"validate_{rule_name.lower().replace(' ', '_').replace('-', '_')}"

        lines.append(f"def {func_name}(data: dict) -> None:")
        lines.append(f'    """Validate: {description}"""')
        lines.append(f'    # TODO: Implement business rule "{rule_name}"')
        lines.append("    pass")
        lines.append("")

        # Register in the entity mapping
        for ent in entities:
            e_lower = ent.lower().strip()
            for action in actions:
                entity_validators.setdefault(e_lower, []).append((action, func_name))

    # Generate the dispatch function
    lines.append("")
    lines.append("def validate_entity(entity: str, action: str, data: dict) -> None:")
    lines.append('    """Run all applicable business rule validators for entity + action."""')
    lines.append("    _validators = {")

    # Organize by (entity, action) -> [func_names] (collect all, avoid duplicate keys)
    dispatch = {}  # (entity, action) -> [func_names]
    for e_lower, pairs in entity_validators.items():
        for action, func_name in pairs:
            dispatch.setdefault((e_lower, action), []).append(func_name)

    for (e_lower, action), funcs in sorted(dispatch.items()):
        # Emit a single key with a list of ALL validators for this (entity, action)
        func_names = ", ".join(sorted(funcs))
        lines.append(f'        ("{e_lower}", "{action}"): [{func_names}],')

    lines.append("    }")
    lines.append("")
    lines.append("    validators = _validators.get((entity, action), [])")
    lines.append("    for v in validators:")
    lines.append("        v(data)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Page template  (with JavaScript)
# ---------------------------------------------------------------------------

def _build_server_page(page: dict, config: dict) -> tuple:
    """Generate server-side Python code for a page route.
    Returns (get_body_lines, extra_routes) — get_body goes inside the GET handler,
    extra_routes (like POST handler) go at module level."""
    tables = config.get("db_schema", {}).get("tables", [])
    endpoints = config.get("api_schema", {}).get("endpoints", [])

    # Find which entity/table this page shows + the POST endpoint for forms
    entity = None
    table_name = None
    class_name = None
    post_endpoint = None  # The API endpoint for creating new items
    list_endpoint = None  # The API endpoint for listing items

    for section in page.get("layout", {}).get("sections", []):
        for comp in section.get("components", []):
            bind = comp.get("data_binding", "").strip()
            # Strip HTTP method prefix if present
            clean_bind = bind
            if ' ' in bind:
                parts = bind.split(' ', 1)
                if parts[0].upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                    clean_bind = parts[1]

            for ep in endpoints:
                if ep.get("path") == clean_bind:
                    entity = ep.get("entity", entity or "")
                    if ep.get("method", "").upper() == "POST":
                        post_endpoint = ep
                    elif ep.get("method", "").upper() == "GET":
                        list_endpoint = ep

            if comp.get("type") == "form":
                # Look for the POST endpoint matching this form
                action = comp.get("api_action", "POST").strip().upper()
                for ep in endpoints:
                    if ep.get("method", "").upper() == action and ep.get("path") == clean_bind:
                        post_endpoint = ep
                        entity = ep.get("entity", entity or "")

            if entity:
                break
        if entity:
            break

    if not list_endpoint:
        # Fallback: any GET endpoint for this entity
        for ep in endpoints:
            if ep.get("entity") == entity and ep.get("method", "").upper() == "GET":
                list_endpoint = ep
                break

    if entity:
        table_name = _match_entity_to_table(entity, tables)
        class_name = _table_name_to_class_name(table_name)

    # Build form fields from POST endpoint's request_schema (what API accepts)
    # Fall back to DB columns filtered by system cols
    form_fields = []  # List of {name, type, required, label}
    table_columns = []  # Columns to display in table (from DB schema)
    full_db_columns = []  # All non-PK DB columns (for defaults)

    if post_endpoint and post_endpoint.get("request_schema"):
        req_schema = post_endpoint["request_schema"]
        props = req_schema.get("properties", {})
        required = req_schema.get("required", [])
        for fname, finfo in props.items():
            ftype = finfo.get("type", "string") if isinstance(finfo, dict) else "string"
            form_fields.append({
                "name": fname, "type": ftype,
                "required": fname in required,
                "label": fname.replace("_", " ").title(),
            })

    # Get table columns for display
    _system_cols = {"id", "created_at", "updated_at", "deleted_at",
                   "type", "deleted_id", "user_id", "password", "token_hash"}
    all_cols = []
    pk_name = "id"
    if table_name:
        for t in tables:
            if t.get("name") == table_name:
                all_cols = t.get("columns", [])
                table_columns = [c for c in all_cols if not c.get("primary_key")]
                # full_db_columns: all columns including PK (needed for UUID/VARCHAR PKs)
                full_db_columns = [c for c in all_cols]
                # Find PK column name
                for c in all_cols:
                    if c.get("primary_key"):
                        pk_name = c.get("name", "id")
                        break
                if not form_fields:
                    # No API schema — derive form from DB columns
                    for c in all_cols:
                        if c.get("primary_key") or c.get("name") in _system_cols:
                            continue
                        ctype = c.get("type", "TEXT").upper()
                        ftype = "string"
                        if "INT" in ctype or "FLOAT" in ctype or "DECIMAL" in ctype:
                            ftype = "number"
                        elif "BOOLEAN" in ctype:
                            ftype = "boolean"
                        form_fields.append({
                            "name": c["name"],
                            "type": ftype,
                            "required": c.get("nullable") is False,
                            "label": c["name"].replace("_", " ").title(),
                        })
                else:
                    # API schema is sparse — add missing user-facing DB columns
                    existing_names = {f["name"] for f in form_fields}
                    for c in all_cols:
                        cname = c.get("name", "")
                        if cname in existing_names or cname in _system_cols or c.get("primary_key"):
                            continue
                        existing_names.add(cname)  # Prevent duplicates
                        ctype = c.get("type", "TEXT").upper()
                        ftype = "string"
                        if "INT" in ctype or "FLOAT" in ctype or "DECIMAL" in ctype:
                            ftype = "number"
                        elif "BOOLEAN" in ctype:
                            ftype = "boolean"
                        form_fields.append({
                            "name": cname, "type": ftype,
                            "required": c.get("nullable") is False,
                            "label": cname.replace("_", " ").title(),
                        })
                break

    # Final dedup: ensure no duplicate field names
    seen = set()
    deduped = []
    for f in form_fields:
        if f["name"] not in seen:
            seen.add(f["name"])
            deduped.append(f)
    form_fields = deduped

    page_route = page.get("route", "/").rstrip("/")
    route_base = page_route if page_route else ""  # For URL building: "" for root -> /path
    home_url = page_route or "/"  # For redirect: "/" for root

    py_lines = []

    # Find boolean columns for toggle feature (before table rendering uses them)
    _bool_cols = [c for c in all_cols if "BOOL" in c.get("type", "").upper() and c.get("name") not in _system_cols]

    # Build HTML with inline CSS
    html_parts = [
        'html = """<!DOCTYPE html>',
        '<html lang="en">',
        '<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<title>{page.get("name", "App")}</title>',
        '<style>',
        '  *{margin:0;padding:0;box-sizing:border-box}',
        '  body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f5;color:#333;padding:20px}',
        '  .container{max-width:900px;margin:0 auto}',
        '  h1{color:#1a1a2e;margin-bottom:20px;padding-bottom:10px;border-bottom:2px solid #6366f1}',
        '  table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)}',
        '  th{background:#6366f1;color:#fff;padding:12px;text-align:left;font-size:14px}',
        '  td{padding:10px 12px;border-bottom:1px solid #eee;font-size:14px}',
        '  tr:hover{background:#f8f8ff}',
        '  .form-section{background:#fff;padding:20px;border-radius:8px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.1)}',
        '  .form-section h3{margin-bottom:15px;color:#6366f1}',
        '  .form-group{margin-bottom:10px}',
        '  label{display:block;font-weight:600;margin-bottom:4px;font-size:13px;color:#555}',
        '  input,textarea,select{width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:4px;font-size:14px}',
        '  input:focus,textarea:focus{outline:none;border-color:#6366f1}',
        '  .btn{background:#6366f1;color:#fff;border:none;padding:10px 24px;border-radius:4px;cursor:pointer;font-size:14px;font-weight:600}',
        '  .btn:hover{background:#4f46e5}',
        '  .badge{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}',
        '  .badge-true{background:#d4edda;color:#155724} .badge-false{background:#f8d7da;color:#721c24}',
        '  .empty{text-align:center;padding:40px;color:#999;font-size:16px}',
        '  .nav{display:flex;gap:10px;margin-bottom:20px}',
        '  .nav a{color:#6366f1;text-decoration:none;padding:8px 16px;border-radius:4px;background:#eef2ff}',
        '  .nav a:hover{background:#dde4ff}',
        '</style>',
        '</head>',
        '<body><div class="container">',
        f'<div class="nav"><a href="/">Home</a> <a href="/docs">API Docs (Swagger)</a></div>',
        f'<h1>{page.get("name", "App")}</h1>',
        '"""',
    ]

    # Build the data table using table_columns
    if class_name and table_columns:
        html_parts.append('')
        html_parts.append(f'# Query {table_name} from database')
        html_parts.append(f'items = db.query({class_name}).all()')
        html_parts.append('if items:')
        html_parts.append('    html += """<table><thead><tr>"""')
        for col in table_columns:
            html_parts.append(f'    html += """<th>{col["name"].replace("_", " ").title()}</th>"""')
        html_parts.append('    html += """<th>Actions</th></tr></thead><tbody>"""')
        html_parts.append('    for item in items:')
        html_parts.append('        html += """<tr>"""')
        for col in table_columns:
            cname = col["name"]
            ctype = col.get("type", "").upper()
            if "BOOLEAN" in ctype:
                html_parts.append(f'        html += f"""<td><span class="badge badge-{{str(item.{cname}).lower()}}">{{item.{cname}}}</span></td>"""')
            else:
                html_parts.append(f'        html += f"""<td>{{item.{cname} if item.{cname} is not None else ""}}</td>"""')
        # Action links: Edit + Toggle + Delete
        confirm_js = "return confirm(&#39;Delete?&#39;)"
        actions_html = f'<a href=\"{route_base}/_edit/{{item.{pk_name}}}\" style=\"color:#3b82f6;text-decoration:none;margin-right:8px\">Edit</a>'
        if _bool_cols:
            actions_html += f' <a href=\"{route_base}/_toggle/{{item.{pk_name}}}\" style=\"color:var(--green);text-decoration:none;margin-right:8px\">Toggle</a>'
        actions_html += f' <a href=\"{route_base}/_delete/{{item.{pk_name}}}\" style=\"color:var(--red);text-decoration:none\" onclick=\"{confirm_js}\">Delete</a>'
        html_parts.append(f"        html += f'<td>{actions_html}</td>'")
        html_parts.append('        html += """</tr>"""')
        html_parts.append('    html += """</tbody></table>"""')
        html_parts.append('else:')
        html_parts.append(f'    html += """<div class="empty">No {table_name} yet. Create one below!</div>"""')

    # Build the form using form_fields
    if class_name and form_fields:
        html_parts.append('')
        html_parts.append(f'html += """<div class="form-section"><h3>Add New {_singularize(class_name) if class_name else "Item"}</h3>"""')
        html_parts.append(f'html += """<form method="post" action="{route_base}">"""')
        for f in form_fields:
            fname = f["name"]
            ftype = f["type"]
            label = f["label"]
            is_req = f["required"]
            if ftype == "textarea":
                html_parts.append(f'html += """<div class="form-group"><label>{label}</label><textarea name="{fname}" rows="3"></textarea></div>"""')
            elif ftype in ("boolean", "checkbox"):
                html_parts.append(f'html += """<div class="form-group"><label><input type="checkbox" name="{fname}" value="true"> {label}</label></div>"""')
            else:
                req_attr = " required" if is_req else ""
                inp_type = "number" if ftype in ("number", "integer", "float") else "text"
                html_parts.append(f'html += """<div class="form-group"><label>{label}</label><input type="{inp_type}" name="{fname}"{req_attr}></div>"""')
        html_parts.append('html += """<button type="submit" class="btn">Submit</button>"""')
        html_parts.append('html += """</form></div>"""')

    # Close HTML
    html_parts.append('html += """</div></body></html>"""')
    html_parts.append('return HTMLResponse(html)')

    # Generate DELETE route
    if class_name and table_columns:
        _pk_col = next((c for c in all_cols if c.get("primary_key")), {"type": "INTEGER"})
        pk_type = "str" if any(t in _pk_col.get("type", "").upper() for t in ("VARCHAR", "UUID", "CHAR", "TEXT")) else "int"
        py_lines.append('')
        py_lines.append(f'@app.get("{route_base}/_delete/{{{pk_name}}}")')
        py_lines.append(f"async def delete_{_safe_func(page)}({pk_name}: {pk_type}, db: Session = Depends(get_db)):")
        py_lines.append(f"    item = db.query({class_name}).filter({class_name}.{pk_name} == {pk_name}).first()")
        py_lines.append(f"    if item:")
        py_lines.append(f"        db.delete(item)")
        py_lines.append(f"        db.commit()")
        py_lines.append(f'    return RedirectResponse(url="{home_url}", status_code=303)')

    # Generate EDIT routes (GET shows prefilled form, POST handles update)
    if class_name and form_fields and table_columns:
        _pk_col = next((c for c in all_cols if c.get("primary_key")), {"type": "INTEGER"})
        pk_type = "str" if any(t in _pk_col.get("type", "").upper() for t in ("VARCHAR", "UUID", "CHAR", "TEXT")) else "int"

        # GET: show edit form
        py_lines.append('')
        py_lines.append(f'@app.get("{route_base}/_edit/{{{pk_name}}}", response_class=HTMLResponse)')
        py_lines.append(f"async def edit_get_{_safe_func(page)}({pk_name}: {pk_type}, db: Session = Depends(get_db)):")
        py_lines.append(f"    item = db.query({class_name}).filter({class_name}.{pk_name} == {pk_name}).first()")
        py_lines.append(f"    if not item:")
        py_lines.append(f'        return HTMLResponse("<h2>Not found</h2>", status_code=404)')
        py_lines.append(f'    html = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Edit</title>"""')
        py_lines.append(f'    html += """<style>*{{font-family:sans-serif}}body{{background:#f5f5f5;padding:20px}}.container{{max-width:600px;margin:0 auto;background:#fff;padding:20px;border-radius:8px}}h2{{color:#6366f1}}label{{display:block;font-weight:600;margin:8px 0 4px;font-size:13px}}input,textarea{{width:100%;padding:8px;border:1px solid #ddd;border-radius:4px}}button{{background:#6366f1;color:#fff;border:none;padding:10px 24px;border-radius:4px;cursor:pointer;margin-top:12px}}</style>"""')
        py_lines.append(f'    html += """</head><body><div class="container"><h2>Edit Item</h2>"""')
        py_lines.append(f"    html += f'<form method=\"post\" action=\"{route_base}/_edit/{{item.{pk_name}}}\">'")
        for f in form_fields:
            fname = f["name"]
            label = f["label"]
            ftype = f["type"]
            if ftype in ("boolean", "checkbox"):
                py_lines.append(f"    html += f'<label><input type=\"checkbox\" name=\"{fname}\" value=\"true\" {{\"checked\" if item.{fname} else \"\"}}> {label}</label><br>'")
            elif ftype == "textarea":
                py_lines.append(f"    html += f'<label>{label}</label><textarea name=\"{fname}\" rows=\"3\">{{item.{fname} or \"\"}}</textarea>'")
            elif ftype in ("number", "integer", "float"):
                py_lines.append(f"    html += f'<label>{label}</label><input type=\"number\" name=\"{fname}\" value=\"{{item.{fname} or 0}}\">'")
            else:
                py_lines.append(f"    html += f'<label>{label}</label><input type=\"text\" name=\"{fname}\" value=\"{{item.{fname} or \"\"}}\">'")
        py_lines.append(f"    html += '<button type=\"submit\">Save Changes</button>'")
        py_lines.append(f"    html += f' <a href=\"{page.get('route', '/')}\">Cancel</a>'")
        py_lines.append(f'    html += """</form></div></body></html>"""')
        py_lines.append(f'    return HTMLResponse(html)')

        # POST: handle edit submission
        py_lines.append('')
        py_lines.append(f'@app.post("{route_base}/_edit/{{{pk_name}}}")')
        py_lines.append(f"async def edit_post_{_safe_func(page)}({pk_name}: {pk_type}, db: Session = Depends(get_db),")
        for f in form_fields:
            py_lines.append(f"    {f['name']}: Optional[str] = Form(None),")
        py_lines.append("):")
        py_lines.append(f"    item = db.query({class_name}).filter({class_name}.{pk_name} == {pk_name}).first()")
        py_lines.append(f"    if not item:")
        py_lines.append(f'        return HTMLResponse("<h2>Not found</h2>", status_code=404)')
        py_lines.append(f"    try:")
        for f in form_fields:
            fname = f["name"]
            ftype = f["type"]
            py_lines.append(f"        if {fname} is not None and str({fname}).strip() != '':")
            if ftype in ("boolean", "checkbox"):
                py_lines.append(f"            setattr(item, '{fname}', str({fname}).lower() in ('true', '1', 'on'))")
            elif ftype in ("number", "integer", "float"):
                py_lines.append(f"            try: setattr(item, '{fname}', float({fname}))")
                py_lines.append(f"            except: pass")
            else:
                py_lines.append(f"            setattr(item, '{fname}', str({fname}).strip())")
        py_lines.append(f"        db.commit()")
        py_lines.append(f'        return RedirectResponse(url="{page.get("route", "/")}", status_code=303)')
        py_lines.append(f"    except Exception as e:")
        py_lines.append(f"        db.rollback()")
        py_lines.append(f"        return HTMLResponse(f'<h2>Error: {{e}}</h2><a href=\"{page.get('route', '/')}\">Back</a>', status_code=400)")

    # Generate TOGGLE route (flips a boolean column like is_completed)
    if class_name and _bool_cols and table_columns:
        toggle_col = _bool_cols[0]["name"]
        _pk_col = next((c for c in all_cols if c.get("primary_key")), {"type": "INTEGER"})
        pk_type = "str" if any(t in _pk_col.get("type", "").upper() for t in ("VARCHAR", "UUID", "CHAR", "TEXT")) else "int"
        py_lines.append('')
        py_lines.append(f'@app.get("{route_base}/_toggle/{{{pk_name}}}")')
        py_lines.append(f"async def toggle_{_safe_func(page)}({pk_name}: {pk_type}, db: Session = Depends(get_db)):")
        py_lines.append(f"    item = db.query({class_name}).filter({class_name}.{pk_name} == {pk_name}).first()")
        py_lines.append(f"    if item:")
        py_lines.append(f"        item.{toggle_col} = not item.{toggle_col}")
        py_lines.append(f"        db.commit()")
        py_lines.append(f'    return RedirectResponse(url="{page.get("route", "/")}", status_code=303)')

    # Generate POST handler using form_fields + auto-fill missing DB columns
    if class_name and form_fields:
        form_col_names = {f["name"] for f in form_fields}
        py_lines.append('')
        py_lines.append(f'@app.post("{page.get("route", "/")}")')
        py_lines.append(f"async def post_{_safe_func(page)}(")
        py_lines.append(f"    db: Session = Depends(get_db),")
        for f in form_fields:
            py_lines.append(f"    {f['name']}: Optional[str] = Form(None),")
        py_lines.append("):")
        py_lines.append(f'    """Handle form submission for {page.get("name")}."""')
        py_lines.append(f"    try:")
        py_lines.append(f"        kwargs = {{}}")
        # Process form fields
        for f in form_fields:
            fname = f["name"]
            ftype = f["type"]
            is_req = f["required"]
            py_lines.append(f"        if {fname} is not None and str({fname}).strip() != '':")
            if ftype in ("boolean", "checkbox"):
                py_lines.append(f"            kwargs['{fname}'] = str({fname}).lower() in ('true', '1', 'on')")
            elif ftype in ("number", "integer", "float"):
                py_lines.append(f"            try: kwargs['{fname}'] = float({fname})")
                py_lines.append(f"            except: pass")
            else:
                py_lines.append(f"            kwargs['{fname}'] = str({fname}).strip()")
            # Required fields: provide default if empty
            if is_req:
                if ftype in ("boolean", "checkbox"):
                    py_lines.append(f"        else: kwargs['{fname}'] = False")
                elif ftype in ("number", "integer", "float"):
                    py_lines.append(f"        else: kwargs['{fname}'] = 0")
                else:
                    py_lines.append(f"        else: kwargs['{fname}'] = ''")
        # Auto-fill any NOT NULL DB columns not in the form (including VARCHAR/UUID PKs)
        # PK columns are always implicitly NOT NULL even if nullable isn't set
        for col in full_db_columns:
            cname = col.get("name", "")
            if cname in form_col_names:
                continue
            is_pk = col.get("primary_key", False)
            ct = col.get("type", "").upper()
            is_required = is_pk or (col.get("nullable") is False)

            if is_pk:
                # Auto-generate primary key for non-integer PKs (e.g., UUID, VARCHAR)
                if "UUID" in ct or "VARCHAR" in ct or "CHAR" in ct or "TEXT" in ct:
                    py_lines.append(f"        kwargs.setdefault('{cname}', str(uuid.uuid4()))")
                # INTEGER/SERIAL/BIGINT PK: skip (auto-increment handles it)
            elif is_required:
                if "TIMESTAMP" in ct or "DATE" in ct:
                    py_lines.append(f"        kwargs.setdefault('{cname}', datetime.now())")
                elif "BOOLEAN" in ct:
                    py_lines.append(f"        kwargs.setdefault('{cname}', False)")
                elif "INT" in ct or "FLOAT" in ct or "DECIMAL" in ct or "NUMERIC" in ct:
                    py_lines.append(f"        kwargs.setdefault('{cname}', 0)")
                else:
                    py_lines.append(f"        kwargs.setdefault('{cname}', '')")
        py_lines.append(f"        item = {class_name}(**kwargs)")
        py_lines.append(f"        db.add(item)")
        py_lines.append(f"        db.commit()")
        py_lines.append(f"        return RedirectResponse(url=\"{page.get('route', '/')}\", status_code=303)")
        py_lines.append(f"    except Exception as e:")
        py_lines.append(f"        db.rollback()")
        py_lines.append(f"        return HTMLResponse(f'<h2>Error: {{e}}</h2><a href=\"{page.get('route', '/')}\">Back</a>', status_code=400)")

    return html_parts, py_lines


def _safe_func(page):
    import re
    name = page.get('name', 'page').lower()
    name = re.sub(r'[^a-z0-9_]', '_', name)  # Replace any non-alphanumeric with _
    name = re.sub(r'_+', '_', name)  # Collapse multiple underscores
    return f"page_{name.strip('_')}"


def _gen_form_fields(lines: list, comp: dict, page: dict, config: dict):
    """Generate HTML input fields for a form based on the matching API endpoint's request_schema."""
    data_bind = comp.get("data_binding", "").strip()
    # Strip HTTP method prefix to get the API path
    bind_path = data_bind
    if ' ' in bind_path:
        parts = bind_path.split(' ', 1)
        if parts[0].upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            bind_path = parts[1]

    # Find matching API endpoint
    endpoints = config.get("api_schema", {}).get("endpoints", [])
    matched_ep = None
    for ep in endpoints:
        if ep.get("path", "") == bind_path:
            matched_ep = ep
            break

    if not matched_ep:
        lines.append("            <!-- No matching API endpoint found for form fields -->")
        return

    request_schema = matched_ep.get("request_schema") or {}
    props = request_schema.get("properties", {})
    required_fields = request_schema.get("required", [])

    if not props:
        # Try to infer fields from DB table
        entity = matched_ep.get("entity", "")
        tables = config.get("db_schema", {}).get("tables", [])
        table_name = _match_entity_to_table(entity, tables)
        for t in tables:
            if t.get("name") == table_name:
                for col in t.get("columns", []):
                    cname = col.get("name", "")
                    if cname in ("id", "created_at", "updated_at"):
                        continue
                    ctype = col.get("type", "").upper()
                    input_type = "number" if any(t in ctype for t in ("INT", "FLOAT", "DECIMAL", "NUMERIC")) else "text"
                    is_req = col.get("nullable") is False and not col.get("primary_key")
                    label = cname.replace("_", " ").title()
                    req_attr = " required" if is_req else ""
                    lines.append(f'            <label>{label}</label>')
                    lines.append(f'            <input type="{input_type}" name="{cname}" class="form-control mb-2"{req_attr}/>')
                break
    else:
        for field_name, field_info in props.items():
            if isinstance(field_info, dict):
                ftype = field_info.get("type", "text")
            else:
                ftype = "text" if field_info not in ("number", "boolean") else field_info
            input_type = "text"
            if ftype in ("integer", "number", "float"):
                input_type = "number"
            elif ftype == "boolean":
                input_type = "checkbox"
            is_req = " required" if field_name in required_fields else ""
            label = field_name.replace("_", " ").title()
            lines.append(f'            <label>{label}</label>')
            lines.append(f'            <input type="{input_type}" name="{field_name}" class="form-control mb-2"{is_req}/>')


def _generate_page_template(page: dict, config: dict) -> str:
    """Generate an HTML page template from a UI page schema."""
    lines = [
        "<!DOCTYPE html>",
        f"<!-- {page['name']} -- generated from config -->",
        '<html lang="en">',
        "<head>",
        f"    <title>{page['name']} -- {config.get('metadata', {}).get('app_name', 'App')}</title>",
        '    <meta charset="UTF-8">',
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">',
        "</head>",
        "<body>",
        f"    <h1>{page['name']}</h1>",
    ]

    for section in page.get("layout", {}).get("sections", []):
        lines.append(f"    <!-- Section: {section['name']} -->")
        lines.append(f'    <section class="{section["name"].lower().replace(" ", "-")}">')
        for comp in section.get("components", []):
            comp_type = comp.get("type", "div")
            data_bind = comp.get("data_binding", "")
            if comp_type == "table":
                lines.append(f'        <table data-bind="{data_bind}" class="table">')
                lines.append('            <thead><tr><th>Loading...</th></tr></thead>')
                lines.append("            <tbody></tbody>")
                lines.append("        </table>")
            elif comp_type == "form":
                action_method = comp.get("api_action", "POST").strip().upper()
                lines.append(f'        <form data-bind="{data_bind}" data-action="{action_method}" class="p-3 border rounded">')
                # Generate input fields from the matching API endpoint's request_schema
                _gen_form_fields(lines, comp, page, config)
                lines.append("            <button type='submit' class='btn btn-primary'>Submit</button>")
                lines.append("        </form>")
            elif comp_type == "card":
                lines.append(f'        <div class="card" data-bind="{data_bind}">')
                lines.append('            <div class="card-body">')
                lines.append(f'                <h5 class="card-title">{comp.get("props", {}).get("title", "")}</h5>')
                lines.append('            </div>')
                lines.append("        </div>")
            elif comp_type == "chart":
                lines.append(f'        <div class="chart-container" data-bind="{data_bind}">')
                lines.append('            <canvas></canvas>')
                lines.append("        </div>")
            else:
                lines.append(f'        <div class="component {comp_type}" data-bind="{data_bind}">')
                lines.append(f"            <!-- {comp_type} component -->")
                lines.append("        </div>")
        lines.append("    </section>")

    # ---- JavaScript: data-binding, form actions, JWT auth ----
    lines.append("")
    lines.append("    <script>")
    lines.append("    (function() {")
    lines.append("        'use strict';")
    lines.append("")
    lines.append("        var API_BASE = '';")
    lines.append("        var TOKEN_KEY = 'auth_token';")
    lines.append("")
    lines.append("        function getToken() {")
    lines.append("            return localStorage.getItem(TOKEN_KEY);")
    lines.append("        }")
    lines.append("")
    lines.append("        function authHeaders() {")
    lines.append("            var token = getToken();")
    lines.append("            if (token) {")
    lines.append("                return { 'Authorization': 'Bearer ' + token };")
    lines.append("            }")
    lines.append("            return {};")
    lines.append("        }")
    lines.append("")
    lines.append("        function showError(el, message) {")
    lines.append("            el.innerHTML = '<div class=\"alert alert-danger\">' + message + '</div>';")
    lines.append("        }")
    lines.append("")
    lines.append("        function showLoading(el) {")
    lines.append("            el.innerHTML = '<div class=\"text-center\"><div class=\"spinner-border\" role=\"status\"><span class=\"visually-hidden\">Loading...</span></div></div>';")
    lines.append("        }")
    lines.append("")
    lines.append("        function showEmpty(el, message) {")
    lines.append("            el.innerHTML = '<div class=\"text-muted text-center p-3\">' + (message || 'No data available') + '</div>';")
    lines.append("        }")
    lines.append("")
    lines.append("        function renderTable(table, data) {")
    lines.append("            if (!data || data.length === 0) {")
    lines.append("                showEmpty(table, 'No records found.');")
    lines.append("                return;")
    lines.append("            }")
    lines.append("            var headers = Object.keys(data[0]);")
    lines.append("            var thead = table.querySelector('thead tr');")
    lines.append("            thead.innerHTML = headers.map(function(h) {")
    lines.append("                return '<th>' + h.replace(/_/g, ' ') + '</th>';")
    lines.append("            }).join('');")
    lines.append("            var tbody = table.querySelector('tbody');")
    lines.append("            tbody.innerHTML = data.map(function(row) {")
    lines.append("                return '<tr>' + headers.map(function(h) {")
    lines.append("                    var val = row[h];")
    lines.append("                    if (val === null || val === undefined) return '<td></td>';")
    lines.append("                    if (typeof val === 'object') return '<td>' + JSON.stringify(val) + '</td>';")
    lines.append("                    return '<td>' + val + '</td>';")
    lines.append("                }).join('') + '</tr>';")
    lines.append("            }).join('');")
    lines.append("        }")
    lines.append("")
    lines.append("        function renderCard(element, data) {")
    lines.append("            var body = element.querySelector('.card-body') || element;")
    lines.append("            if (data === null || data === undefined) {")
    lines.append("                showEmpty(body);")
    lines.append("                return;")
    lines.append("            }")
    lines.append("            if (Array.isArray(data)) {")
    lines.append("                if (data.length === 0) { showEmpty(body); return; }")
    lines.append("                var html = '<ul class=\"list-group list-group-flush\">';")
    lines.append("                data.forEach(function(item) {")
    lines.append("                    if (typeof item === 'object') {")
    lines.append("                        html += '<li class=\"list-group-item\"><pre>' + JSON.stringify(item, null, 2) + '</pre></li>';")
    lines.append("                    } else {")
    lines.append("                        html += '<li class=\"list-group-item\">' + item + '</li>';")
    lines.append("                    }")
    lines.append("                });")
    lines.append("                html += '</ul>';")
    lines.append("                body.innerHTML = html;")
    lines.append("            } else if (typeof data === 'object') {")
    lines.append("                body.innerHTML = Object.keys(data).map(function(k) {")
    lines.append("                    var v = data[k];")
    lines.append("                    if (v === null || v === undefined) v = '';")
    lines.append("                    if (typeof v === 'object') v = JSON.stringify(v);")
    lines.append("                    return '<p><strong>' + k.replace(/_/g, ' ') + ':</strong> ' + v + '</p>';")
    lines.append("                }).join('');")
    lines.append("            } else {")
    lines.append("                body.innerHTML = '<p class=\"card-text\">' + data + '</p>';")
    lines.append("            }")
    lines.append("        }")
    lines.append("")
    lines.append("        async function fetchData(bindPath, element) {")
    lines.append("            showLoading(element);")
    lines.append("            try {")
    lines.append("                // Strip HTTP method prefix if present ('GET /api/tasks' -> '/api/tasks')")
    lines.append("                var url = bindPath.replace(/^(GET|POST|PUT|PATCH|DELETE)\\s+/i, '');")
    lines.append("                var response = await fetch(API_BASE + url, {")
    lines.append("                    headers: Object.assign({}, authHeaders())")
    lines.append("                });")
    lines.append("                if (!response.ok) throw new Error('HTTP ' + response.status);")
    lines.append("                var data = await response.json();")
    lines.append("                if (element.tagName === 'TABLE') {")
    lines.append("                    renderTable(element, Array.isArray(data) ? data : [data]);")
    lines.append("                } else {")
    lines.append("                    renderCard(element, data);")
    lines.append("                }")
    lines.append("            } catch (err) {")
    lines.append("                showError(element, 'Failed to load data: ' + err.message);")
    lines.append("            }")
    lines.append("        }")
    lines.append("")
    lines.append("        async function submitForm(form) {")
    lines.append("            var bindPath = form.getAttribute('data-bind');")
    lines.append("            var httpMethod = form.getAttribute('data-action') || 'POST';")
    lines.append("            if (!bindPath) return;")
    lines.append("            // Strip HTTP method prefix if present (e.g., 'POST /api/tasks' -> '/api/tasks')")
    lines.append("            var url = bindPath.replace(/^(GET|POST|PUT|PATCH|DELETE)\\s+/i, '');")
    lines.append("            // Keep method from data-action, strip whitespace prefix if any")
    lines.append("            var method = httpMethod.replace(/^(GET|POST|PUT|PATCH|DELETE)\\s+.*$/i, '$1').toUpperCase();")
    lines.append("            if (!['GET','POST','PUT','PATCH','DELETE'].includes(method)) method = 'POST';")
    lines.append("            var formData = new FormData(form);")
    lines.append("            var data = {};")
    lines.append("            formData.forEach(function(value, key) { data[key] = value; });")
    lines.append("            try {")
    lines.append("                var response = await fetch(API_BASE + url, {")
    lines.append("                    method: method,")
    lines.append("                    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),")
    lines.append("                    body: JSON.stringify(data)")
    lines.append("                });")
    lines.append("                if (!response.ok) throw new Error('HTTP ' + response.status);")
    lines.append("                var result = await response.json();")
    lines.append("                // Refresh all data-bound elements on success")
    lines.append("                document.querySelectorAll('[data-bind]').forEach(function(el) {")
    lines.append("                    var bindPath = el.getAttribute('data-bind');")
    lines.append("                    if (bindPath) fetchData(bindPath, el);")
    lines.append("                });")
    lines.append("            } catch (err) {")
    lines.append("                alert('Submission failed: ' + err.message);")
    lines.append("            }")
    lines.append("        }")
    lines.append("")
    lines.append("        document.addEventListener('DOMContentLoaded', function() {")
    lines.append("            // Wire up data-bound elements")
    lines.append("            document.querySelectorAll('[data-bind]').forEach(function(el) {")
    lines.append("                var bindPath = el.getAttribute('data-bind');")
    lines.append("                if (bindPath) fetchData(bindPath, el);")
    lines.append("            });")
    lines.append("")
    lines.append("            // Wire up forms with data-action")
    lines.append("            document.querySelectorAll('form[data-action]').forEach(function(form) {")
    lines.append("                form.addEventListener('submit', function(e) {")
    lines.append("                    e.preventDefault();")
    lines.append("                    submitForm(this);")
    lines.append("                });")
    lines.append("            });")
    lines.append("")
    lines.append("            // Token from URL query parameter (login callback)")
    lines.append("            var params = new URLSearchParams(window.location.search);")
    lines.append("            var token = params.get('token');")
    lines.append("            if (token) {")
    lines.append("                localStorage.setItem(TOKEN_KEY, token);")
    lines.append("                window.history.replaceState({}, document.title, window.location.pathname);")
    lines.append("            }")
    lines.append("        });")
    lines.append("    })();")
    lines.append("    </script>")
    lines.append("")

    lines.extend([
        '    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>',
        "</body>",
        "</html>",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------

def _generate_requirements(config: dict) -> str:
    """Generate Python requirements for the generated app.
    Uses flexible versions so pip can resolve compatible wheels for the host Python."""
    return "\n".join([
        "# Generated app requirements",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "sqlalchemy>=2.0.0",
        "pyjwt>=2.8.0",
        "python-dotenv>=1.0.0",
        "pydantic>=2.5.0",
        "python-multipart>=0.0.9",
    ])


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------

def _generate_dockerfile(config: dict) -> str:
    """Generate Dockerfile for containerized deployment of the generated app."""
    lines = [
        "FROM python:3.12-slim",
        "",
        "WORKDIR /app",
        "",
        "# Install Python dependencies",
        "COPY requirements.txt .",
        "RUN pip install --no-cache-dir -r requirements.txt",
        "",
        "# Copy application source",
        "COPY . .",
        "",
        "EXPOSE 8000",
        "",
        'CMD ["python", "app.py"]',
    ]
    return "\n".join(lines)
