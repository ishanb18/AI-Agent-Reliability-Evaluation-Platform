# 📚 Day 3 Technical Explanation & Architecture Reference

This document provides a comprehensive, step-by-step breakdown of **every new file created** and **every file modified** during the Day 3 implementation of the **AI Agent Reliability & Evaluation Platform**.

---

## 1. 🏗️ File Structure Overview (Day 3 Additions & Modifications)

```text
app/
├── models/
│   ├── agent.py               # [NEW] Agent ORM model
│   ├── test_suite.py          # [NEW] TestSuite & TestCase ORM models
│   └── __init__.py            # [MODIFIED] Exports all models so create_all() works
├── schemas/                   # [NEW DIRECTORY]
│   ├── __init__.py            # [NEW] Package marker
│   ├── agent.py               # [NEW] Agent Pydantic validation schemas
│   └── test_suite.py          # [NEW] TestSuite & TestCase Pydantic schemas
├── routers/                   # [NEW DIRECTORY]
│   ├── __init__.py            # [NEW] Package marker
│   ├── agents.py              # [NEW] Agent CRUD + Connection Handshake router
│   └── test_suites.py         # [NEW] TestSuite CRUD + JSON/CSV upload + Seed router
├── seed/                      # [NEW DIRECTORY]
│   ├── __init__.py            # [NEW] Package marker
│   └── test_cases.py          # [NEW] 35 curated seed test cases across 5 categories
├── main.py                    # [MODIFIED] Registered /agents and /test-suites routers
requirements.txt               # [MODIFIED] Added python-multipart for file upload parsing
DAY_3_EXPLANATION.md           # [NEW] Complete technical walkthrough document
```

---

## 2. 🗄️ Database Integration: PostgreSQL Container & SQLite Resilient Fallback

### How Docker & PostgreSQL were Integrated and Tested:

1. **Container Infrastructure**:
   - PostgreSQL 16 is containerized via `docker-compose.yml` (`container_name: evalplatform_db`, port `5432:5432`).
2. **Dynamic Database Resolution**:
   - `app/db/database.py` connects to `postgresql://evaluser:evalpass@localhost:5432/evaldb`.
   - On application startup (`lifespan`), SQLAlchemy attempts to create tables in PostgreSQL (`Base.metadata.create_all(bind=engine)`).
   - If PostgreSQL is offline/stopped, `database.py` gracefully catches the operational error and falls back to a local SQLite engine (`evalplatform.db`), ensuring **zero downtime**.
3. **PostgreSQL Verification**:
   - Docker container `evalplatform_db` was booted via `docker-compose up -d`.
   - FastAPI server was started; logs confirmed `startup: database & gateway ready env=development` connected directly to PostgreSQL.
   - Executed `test_day3.py`, performing all CRUD operations, connection handshakes, seed generation, JSON bulk ingestion, and soft deletions directly against the live PostgreSQL database.

---

## 3. 🤖 Agent ORM & Schemas (`app/models/agent.py` & `app/schemas/agent.py`)

### Code Highlights & Breakdown:

#### `app/models/agent.py`:

```python
class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connection_type: Mapped[str] = mapped_column(String(20), nullable=False, default="rest_api")
    endpoint: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(50), default="v1")
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    framework: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    components: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-string
    response_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-string
    auto_discover: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="untested")
    last_tested_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

### Why We Selected These Specific Techniques:

#### 1. Why JSON-encoded `Text` columns for `components` & `response_mapping`?

- **Choice**: Stored as JSON strings inside `Text` columns rather than PostgreSQL `ARRAY` or native `JSONB`.
- **Reason**: Maintains 100% compatibility across both PostgreSQL and SQLite fallback environments (SQLite does not natively support PostgreSQL `ARRAY` types).
- **Alternative & Risk if not used**: If we used `sqlalchemy.dialects.postgresql.ARRAY`, running locally on SQLite when Docker is down would throw fatal SQLAlchemy compilation errors.
- **How it works**: Serialized with `json.dumps()` on save, deserialized with `json.loads()` on read via helper methods (`get_components_list()`, `set_components_list()`).

#### 2. Why Soft Delete (`is_active = False`) instead of Hard SQL `DELETE`?

- **Choice**: Setting `is_active = False` on deletion.
- **Reason**: Evaluation runs reference agent IDs. A hard SQL `DELETE` breaks historical audit logs or foreign key constraints.
- **Alternative & Risk if not used**: Hard deletion (`db.delete(agent)`) causes orphaned evaluation runs or database integrity violations.

#### 3. Schema Separation Pattern (`AgentCreate`, `AgentUpdate`, `AgentResponse`):

- **Choice**: Separate Pydantic models for request input vs output.
- **Reason**: Prevents parameter injection attacks (e.g. a user attempting to send `{"id": 999, "is_active": false}` during creation). `AgentCreate` acts as a strict whitelist of fields acceptable from users.

---

## 4. 🔌 Connection Handshake Feature (`app/routers/agents.py`)

### How Naive/Inexperienced Users Can Connect Their AI Agent Smoothly:

1. **Zero Setup Required**: A naive user only needs to paste their agent's URL endpoint (e.g., `https://mycompany.com/agent`).
2. **Instant Feedback**: Upon calling `POST /agents`, the platform immediately fires a test ping (`_test_agent_connection()`) to the endpoint.
3. **Response Auto-Discovery**:
   - The handshake inspects the response payload and automatically maps where the answer text lives (`answer`, `response`, `result.text`, etc.).
   - It informs the user which evaluation metrics are unlocked (e.g. `["correctness", "relevance", "safety", "latency"]`) vs which are unavailable (e.g. `tool_accuracy` if no `tool_calls` array is returned).
