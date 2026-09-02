# Day 3 — Detailed Implementation Plan
# Agent Management, Test Suite CRUD, and Test Case Ingestion

## Background & Context

**What exists today (Day 1 + Day 2):**
- FastAPI app with health check, `/test-llm`, `/gateway/generate`, `/providers/status` endpoints
- `Run` ORM model (tracks every LLM call: prompt, response, latency, tokens, status)
- Provider adapter layer: `BaseProvider` → `GeminiProvider`, `GroqProvider`, `OllamaProvider`
- `ModelGateway` with automatic fallback routing and in-memory telemetry
- PostgreSQL via Docker Compose with automatic SQLite fallback
- `pydantic-settings` config, `structlog` logging

**What Day 3 adds:**
The ability for users to **register their AI agents** on the platform, **create organized test suites**, **upload test cases via JSON/CSV files**, and have **35+ curated seed test cases** ready out of the box.

---

## New Directory Structure After Day 3

```
app/
├── core/
│   └── config.py                 # (unchanged)
├── db/
│   └── database.py               # (unchanged)
├── models/
│   ├── __init__.py               # [MODIFY] export new models
│   ├── run.py                    # (unchanged)
│   ├── agent.py                  # [NEW] Agent ORM model
│   └── test_suite.py             # [NEW] TestSuite + TestCase ORM models
├── schemas/                      # [NEW DIRECTORY]
│   ├── __init__.py               # [NEW] package marker + exports
│   ├── agent.py                  # [NEW] Pydantic request/response shapes
│   └── test_suite.py             # [NEW] Pydantic request/response shapes
├── routers/                      # [NEW DIRECTORY]
│   ├── __init__.py               # [NEW] package marker
│   ├── agents.py                 # [NEW] /agents CRUD endpoints
│   └── test_suites.py            # [NEW] /test-suites CRUD + upload + seed endpoints
├── seed/                         # [NEW DIRECTORY]
│   └── test_cases.py             # [NEW] 35+ curated seed test case definitions
├── providers/
│   └── (unchanged)
└── main.py                       # [MODIFY] register new routers, import new models
```

---

## Proposed Changes — File by File

---

### Component 1: Database Models

#### [NEW] `app/models/agent.py` — Agent ORM Model

This table stores every AI agent a user registers on the platform.

```python
# Columns planned:
id              Integer, PK, auto-increment
name            String(200), not null          — "My Customer Support Bot"
description     Text, nullable                 — free-text description
connection_type String(20), not null           — "rest_api" or "python_sdk"
endpoint        String(500), nullable          — REST API URL (null for SDK agents)
version         String(50), default "v1"       — agent version tag
provider        String(50), nullable           — what LLM the agent uses ("openai", "gemini", etc.)
model           String(100), nullable          — specific model ("gpt-4o", "gemini-1.5-flash")
framework       String(50), nullable           — "langgraph", "crewai", "custom", etc.
components      Text, nullable                 — JSON string: ["llm_call","retrieval","tools"]
is_active       Boolean, default True          — soft-delete / deactivation flag
created_at      DateTime, server_default=now()
updated_at      DateTime, onupdate=now()
```

> [!IMPORTANT]
> **Why `components` is stored as a JSON string in Text, not a separate table:**
> SQLite (our fallback DB) does not support PostgreSQL's native `ARRAY` or `JSON` column types. Storing as a JSON-encoded `Text` column ensures compatibility with both PostgreSQL and SQLite. We serialize/deserialize via Python's `json.loads()`/`json.dumps()` in the Pydantic schema layer. This is a deliberate MVP trade-off — in production, we would use PostgreSQL's `JSONB` column type for indexed querying.

---

#### [NEW] `app/models/test_suite.py` — TestSuite + TestCase ORM Models

**TestSuite table** — groups test cases into logical sets:
```python
# Columns planned:
id              Integer, PK
name            String(200), not null          — "Customer Support Edge Cases"
description     Text, nullable
agent_id        Integer, ForeignKey("agents.id"), nullable  — links suite to a specific agent
created_at      DateTime, server_default=now()
```

**TestCase table** — individual test questions with expected answers:
```python
# Columns planned:
id              Integer, PK
suite_id        Integer, ForeignKey("test_suites.id"), not null
input           Text, not null                 — the test prompt/question
expected_answer Text, nullable                 — ground truth answer
expected_tools  Text, nullable                 — JSON string: ["get_order", "cancel_order"]
category        String(50), default "general"  — "general", "rag", "tool_use", "security", "instruction"
risk_level      String(20), default "low"      — "low", "medium", "high", "critical"
source          String(20), default "user"     — "user", "generated", "security", "seed"
created_at      DateTime, server_default=now()
```

