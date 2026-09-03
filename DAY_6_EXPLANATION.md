# DAY 6 — Experiment Comparison, Evaluation Discovery & Quota Maximizer

**Date**: 2026-09-03 | **Version**: 0.6.0 | **Status**: Complete

---

## Overview

Day 6 adds four major features on top of the Day 1–5 foundation:

| Feature | Files Changed | Purpose |
|---------|--------------|---------|
| **A. Evaluation Discovery** | `discovery.py`, `evaluations.py`, `evaluation.py` schemas | User picks which metrics to run |
| **B. Experiment Comparison** | `experimenter.py`, `experiment.py`, `experiments.py` | V1 vs V2 with PASS/REVIEW/FAIL |
| **C. Gemini Quota Maximizer** | `gemini.py`, `config.py`, `.env` | 2 keys × 4 models = ~160 req/day |
| **D. API Documentation** | `API_REFERENCE.md` | Full endpoint reference |

---

## Feature A: Evaluation Discovery & Metric Selection

### The Problem
Previously, users triggered `POST /evaluations/run` blindly. The orchestrator would silently try all metrics and skip ones where data was missing — but the user had no idea WHY metrics were skipped or HOW to enable them.

### The Solution: 2-Step Evaluation Flow

**Step 1: Discover** — `POST /evaluations/discover`
```
User sends: { agent_id: 1, suite_id: 1 }

Platform does:
  1. Picks a representative test case from the suite
  2. Sends it to the agent's endpoint (live probe)
  3. Inspects what fields the response contains
  4. Checks which test cases have expected_answer / expected_tools

Platform returns:
  - available_metrics: ["relevance", "safety", "correctness", "tool_accuracy"]
  - unavailable_metrics: ["faithfulness", "instruction_following"]
  - Per-metric: reason WHY blocked + HOW TO ENABLE (step-by-step)
```

**Step 2: Run** — `POST /evaluations/run`
```json
{
  "agent_id": 1,
  "suite_id": 1,
  "selected_metrics": ["relevance", "safety", "correctness"]
}
```
Only selected metrics run. Others are recorded in `metrics_skipped` with reason `"not selected by user"`.

### Key File: `app/evaluation/discovery.py`

The `METRIC_REQUIREMENTS` dict defines what each metric needs:
```python
"faithfulness": {
    "needs_from_agent": "context/retrieved_chunks in the response",
    "needs_from_suite": "any input",
    "how_to_enable": "Add context chunks: {\"answer\": \"...\", \"context\": [\"chunk1\", ...]}"
}
```

The `discover_capabilities()` function:
1. Calls `_pick_probe_case()` — finds most informative test case (prefers one with both `expected_answer` and `expected_tools`)
2. Calls `_probe_agent()` — live HTTP call with 15s timeout
3. Calls `_detect_fields()` — scans response for answer/tools/context/metadata keys
4. Calls `_build_metric_report()` — cross-references detected fields with suite data to determine per-metric availability

### Orchestrator Changes

`_score_case()` now accepts `selected_metrics: Optional[List[str]]`:
```python
def _should_run(metric: str) -> bool:
    if selected_metrics is None:
        return True   # None = run everything (backwards compatible)
    return metric in selected_metrics
```

Every metric block now starts with:
```python
if not _should_run("correctness"):
    metrics_skipped["correctness"] = "not selected by user"
elif answer and expected_answer:
    # ... run the metric
```

---

## Feature B: Experiment Comparison & Regression

### The Problem
No way to compare Agent V1 vs V2 systematically. No deployment gate.

### The Solution: Experiment System

**New DB Table**: `experiments`
```
id | name | baseline_run_id | candidate_run_id | verdict | result (JSON) | config (JSON)
```

**Two creation modes**:
- **Mode A** — Compare existing runs: `{baseline_run_id: 3, candidate_run_id: 7}`
- **Mode B** — Fresh run: `{baseline_agent_id: 1, candidate_agent_id: 2, suite_id: 1}` (runs both agents then compares)

### Key File: `app/evaluation/experimenter.py`

**`compare_runs()` logic:**

1. **Load metric averages** — `_get_run_metric_averages()` computes per-metric means across all `EvalRunCase` + `Evaluation` records
2. **Build diff table** — `_build_metric_diffs()` compares baseline vs candidate per metric:
   ```python
   delta = candidate_val - baseline_val
   status = "improved" | "regressed" | "unchanged" | "new" | "removed"
   ```
3. **Determine verdict** — `_determine_verdict()`:
   - **FAIL**: `candidate < threshold` on any metric → immediately fails
   - **REVIEW**: all thresholds pass BUT any metric regressed > 5% from baseline
   - **PASS**: all thresholds pass AND no significant regressions
4. **Generate suggestions** — `_generate_suggestions()` produces human-readable recommendations based on which metrics failed or regressed

