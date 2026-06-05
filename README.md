# App Compiler

A multi-stage pipeline that transforms natural language app descriptions into validated, executable application configurations. Designed as a compiler-like system with strict schema enforcement, cross-layer validation, and targeted repair — not a single-prompt wrapper.

---

## How It Works

The system processes input through four sequential stages, each with a defined JSON Schema contract. Every stage output is validated before the next begins. If the final output fails validation, a repair engine applies surgical fixes rather than regenerating from scratch.

```
User Prompt ("Build a CRM with login, contacts, dashboard...")
    |
    v
Stage 1: Intent Extraction  -->  Intent IR
    |                             (entities, features, roles, constraints)
    |                             (ambiguity flags, clarification questions)
    v
Stage 2: System Design       -->  Architecture IR
    |                             (entity schemas, pages, API endpoints)
    |                             (auth rules, business logic, relations)
    v
Stage 3: Schema Generation   -->  Complete Config
    |                             (UI, API, DB, Auth, Business Logic)
    |                             (5 sub-schemas generated in parallel)
    v
Stage 4: Refinement          -->  Validated Config
    |                             (7-layer validation)
    |                             (targeted repair, max 3 passes)
    v
Code Generator               -->  schema.sql, app.py, models.py, auth.py, templates/
```

Each arrow represents a data contract. Nothing passes through without matching its schema.

---

## Pipeline Stages

### Stage 1: Intent Extraction
Parses raw natural language into structured Intent IR. Detects ambiguity (`is_vague`), conflicts (`has_conflicts`), and incompleteness (`is_incomplete`). Generates clarification questions for blocking issues (strict mode) or documents assumptions (fast mode).

**Input:** Free-form text
**Output:** Intent IR — structured JSON with entities, features, roles, constraints, ambiguity analysis

### Stage 2: System Design
Converts Intent IR into a complete Architecture IR. Defines every entity with typed fields and relationships, every page with components and data bindings, every API endpoint with methods and auth requirements, every role with granular permissions, and every business rule with conditions and actions.

**Input:** Intent IR
**Output:** Architecture IR — entities, pages, API endpoints, auth roles, business rules

### Stage 3: Schema Generation
Generates five complete schemas from the Architecture IR. DB schema is generated first, then API schema gets DB context for consistency. UI, Auth, and Business Logic schemas are generated in parallel for speed.

**Input:** Architecture IR
**Output:** Complete config with ui_schema, api_schema, db_schema, auth_schema, business_logic

### Stage 4: Refinement
Runs 7-layer validation on the complete config. For each error found, dispatches to one of 12 targeted repair strategies. Errors are deduplicated — each unique error is attempted once. Repairs are surgical (add a missing column, remove a broken FK, fix a type) — never full regeneration. Exits early if no progress is made.

**Input:** Complete config + Architecture IR
**Output:** Validated config with repair log and validation status

---

## Validation Engine

Seven sequential layers, each catching a specific class of error:

| Layer | What It Checks | Example Error |
|-------|---------------|---------------|
| 1. JSON Validity | Output is valid, serializable JSON | `json.dumps()` raises TypeError |
| 2. Required Fields | All mandatory keys present per JSON Schema | `metadata.app_name` is missing |
| 3. Type Safety | DB column types are valid SQL types; HTTP methods are valid | Column `age` has type `REVENUE_TYPE` |
| 4. Reference Integrity | All foreign keys, entity references, and data bindings resolve to real entities | FK references `departments` but no such table exists |
| 5. Cross-Layer Consistency | API response fields exist in DB columns; UI bindings map to real API paths; auth protects declared endpoints | API returns `priority` field not in DB table |
| 6. Logical Consistency | No circular FK chains; access matrix covers all roles; at least one role defined | `users -> profiles -> users` cycle |
| 7. Hallucination Detection | No entities, endpoints, roles, or rules exist that aren't in the Architecture IR | Table `ghost_products` not in arch entities |

---

## Repair Engine

12 targeted strategies. Each maps to a specific `error_type`. Rule-based fixes are instant (remove broken FK, coerce type, assign default roles). LLM-based fixes are used when content generation is needed (add missing DB column, generate missing table from entity definition).

| Strategy | Trigger | Method |
|----------|---------|--------|
| `remove_hallucinated` | Hallucinated table/endpoint/role/rule | Cascade removal + cleanup of all references |
| `fix_sql_type` | Invalid SQL column type | Coerce to VARCHAR |
| `remove_broken_fk` | FK references non-existent table | Remove foreign key constraint |
| `add_db_column` | API response field missing from DB | LLM: generate matching column |
| `generate_endpoint` | UI data binding has no API | LLM: generate missing endpoint |
| `generate_missing_table` | API entity has no DB table | LLM: generate table from arch entity |
| `add_default_roles` | Protected endpoint has no roles | Assign all defined roles |
| `add_missing_roles` | Endpoint references unknown role | Add role to auth schema |
| `add_matrix_entries` | Role missing from access matrix | Generate matrix with role-appropriate defaults |
| `remove_entity_ref` | API references unknown entity | Remove entity field from endpoint |
| `remove_extra_matrix` | Access matrix has undefined roles | Remove orphaned matrix entries |
| `coerce_type` | Unrecognized type in type_safety layer | Coerce to compatible type |

