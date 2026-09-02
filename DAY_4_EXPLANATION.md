# Day 4 — Evaluation Engine: Complete Technical Reference

## What Day 4 Builds

Day 4 is the **core value** of the entire platform — the engine that actually evaluates an AI agent's responses. Given an agent + a test suite, it automatically invokes the agent for every test case, scores every response using multiple techniques, and stores granular results in the database.

---

## File Map

```
app/
├── evaluation/                  ← NEW directory (all scoring logic)
│   ├── __init__.py             ← package marker
│   ├── deterministic.py        ← math-based scores (no LLM)
│   ├── llm_judge.py            ← LLM-as-a-Judge scores
│   └── orchestrator.py         ← coordinates the full pipeline
├── models/
│   └── eval_run.py             ← NEW: 3 database tables
├── schemas/
│   └── evaluation.py           ← NEW: Pydantic request/response shapes
├── routers/
│   └── evaluations.py          ← NEW: 5 REST API endpoints
└── main.py                     ← MODIFIED: registered router, v0.4.0

DAY_4_EXPLANATION.md            ← this file
```

---

## Part 1 — Database Models (`app/models/eval_run.py`)

Three new tables are created automatically when the server starts.

### Table 1: `EvalRun` — One evaluation session

Created once per "run evaluation" request. Tracks overall progress.

```python
class EvalRun(Base):
    id            # primary key
    agent_id      # FK → which agent was evaluated
    suite_id      # FK → which test suite was run
    status        # "pending" → "running" → "completed" / "failed"
    total_cases   # how many test cases were in the suite
    passed_cases  # cases where overall_score >= 0.5
    failed_cases  # cases where overall_score < 0.5 or invocation failed
    avg_score     # average of all case scores (NULL until completed)
    judge_provider / judge_model  # which LLM was used as the judge
    started_at / completed_at / created_at
```

**Why `avg_score` is NULL until completion:** prevents reading a misleading partial average while the run is still in progress.

---

### Table 2: `EvalRunCase` — One test case execution

One record per test case. Stores what the agent returned and how it performed.

```python
class EvalRunCase(Base):
    id / run_id / test_case_id
    agent_output        # extracted answer text (after response_mapping)
    agent_raw_response  # full raw JSON (stored for debugging)
    tool_trace          # JSON list of tool names agent called
    context_chunks      # JSON list of retrieved chunks (RAG agents)
    latency_ms          # measured end-to-end response time
    input_tokens / output_tokens / estimated_cost / step_count
    status              # "success" | "error" | "timeout"
    error               # what went wrong if status != success
```

**Key helper methods:**
- `get_tool_trace_list()` / `set_tool_trace_list()` — serialize/deserialize JSON ↔ Python list
- `get_context_chunks_list()` / `set_context_chunks_list()` — same pattern for context

**Why store `agent_raw_response`?** When a score looks unexpected, you can inspect exactly what the agent returned before any parsing. Essential for debugging.

---

### Table 3: `Evaluation` — All metric scores for one case

One record per `EvalRunCase`. All scores are **nullable** — `NULL` means **skipped** (data unavailable), NOT failed.

```python
class Evaluation(Base):
    id / run_case_id
    # LLM-as-a-Judge (nullable)
    correctness / relevance / faithfulness / completeness
    instruction_following / safety_score
    # Deterministic (nullable)
    tool_accuracy / trajectory_score
    # Metadata
    judge_provider / judge_model
    reasoning           # JSON dict: {"correctness": "reason...", "relevance": "reason..."}
    metrics_evaluated   # JSON list of metric names that ran
    metrics_skipped     # JSON dict of metric → reason it was skipped
```

**Key method: `compute_overall_score()`**
Averages all non-NULL scores. Used to determine PASS (≥ 0.5) vs FAIL (< 0.5).

```python
def compute_overall_score(self) -> Optional[float]:
    scores = [s for s in [self.correctness, self.relevance, ...] if s is not None]
    return round(sum(scores) / len(scores), 4) if scores else None
```

---

## Part 2 — Deterministic Evaluators (`app/evaluation/deterministic.py`)

Zero LLM calls. Pure math. Always runs in < 1ms.

### `evaluate_tool_accuracy(actual_tools, expected_tools) → float`

**What:** Did the agent call the right tools? (order does NOT matter here)

**Formula:** `len(actual ∩ expected) / len(expected)`

```
expected = ["get_order", "cancel_order"]
actual   = ["get_order", "cancel_order"]  → 2/2 = 1.0
actual   = ["get_order"]                  → 1/2 = 0.5
actual   = []                             → 0/2 = 0.0
```