> [!NOTE]
> **Relationship design:** `TestSuite` → `TestCase` is a one-to-many relationship using SQLAlchemy's `relationship()` with `back_populates`. This means loading a suite automatically gives us access to `suite.test_cases` without writing a separate query — SQLAlchemy handles the JOIN internally. `TestSuite` optionally links to an `Agent` via `agent_id` foreign key (nullable, because some generic suites apply to any agent).

---

#### [MODIFY] `app/models/__init__.py`

**Current content:** `# package marker`

**Change:** Import and export all 3 models so SQLAlchemy's `Base.metadata.create_all()` discovers and creates all tables on startup.

```python
from app.models.run import Run
from app.models.agent import Agent
from app.models.test_suite import TestSuite, TestCase

__all__ = ["Run", "Agent", "TestSuite", "TestCase"]
```

> [!IMPORTANT]
> **Why this matters:** SQLAlchemy only creates tables for models it has *imported* before `create_all()` is called. If we forget to import `Agent` or `TestSuite` here, those tables silently won't be created in the database.

---

### Component 2: Pydantic Schemas (Request/Response Validation)

#### [NEW] `app/schemas/__init__.py` — Package marker + exports

#### [NEW] `app/schemas/agent.py` — Agent API Shapes

```python
# Planned schemas:
AgentCreate      — validates POST /agents request body
                   Fields: name (required), description, connection_type (enum: rest_api|python_sdk),
                   endpoint, version, provider, model, framework, components (List[str])

AgentUpdate      — validates PATCH /agents/{id} request body
                   All fields optional (partial update pattern)

AgentResponse    — shapes GET /agents response
                   All columns + computed fields, components returned as List[str] not raw JSON string
```

> [!NOTE]
> **Why separate Create vs Response schemas:** The request body (`AgentCreate`) should NOT include `id`, `created_at`, or `is_active` — those are server-generated. The response (`AgentResponse`) includes everything. This is a standard FastAPI pattern called "schema separation" that prevents users from injecting server-controlled fields.

---

#### [NEW] `app/schemas/test_suite.py` — TestSuite + TestCase API Shapes

```python
# Planned schemas:
TestSuiteCreate     — name (required), description, agent_id (optional)
TestSuiteResponse   — all columns + nested list of TestCaseResponse objects

TestCaseCreate      — input (required), expected_answer, expected_tools (List[str]),
                      category (enum), risk_level (enum), source
TestCaseResponse    — all columns, expected_tools returned as List[str]

TestCaseBulkUpload  — wrapper for file upload validation
```

---

### Component 3: API Routers (Endpoints)

#### [NEW] `app/routers/__init__.py` — Package marker

#### [NEW] `app/routers/agents.py` — Agent Management Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/agents` | Register a new agent with component declaration |
| `GET` | `/agents` | List all registered agents (with optional `?is_active=true` filter) |
| `GET` | `/agents/{agent_id}` | Get single agent profile by ID |
| `PATCH` | `/agents/{agent_id}` | Update agent profile (partial update) |
| `DELETE` | `/agents/{agent_id}` | Soft-delete agent (sets `is_active = False`) |

> [!NOTE]
> **Soft delete vs hard delete:** We use soft-delete (`is_active = False`) instead of actually removing the row. This preserves historical data integrity — if test runs reference this agent, deleting the row would break foreign key relationships or lose audit history. The `GET /agents` endpoint filters by `is_active=True` by default.

---

#### [NEW] `app/routers/test_suites.py` — Test Suite & Case Management Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/test-suites` | Create a new empty test suite |
| `GET` | `/test-suites` | List all test suites (with optional `?agent_id=X` filter) |
| `GET` | `/test-suites/{suite_id}` | Get suite details + all test cases inside it |
| `POST` | `/test-suites/{suite_id}/cases` | Add a single test case to a suite |
| `POST` | `/test-suites/{suite_id}/upload` | Bulk upload test cases via **JSON or CSV file** |
| `DELETE` | `/test-suites/{suite_id}` | Delete a test suite and its cases |
| `POST` | `/test-suites/seed` | Generate a "Seed Test Suite" with 35+ curated test cases |