### Default Thresholds
```python
DEFAULT_THRESHOLDS = {
    "correctness":      0.70,
    "relevance":        0.70,
    "safety_score":     0.85,   # stricter for safety
    "faithfulness":     0.70,
    "completeness":     0.65,
    "tool_accuracy":    0.70,
    "trajectory_score": 0.65,
}
REGRESSION_TOLERANCE = 0.05   # 5% drop triggers REVIEW
```

All overridable via `"thresholds": {"correctness": 0.80}` in the request.

---

## Feature C: Gemini Quota Maximizer

### The Problem
Gemini free tier: **~20 requests/day per model**. With 1 key and 1 model = only 20 evaluation calls before hitting quota.

### The Solution: Priority Cascade Strategy

**Config** (`config.py` + `.env`):
```env
GEMINI_API_KEY=key_from_account_1
GEMINI_API_KEYS=key_from_account_2,key_from_account_3   # comma-separated
GEMINI_ROTATE_MODELS=true
```

**Cascade order** (in `gemini.py`):
```
Step 1: Key1 + gemini-3.5-flash      (best model, key 1)
Step 2: Key2 + gemini-3.5-flash      (best model, key 2)
Step 3: Key1 + gemini-3.6-flash      (2nd model, key 1)
Step 4: Key2 + gemini-3.6-flash      (2nd model, key 2)
Step 5: Key1 + gemini-3.5-flash-lite (3rd model, key 1)
Step 6: Key2 + gemini-3.5-flash-lite (3rd model, key 2)
Step 7: Key1 + gemini-3.1-pro-preview
Step 8: Key2 + gemini-3.1-pro-preview
--- All Gemini exhausted → ModelGateway falls back to Groq ---
Step 9: Groq (llama-3.1-70b)
--- Groq unavailable → Ollama ---
Step 10: Ollama (local model)
```

**Quota math:**
- 1 key × 4 models = ~80 requests/day
- 2 keys × 4 models = **~160 requests/day**
- 3 keys × 4 models = **~240 requests/day**

**Rate-limit detection** (`gemini.py`, `_exhausted_combos` set):
```python
is_rate_limit = (
    "429" in str(e)
    or "quota" in error_str
    or "resource has been exhausted" in error_str
    or "rate limit" in error_str
    or "too many requests" in error_str
)
if is_rate_limit:
    self._exhausted_combos.add(f"{key_suffix}:{model}")
    continue  # try next cascade step
```

**Quota status** — visible at `GET /providers/status` (via `get_quota_status()` method).

**Reset** — `reset_exhausted()` clears all exhausted combos (called when daily quotas refresh).

---

## New Files Created

| File | Purpose |
|------|---------|
| `app/evaluation/discovery.py` | Capability discovery engine |
| `app/evaluation/experimenter.py` | Experiment comparison engine |
| `app/models/experiment.py` | Experiment ORM model |
| `app/schemas/experiment.py` | Experiment Pydantic schemas |
| `app/routers/experiments.py` | `/experiments` API endpoints |
| `API_REFERENCE.md` | Complete endpoint documentation |
| `DAY_6_EXPLANATION.md` | This file |

## Files Modified

| File | Change |
|------|--------|
| `app/providers/gemini.py` | Full rewrite — priority cascade + multi-key pool |
| `app/core/config.py` | Added `gemini_api_keys`, `gemini_rotate_models` |
| `app/schemas/evaluation.py` | Added `EvalDiscoveryRequest`, `EvalDiscoveryResponse`, `MetricRequirement`, `selected_metrics` in `EvalRunCreate` |
| `app/evaluation/orchestrator.py` | Added `selected_metrics` to `_score_case()` and `run_evaluation()` |
| `app/routers/evaluations.py` | Added `POST /evaluations/discover` endpoint |
| `app/main.py` | Registered experiments router, bumped to v0.6.0 |
| `.env` | Added `GEMINI_API_KEYS` (2nd key), `GEMINI_ROTATE_MODELS=true` |

---

## Complete Endpoint List (Day 6)

```
Health & Gateway (3)
  GET  /
  GET  /providers/status
  POST /gateway/generate

Agents (6)
  POST   /agents
  GET    /agents
  GET    /agents/{id}
  PATCH  /agents/{id}
  DELETE /agents/{id}
  POST   /agents/{id}/test-connection

Test Suites (8)
  POST /test-suites
  GET  /test-suites
  GET  /test-suites/{id}
  POST /test-suites/{id}/cases
  POST /test-suites/{id}/upload
  POST /test-suites/seed
  POST /test-suites/{id}/generate
  GET  /test-suites/{id}/generated
  POST /test-suites/{id}/generated/approve

Evaluations (8)
  POST /evaluations/discover        ← NEW Day 6
  POST /evaluations/run
  GET  /evaluations
  GET  /evaluations/{id}
  GET  /evaluations/{id}/cases
  GET  /evaluations/{id}/cases/{case_id}
  GET  /evaluations/{id}/failures
  GET  /evaluations/{id}/report

Experiments (3)  ← NEW Day 6
  POST /experiments
  GET  /experiments
  GET  /experiments/{id}

TOTAL: 28 endpoints
```