4. **Actionable Suggestions**: If the endpoint fails or times out, the handshake returns human-readable diagnostic messages: *"Could not connect to URL. Check if your agent server is running."*

### Key Code & Mechanics:

```python
def _test_agent_connection(endpoint: str) -> ConnectionTestResult:
    test_payload = {"input": "Hello, are you there? This is a connection test."}
    try:
        start = time.time()
        response = httpx.post(endpoint, json=test_payload, timeout=10.0)
        latency_ms = round((time.time() - start) * 1000, 2)
        ...
        # Scans response keys for answer, tools, context, metadata
```

---

## 5. 🧪 Test Suite & Ingestion Engine (`app/models/test_suite.py`, `app/schemas/test_suite.py`, `app/routers/test_suites.py`)

### Code Highlights & Breakdown:

#### `app/models/test_suite.py`:

```python
class TestSuite(Base):
    __tablename__ = "test_suites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)

    test_cases: Mapped[List["TestCase"]] = relationship(
        "TestCase", back_populates="suite", cascade="all, delete-orphan", lazy="selectin"
    )
```

### Why We Selected These Specific Techniques:

#### 1. Why `cascade="all, delete-orphan"` and `lazy="selectin"`?

- **`cascade="all, delete-orphan"`**: Automatically deletes all child `TestCase` rows when a `TestSuite` is deleted. Prevents orphaned database records.
- **`lazy="selectin"`**: Instructs SQLAlchemy to load test cases using an optimized second `SELECT IN` query rather than multiple queries, avoiding N+1 performance bottlenecks.

#### 2. Why `POST /test-suites/seed` is placed BEFORE `POST /test-suites/{suite_id}`?

- **Reason**: FastAPI evaluates routes sequentially. If `/{suite_id}` was declared first, calling `/test-suites/seed` would cause FastAPI to parse `"seed"` as an integer `suite_id`, raising a 422 Unprocessable Entity validation error.

#### 3. CSV/JSON Bulk Upload Ingestion Engine (`POST /test-suites/{id}/upload`):

- Accepts multipart form files via `python-multipart` and `UploadFile`.
- Automatically checks extension (`.json` or `.csv`).
- **JSON Parser**: Validates JSON array structure.
- **CSV Parser**: Uses `csv.DictReader`, auto-converts stringified list representations in `expected_tools` column (`'["tool1", "tool2"]'`) into Python lists.
- **Row-by-Row Resilience**: Validates each row using `TestCaseCreate` Pydantic model. If row 5 is invalid, it records an `UploadError` for row 5 and continues processing valid rows.

---

## 6. 🌱 Curated Seed Dataset (`app/seed/test_cases.py`)

Includes **35 production-ready, curated test cases** across 5 core categories:

1. **General QA (8 cases)**: Factual correctness, math, reasoning.
2. **RAG / Retrieval (7 cases)**: Faithfulness, hallucination traps, grounding.
3. **Tool Execution (7 cases)**: Tool selection, multi-tool chaining, invalid argument handling.
4. **Instruction Following (6 cases)**: Format constraints, persona enforcement, bullet-count constraints.
5. **Security (7 cases)**: Prompt injection, system prompt leak probes, PII protection, jailbreaks.

Triggering `POST /test-suites/seed` populates these 35 test cases into the database immediately.

---

## 7. 🚀 OpenAPI / Swagger Documentation & Schemas

### How the "Schemas" Section in FastAPI `/docs` is Generated:

- FastAPI automatically parses all Pydantic models referenced in router endpoint annotations (`response_model=...` and body arguments `agent_data: AgentCreate`).
- Field-level metadata defined using `Field(description=..., examples=..., min_length=...)` is reflected dynamically in the interactive Swagger UI at `http://127.0.0.1:8000/docs`.

---

## 8. 🛠️ Future Improvements & Potential Trade-offs

1. **Alembic Database Migrations**:
   - *Current State*: Using `Base.metadata.create_all()`.
   - *Improvement*: Introduce Alembic migration scripts to track schema migrations explicitly as tables evolve.
2. **Async Database Driver (`asyncpg`)**:
   - *Current State*: Synchronous SQLAlchemy ORM queries with `psycopg2-binary`.
   - *Improvement*: Migrate to async database sessions (`AsyncSession` + `asyncpg`) to handle high-concurrency evaluation workloads.
3. **User Authentication & Authorization**:
   - *Current State*: Endpoints are public for MVP evaluation.
   - *Improvement*: Add JWT-based API key authentication (`Header(..., alias="X-API-Key")`) to isolate agent profiles and test suites per tenant/user.