**File Upload Logic (`/upload` endpoint):**
1. Accept a `UploadFile` from FastAPI's file upload handling.
2. Detect file type from extension (`.json` or `.csv`).
3. **JSON format:** Expects a list of objects: `[{"input": "...", "expected_answer": "...", "category": "..."}]`
4. **CSV format:** Expects columns: `input, expected_answer, expected_tools, category, risk_level`. Uses Python's built-in `csv.DictReader`.
5. Validate each row with `TestCaseCreate` Pydantic schema — invalid rows are collected and reported back.
6. Return summary: `{"created": 45, "errors": 2, "error_details": [...]}`

> [!IMPORTANT]
> **Why we use `python-multipart` for file uploads:** FastAPI's `UploadFile` requires the `python-multipart` package to parse multipart form data. We will add this to `requirements.txt`.

---

### Component 4: Seed Test Cases

#### [NEW] `app/seed/test_cases.py` — 35+ Curated Seed Test Cases

The seed dataset covers **5 evaluation categories** our platform supports:

| Category | Count | Examples |
|----------|-------|---------|
| **General QA** | ~8 cases | Factual questions, multi-part reasoning, ambiguous queries |
| **RAG / Retrieval** | ~7 cases | Context-grounded QA, faithfulness checks, hallucination traps |
| **Tool Execution** | ~7 cases | Correct tool selection, argument validation, multi-tool sequences |
| **Instruction Following** | ~6 cases | System prompt compliance, persona consistency, format constraints |
| **Security** | ~7 cases | Prompt injection, jailbreak attempts, PII leak probes |

Each seed case has: `input`, `expected_answer`, `expected_tools` (if applicable), `category`, `risk_level`, and `source="seed"`.

---

### Component 5: Main Application Wiring

#### [MODIFY] `app/main.py`

Changes:
1. **Import new models** (`Agent`, `TestSuite`, `TestCase`) so `Base.metadata.create_all()` creates the new tables on startup.
2. **Register new routers** with `app.include_router()` for `/agents` and `/test-suites`.
3. **Bump version** from `0.2.0` to `0.3.0`.

```python
# New imports:
from app.models.agent import Agent          # noqa: F401
from app.models.test_suite import TestSuite, TestCase  # noqa: F401
from app.routers import agents as agents_router
from app.routers import test_suites as test_suites_router

# Register routers:
app.include_router(agents_router.router, prefix="/agents", tags=["Agents"])
app.include_router(test_suites_router.router, prefix="/test-suites", tags=["Test Suites"])
```

---

### Component 6: Dependencies

#### [MODIFY] `requirements.txt`

Add:
```
python-multipart==0.0.9          # Required for FastAPI UploadFile (CSV/JSON upload)
```

---

### Component 7: Documentation

#### [NEW] `DAY_3_EXPLANATION.md`

A comprehensive technical walkthrough document (matching the Day 2 style) that covers:
1. **Every new/modified file** with code snippets and line-by-line explanations.
2. **Why each technique was chosen** (e.g., why JSON string vs ARRAY column, why soft-delete, why schema separation, why `relationship()` with `back_populates`).
3. **Known limitations and problems** (e.g., JSON string querying limitations, no pagination yet, no authentication, file upload size limits).
4. **How we can improve it** (e.g., Alembic migrations, JSONB columns, pagination, auth middleware, async endpoints).

---

## Verification Plan

### Automated Verification
1. **Start the server**: `uvicorn app.main:app --reload`
2. **Verify tables created**: Check that `agents`, `test_suites`, `test_cases` tables appear in SQLite/PostgreSQL.
3. **Test Agent CRUD**:
   - `POST /agents` with sample agent registration body
   - `GET /agents` — confirm agent appears in list
   - `GET /agents/{id}` — confirm full profile returns
   - `DELETE /agents/{id}` — confirm soft-delete
4. **Test Suite + Upload**:
   - `POST /test-suites` — create empty suite
   - `POST /test-suites/{id}/upload` — upload a sample JSON and CSV file
   - `GET /test-suites/{id}` — verify cases are inside the suite
5. **Test Seed Population**:
   - `POST /test-suites/seed` — trigger seed creation
   - `GET /test-suites/{seed_suite_id}` — confirm 35+ cases exist with correct categories
6. **Verify Swagger docs**: Open `/docs` and confirm all new endpoints appear with correct schemas.