---

### `evaluate_trajectory(actual_tools, expected_tools) → float`

**What:** Did the agent call tools in the right ORDER?

**Algorithm:** Longest Common Subsequence (LCS) via dynamic programming.

**Formula:** `LCS(actual, expected) / len(expected)`

**Why LCS not exact match?** Agents may insert extra diagnostic steps. LCS rewards partial order correctness instead of all-or-nothing.

```
expected = ["A", "B", "C"]
actual   = ["A", "B", "C"]  → LCS=3, score=1.0
actual   = ["A", "C", "B"]  → LCS=2, score=0.67 (wrong order)
actual   = ["A", "B"]       → LCS=2, score=0.67 (missing step)
```

**How LCS works (dp table):**
```python
for i in range(1, m+1):
    for j in range(1, n+1):
        if actual[i-1] == expected[j-1]:
            dp[i][j] = dp[i-1][j-1] + 1   # extend the match
        else:
            dp[i][j] = max(dp[i-1][j], dp[i][j-1])  # take best previous
```

---

### `calculate_cost(provider, model, input_tokens, output_tokens) → float`

**What:** Estimates USD cost of one agent call.

**Formula:** `(input_tokens / 1_000_000) × input_price + (output_tokens / 1_000_000) × output_price`

Uses a `_PRICING_TABLE` dict keyed by `"provider/model"` (e.g. `"openai/gpt-4o"` → `(5.00, 15.00)`). Falls back to a conservative default `(1.00, 2.00)` if model is not listed.

---

### `score_latency(latency_ms, threshold_ms=3000) → float`

**What:** Converts raw milliseconds into a 0–1 score.

**Formula:** `max(0.0, 1.0 - (latency_ms / threshold_ms))`

```
0ms   → 1.0  (instant)
1500ms → 0.5  (acceptable)
3000ms → 0.0  (at threshold)
5000ms → 0.0  (capped at 0, never negative)
```

---

## Part 3 — LLM-as-a-Judge Evaluators (`app/evaluation/llm_judge.py`)

### How Hallucination Is Prevented

| Technique | What it does |
|-----------|-------------|
| **Closed-context** | All facts in the prompt — judge compares, never recalls |
| **Strict JSON** | Response must be `{"score": float, "reasoning": "..."}` — free text rejected |
| **Explicit rubric** | 5 anchor points (1.0/0.8/0.5/0.2/0.0) with written criteria |
| **Temperature 0** | Same input = same output every time |
| **Stored reasoning** | Every score has an auditable justification |

---

### `_call_judge(prompt, gateway, judge_provider, judge_model)` — Shared Core

All 6 metrics funnel through this single function. It:
1. Calls `gateway.generate(prompt, provider=judge_provider, job_type="fast", enable_fallback=True)`
2. Strips markdown code fences if present (` ```json ... ``` `)
3. Parses JSON: `json.loads(raw)`
4. Clamps score: `max(0.0, min(1.0, score))`
5. Returns `(score, reasoning)` on success, `(None, error_message)` on failure

**Why return `None` not `0.0` on failure?** `None` = "couldn't evaluate". `0.0` = "evaluated and it was terrible". These are very different things.

---

### The 6 Metrics

| Function | Inputs Needed | When Skipped |
|----------|--------------|--------------|
| `evaluate_correctness` | question, answer, expected_answer | No expected_answer |
| `evaluate_relevance` | question, answer | Never |
| `evaluate_faithfulness` | question, answer, context_chunks | No context in response |
| `evaluate_completeness` | question, answer, expected_answer | No expected_answer |
| `evaluate_safety` | question, answer | Never |
| `evaluate_instruction_following` | question, answer, system_prompt | No system_prompt |

**Correctness** — Compares agent answer vs ground truth. Judge task is *comparison*, not knowledge retrieval.

**Relevance** — Is the answer on-topic? Independent of whether it's factually right. Always runs.

**Faithfulness** — RAG hallucination check. Judge given context chunks + answer. Checks: "Can every claim be traced back to the context?" Does NOT need expected_answer.

**Completeness** — Coverage check. Did the answer hit all key points from expected_answer?

**Safety** — Checks harmful content, PII exposure, system prompt leakage, injection compliance. Always runs.

**Instruction Following** — Did the agent obey its system prompt (format, persona, scope, policy)?

---

## Part 4 — Orchestrator (`app/evaluation/orchestrator.py`)

The central brain. Coordinates all 4 steps for every test case.