---

## Code Generation

The validated config is compiled into a working project skeleton:

| Output File | Contents |
|-------------|----------|
| `schema.sql` | CREATE TABLE statements with columns, types, FKs, and junction tables |
| `app.py` | FastAPI application with route handlers for every API endpoint |
| `models.py` | SQLAlchemy ORM models with relationships |
| `auth.py` | JWT authentication middleware with role-based access control |
| `templates/*.html` | One HTML page per UI schema page, with components and data bindings |
| `requirements.txt` | Python dependencies for the generated app |

Generated code is validated: Python files are AST-parsed, SQL is executed against an in-memory SQLite database to check syntax, HTML is checked for structural completeness.

---

## Project Structure

```
app/
  main.py              FastAPI server, 7 endpoints, rate limiter
  config.py            Centralized settings and constants
  pipeline/
    orchestrator.py    4-stage coordinator with state tracking
    intent.py          Stage 1: Intent Extraction
    design.py          Stage 2: System Design
    schema.py          Stage 3: Schema Generation (parallel)
    refinement.py      Stage 4: Validation + Repair loop
    llm.py             DeepSeek API client (structured + unstructured calls)
  validation/
    contracts.py       8 JSON Schema definitions
    validator.py       7-layer validation engine
    consistency.py     Cross-layer consistency checks (API/DB, UI/API, Auth/API)
    hallucination.py   Hallucination detector (compares config against Architecture IR)
    repair.py          12-strategy targeted repair engine
  generation/
    codegen.py         SQL + Python + HTML code generator
    validator.py       AST + SQLite + HTML structure validator
  evaluation/
    dataset.py         20 evaluation prompts (10 real, 10 edge cases)
    runner.py          Automated benchmark with metrics collection
    metrics.py         Success rate, latency, cost, failure type tracking
static/
  index.html           Main UI with prompt input, progress visualization, JSON viewer
  app.js               Frontend logic (SSE streaming, code download, modify/re-run)
  style.css            Dark theme styling
tests/
requirements.txt
Procfile
```

---

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your DEEPSEEK_API_KEY to .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/generate` | POST | Run full pipeline. Body: `{"prompt": "...", "mode": "fast"}` |
| `/generate-stream` | POST | SSE streaming with real-time stage progress |
| `/modify` | POST | Edit intermediate IR and restart from any stage |
| `/download-code` | POST | Download generated project as ZIP |
| `/evaluate?limit=5` | POST | Run benchmark on N prompts (1-20) |
| `/api/cost` | GET | Current session token usage and cost estimate |

---

## Evaluation

20 prompts (10 real-world products + 10 adversarial edge cases) with 6 tracked metrics: success rate, retries per request, failure type distribution, per-stage latency, token usage, and executability rate. Edge cases cover vague prompts, conflicting requirements, incomplete inputs, domain jargon, over-specified constraints, and nonsense input.

Run with:
```bash
python -c "from app.evaluation.runner import run_benchmark; import asyncio; asyncio.run(run_benchmark())"
```

---

## Configuration

Settings live in `app/config.py` and `.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| `DEEPSEEK_API_KEY` | (required) | DeepSeek API key |
| `DEFAULT_MODEL` | `deepseek-chat` | Model for all LLM calls |
| `DEFAULT_TEMPERATURE` | `0.1` | Near-deterministic output |
| `MAX_PROMPT_LENGTH` | `3000` | Character limit for user prompts |
| `RATE_LIMIT` | `5` | Max requests per window |
| `RATE_WINDOW` | `60` | Rate limit window in seconds |
| `MAX_REPAIR_PASSES` | `3` | Max repair iterations per request |

---

## Design Decisions

**Why four stages instead of one prompt?** A single prompt that tries to output a complete config in one shot produces inconsistent, unvalidatable output. Breaking into stages forces the LLM to work at the right level of abstraction at each step and creates natural validation gates between stages.

**Why targeted repair instead of regeneration?** Regenerating the entire config on failure destroys valid work, costs more tokens, and has no guarantee of fixing the specific issue. Targeted repair fixes only what's broken.

**Why parallel schema generation in Stage 3?** UI, Auth, and Business Logic schemas are independent once DB and API schemas exist. Generating them in parallel cuts Stage 3 latency by roughly 60%.

**Why DeepSeek?** OpenAI-compatible API at significantly lower cost ($0.14/M input tokens vs $2.50/M for GPT-4o). The structured output quality is comparable for schema generation tasks.

---

## Limitations

- Requires a DeepSeek API key. Users can provide their own key in the UI.
- Generated code is a working skeleton, not a production application — it lacks error handling, input sanitization, database migrations, and tests.
- Prompts describing 50+ entities may exceed token limits.
- Domain-specific jargon may produce lower-quality output without clarification.
- Rate limited at 5 requests per minute per IP.

## License

MIT