### `_invoke_agent(endpoint, test_input)` → (raw_json, latency_ms, status, error)

POSTs `{"input": test_input}` to the agent's endpoint with a 30-second timeout.

Three outcomes:
- **success** — agent responded (JSON or plain text)
- **error** — agent returned HTTP 4xx/5xx
- **timeout** — agent didn't respond in 30s

```python
start = time.time()
response = httpx.post(endpoint, json=payload, timeout=30.0)
latency_ms = round((time.time() - start) * 1000, 2)
```

Plain text responses are wrapped: `{"answer": response.text}` so downstream parsing works uniformly.

---

### `_parse_response(raw_json, response_mapping)` → parsed dict

4-level fallback chain — always produces an `answer` field:

```
Level 1: Use response_mapping  →  agent.response_mapping["answer"] = "response" → data["response"]
Level 2: Auto-detect by name   →  scan ["answer","response","output","text","result","message","content","reply"]
Level 3: Nested key            →  {"result": {"text": "..."}} → extract sub-key "text"
Level 4: Entire JSON           →  json.dumps(raw_json) as the answer
```

Also extracts tools and context using the same fallback approach.

---

### `_extract_tool_names(tools_data)` → List[str]

Normalizes tool data from any format to a flat list of strings:

```python
["get_order", "cancel_order"]               → ["get_order", "cancel_order"]
[{"name": "get_order"}, ...]               → ["get_order", ...]
[{"tool": "get_order"}, ...]               → ["get_order", ...]
"get_order"                                 → ["get_order"]
```

---

### `_extract_context_strings(context_data)` → List[str]

Same normalization for RAG context chunks. Extracts `content`, `text`, `chunk`, or `passage` keys from dicts.

---

### `_score_case(test_case, agent, parsed_data, latency_ms, ...)` → (scores, reasoning, evaluated, skipped)

The metric decision engine. Determines what to run based on available data:

```python
# Always run
scores["latency_score"] = deterministic.score_latency(latency_ms)
scores["relevance"]     = llm_judge.evaluate_relevance(...)
scores["safety_score"]  = llm_judge.evaluate_safety(...)

# Only if expected_answer exists
if expected_answer:
    scores["correctness"]  = llm_judge.evaluate_correctness(...)
    scores["completeness"] = llm_judge.evaluate_completeness(...)

# Only if context_chunks found in response
if context_chunks:
    scores["faithfulness"] = llm_judge.evaluate_faithfulness(...)

# Only if both actual and expected tools exist
if actual_tools and expected_tools:
    scores["tool_accuracy"]    = deterministic.evaluate_tool_accuracy(...)
    scores["trajectory_score"] = deterministic.evaluate_trajectory(...)

# Only if agent has system_prompt
if system_prompt:
    scores["instruction_following"] = llm_judge.evaluate_instruction_following(...)
```

---

### `run_evaluation(agent_id, suite_id, db, gateway, ...)` → EvalRun

The main entry point. Full pipeline:

```
1. Load + validate agent and suite from DB
2. Create EvalRun{status="running"}
3. For each test case:
     a. _invoke_agent()         → raw response + latency
     b. _parse_response()       → extract answer, tools, context
     c. Create EvalRunCase in DB
     d. _score_case()           → run all applicable evaluators
     e. Create Evaluation in DB
     f. db.commit()             ← commit after EACH case
4. Aggregate → update EvalRun{status="completed", avg_score}
5. Return EvalRun
```

**Why `db.commit()` after each case?** If the server crashes mid-run (on case 17 of 35), cases 1–16 are already saved. Without this, the entire run would restart from scratch.

---

## Part 5 — API Router (`app/routers/evaluations.py`)

### 5 Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/evaluations/run` | POST | Trigger a new evaluation run |
| `/evaluations` | GET | List runs (filterable by agent_id, status) |
| `/evaluations/{run_id}` | GET | Get run summary + aggregated scores |
| `/evaluations/{run_id}/cases` | GET | Get all cases + per-case scores |
| `/evaluations/{run_id}/cases/{case_id}` | GET | Get single case detail |

### 3 Helper Converters (ORM → Pydantic)

```python
_run_to_response(run)          → EvalRunResponse
_run_case_to_response(case)    → EvalRunCaseResponse
_evaluation_to_response(eval)  → EvaluationResponse
```

These deserialize JSON fields (tool_trace, context_chunks, reasoning, metrics_evaluated, metrics_skipped) from their DB string format back into Python objects for the API response.

### `get_gateway()` — Singleton

```python
_gateway: Optional[ModelGateway] = None

def get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway
```

Module-level singleton so telemetry counters persist across requests.

---

## Part 6 — Workflow-Adaptive Evaluation

The platform adapts which metrics run based on what the agent returns:

| Metric | Basic QA | RAG Agent | Tool Agent | Full Agent |
|--------|:---:|:---:|:---:|:---:|
| Relevance | ✅ | ✅ | ✅ | ✅ |
| Safety | ✅ | ✅ | ✅ | ✅ |
| Latency / Cost | ✅ | ✅ | ✅ | ✅ |
| Correctness | ⚡ | ⚡ | ⚡ | ⚡ |
| Completeness | ⚡ | ⚡ | ⚡ | ⚡ |
| Faithfulness | ❌ | ✅ | ❌ | ✅ |
| Tool Accuracy | ❌ | ❌ | ✅ | ✅ |
| Trajectory | ❌ | ❌ | ✅ | ✅ |
| Instruction Following | ⚡ | ⚡ | ⚡ | ⚡ |

✅ Always &nbsp;&nbsp; ⚡ If data available &nbsp;&nbsp; ❌ Skipped

---

## Part 7 — Complete User Journey

### Step 1: User has an existing agent running somewhere
```
https://my-customer-support-bot.com/chat
Returns: {"response": "I can help with that", "sources": ["doc1.pdf"]}
```

### Step 2: User registered the agent on Day 3
The handshake auto-detected:
```json
"response_mapping": {"answer": "response", "context": "sources"}
"components": ["llm_call", "retrieval"]
```

### Step 3: User selects a test suite and hits "Run Evaluation"
```http
POST /evaluations/run
{"agent_id": 1, "suite_id": 1, "judge_provider": "gemini"}
```

### Step 4: Orchestrator processes each test case
For test case: *"What is the return policy?"* / expected: *"30 days with receipt"*

```
POST https://my-customer-support-bot.com/chat
     {"input": "What is the return policy?"}

Response: {"response": "Returns are accepted within 60 days", "sources": ["policy.pdf: Returns within 30 days..."]}
Latency: 420ms
```

**Parsing:**
- `answer` = "Returns are accepted within 60 days"
- `context` = ["policy.pdf: Returns within 30 days..."]

**Scoring:**
- `latency_score` = `max(0, 1 - 420/3000)` = 0.86 ✅
- `relevance` = 0.95 (judge: "directly addresses return policy question")
- `safety` = 1.0 (judge: "no safety issues")
- `correctness` = 0.2 (judge: "says 60 days, expected answer says 30 days")
- `completeness` = 0.6 (judge: "mentions timeframe but not receipt requirement")
- `faithfulness` = 0.3 (judge: "answer says 60 days but context says 30 days — unfaithful")
- `tool_accuracy` = NULL (skipped — no tools)
- `overall_score` = avg(0.86, 0.95, 1.0, 0.2, 0.6, 0.3) = **0.65**

### Step 5: User reads the results
```http
GET /evaluations/1/cases
```

Sees: agent answered wrong (60 days vs 30 days) AND hallucinated beyond the context. The faithfulness score (0.3) flags this as a RAG grounding failure.

### Step 6: User fixes the agent, re-runs, compares
The improved agent scores 0.92 avg_score vs original 0.65. Regression confirmed.

---

## Part 8 — Modifications to Existing Files

### `app/models/__init__.py`
Added 3 new imports so `create_all()` discovers the new tables:
```python
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation  # Day 4
```

### `app/main.py`
```python
# New import
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation
from app.routers import evaluations as evaluations_router

# New router registration
app.include_router(evaluations_router.router, prefix="/evaluations", tags=["Evaluations"])

# Version bump
return {"status": "ok", "version": "0.4.0", ...}
```

---

## Part 9 — Key Design Principles

**Graceful Degradation** — Works with any agent format. Plain text agent → 4 metrics. Rich JSON agent → 10 metrics. Never rejects an agent.

**Auditability** — Every score has stored reasoning. `metrics_skipped` records exactly why metrics didn't run. Nothing is a black box.

**Per-Case Isolation** — One timeout/error never kills the whole run. `try/except` + `db.commit()` after each case ensures partial results are always saved.

**Deterministic First** — Tool accuracy, trajectory, cost, latency use pure math. Only use the LLM judge when language understanding is genuinely needed.

**NULL ≠ Zero** — Skipped metrics store `NULL`. This is critical. A `NULL` faithfulness score means "not a RAG agent". A `0.0` faithfulness score means "RAG agent that hallucinated completely". These must not be confused.
