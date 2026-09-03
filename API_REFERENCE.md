# AI Agent Reliability Platform — API Reference

**Version**: 0.6.0 | **Base URL**: `http://localhost:8000`

All endpoints accept and return JSON. Interactive docs available at `/docs` (Swagger UI).

---

## Table of Contents

1. [Health & Gateway](#1-health--gateway)
2. [Agents](#2-agents)
3. [Test Suites](#3-test-suites)
4. [Evaluations](#4-evaluations)
5. [Experiments](#5-experiments)

---

## 1. Health & Gateway

### `GET /`
Health check. Returns platform status and version.

**Response**
```json
{ "status": "ok", "version": "0.6.0", "env": "development" }
```

---

### `GET /providers/status`
Real-time telemetry for all LLM providers (Gemini, Groq, Ollama).

**Response** — `Dict[str, ProviderStats]`
```json
{
  "gemini": {
    "provider": "gemini",
    "is_available": true,
    "status": "HEALTHY",
    "request_count": 12,
    "success_count": 11,
    "error_count": 1,
    "input_tokens": 4200,
    "output_tokens": 1800,
    "avg_latency_ms": 843.5,
    "supported_models": {
      "default": "gemini-3.5-flash",
      "fast": "gemini-3.5-flash-lite",
      "reasoning": "gemini-3.1-pro-preview",
      "code": "gemini-3.6-flash"
    }
  }
}
```

> **Status values**: `HEALTHY` | `WARNING` (>20% error rate) | `UNAVAILABLE` (no API key)

---

### `POST /gateway/generate`
Generate text via the Model Gateway with automatic fallback.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | ✅ | Input text to send to the model |
| `provider` | string | ❌ | Primary provider: `gemini`, `groq`, `ollama` (default: `gemini`) |
| `model` | string | ❌ | Exact model name (overrides job_type) |
| `job_type` | string | ❌ | `default`, `fast`, `reasoning`, `code` |
| `enable_fallback` | bool | ❌ | Auto-switch to next provider on failure (default: `true`) |
| `fallback_order` | list[str] | ❌ | Custom fallback sequence e.g. `["groq", "ollama"]` |

**Example Request**
```json
{
  "prompt": "Explain RAG in 2 sentences",
  "provider": "gemini",
  "job_type": "fast",
  "enable_fallback": true
}
```

**Response**
```json
{
  "run_id": 42,
  "provider": "gemini",
  "model": "gemini-3.5-flash-lite",
  "prompt": "Explain RAG in 2 sentences",
  "response": "RAG combines retrieval with generation...",
  "latency_ms": 621.3,
  "input_tokens": 12,
  "output_tokens": 48,
  "status": "success",
  "fallback_used": false,
  "primary_provider": "gemini"
}
```

---

## 2. Agents

### `POST /agents`
Register a new AI agent. Automatically runs a connection handshake for REST API agents.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Human-readable agent name |
| `description` | string | ❌ | What this agent does |
| `connection_type` | string | ❌ | `rest_api` or `python_sdk` (default: `rest_api`) |
| `endpoint` | string | ❌ | Agent's REST URL (required for rest_api) |
| `version` | string | ❌ | Version tag, e.g. `"v2"` (default: `"v1"`) |
| `provider` | string | ❌ | LLM the agent uses: `openai`, `gemini`, `anthropic` |
| `model` | string | ❌ | Specific model: `gpt-4o`, `gemini-1.5-flash` |
| `framework` | string | ❌ | Agent framework: `langgraph`, `crewai`, `custom` |
| `components` | list[str] | ❌ | Declared capabilities: `llm_call`, `retrieval`, `tools`, `multi_step` |
| `response_mapping` | dict | ❌ | Custom field mapping for non-standard responses |
| `auto_discover` | bool | ❌ | Auto-detect components from traces (default: `true`) |

**Example Request**
```json
{
  "name": "Customer Support Bot v1",
  "description": "Handles order cancellations and refunds",
  "connection_type": "rest_api",
  "endpoint": "http://localhost:9000/chat",
  "provider": "openai",
  "model": "gpt-4o",
  "components": ["llm_call", "tools"]
}
```

**Response** — includes agent profile + connection test result
```json
{
  "agent": {
    "id": 1,
    "name": "Customer Support Bot v1",
    "connection_type": "rest_api",
    "endpoint": "http://localhost:9000/chat",
    "connection_status": "connected",
    "is_active": true,
    "created_at": "2026-09-03T15:00:00Z"
  },
  "connection_test": {
    "status": "connected",
    "latency_ms": 243.1,
    "status_code": 200,
    "detected_fields": { "answer": "response", "tools": "tool_calls" },
    "available_metrics": ["correctness", "relevance", "safety", "tool_accuracy", "latency"],
    "unavailable_metrics": ["rag_faithfulness", "context_precision"]
  }
}
```

---

### `GET /agents`
List all registered agents.

**Query Params**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `is_active` | bool | `true` | Filter by active/inactive status |

---

### `GET /agents/{agent_id}`
Get a single agent profile by ID.

**Response** — full `AgentResponse` (same shape as agent object above)

---

### `PATCH /agents/{agent_id}`
Partial update of an agent profile. Only provided fields are updated.

**Request Body** — all fields optional, same fields as `POST /agents`

---

### `DELETE /agents/{agent_id}`
Soft-delete an agent (sets `is_active=false`). Preserves historical run data.

**Response**
```json
{ "detail": "Agent 'Support Bot' (id=1) deactivated", "is_active": false }
```

---

### `POST /agents/{agent_id}/test-connection`
Re-test agent connectivity and re-detect response format.

**Response** — `ConnectionTestResult`
```json
{
  "status": "connected",
  "latency_ms": 187.4,
  "detected_fields": { "answer": "output", "context": "retrieved_chunks" },
  "available_metrics": ["relevance", "safety", "faithfulness", "latency"],
  "unavailable_metrics": ["tool_accuracy", "trajectory"]
}
```

---

## 3. Test Suites

### `POST /test-suites`
Create a new empty test suite.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Suite name |
| `description` | string | ❌ | What this suite covers |
| `agent_id` | int | ❌ | Link to a specific agent (null = universal) |

**Response** — `TestSuiteResponse` with `id`, `name`, `case_count: 0`

---

### `GET /test-suites`
List all test suites (lightweight, without test case details).

---

### `GET /test-suites/{suite_id}`
Get full suite with all test cases.

**Response** — includes `test_cases` list with all case details

---

### `POST /test-suites/{suite_id}/cases`
Add a single test case to a suite.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input` | string | ✅ | The test prompt/question sent to the agent |
| `expected_answer` | string | ❌ | Ground-truth answer (enables correctness + completeness metrics) |
| `expected_tools` | list[str] | ❌ | Tools the agent SHOULD call (enables tool_accuracy + trajectory) |
| `category` | string | ❌ | `general`, `rag`, `tool_use`, `instruction`, `security` |
| `risk_level` | string | ❌ | `low`, `medium`, `high`, `critical` |
| `source` | string | ❌ | `user`, `generated`, `security`, `seed` |
| `status` | string | ❌ | `active`, `pending_review`, `rejected` (default: `active`) |

**Example Request**
```json
{
  "input": "Cancel my order #12345",
  "expected_answer": "I've cancelled order #12345. Refund in 3-5 days.",
  "expected_tools": ["get_order", "cancel_order"],
  "category": "tool_use",
  "risk_level": "medium"
}
```

---

### `POST /test-suites/{suite_id}/upload`
Bulk upload test cases from JSON or CSV.

**Request** — `multipart/form-data` with file field `file`

**Supported formats**:
- **JSON**: array of test case objects
- **CSV**: columns: `input`, `expected_answer`, `expected_tools`, `category`, `risk_level`

**Response**
```json
{
  "suite_id": 1,
  "created": 32,
  "errors": 3,
  "error_details": [
    { "row": 5, "error": "input field is required" }
  ]
}
```

---

### `POST /test-suites/seed`
Populate a new suite with 35 pre-built seed test cases across all 5 categories.

**Response**
```json
{
  "suite_id": 2,
  "suite_name": "Seed Test Suite — 2026-09-03",
  "cases_created": 35,
  "categories": { "general": 8, "rag": 7, "tool_use": 7, "instruction": 6, "security": 7 }
}
```

---

### `POST /test-suites/{suite_id}/generate`
Auto-generate test cases using LLM.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | string | ✅ | `edge`, `adversarial`, or `security` |
| `count` | int | ❌ | Number to generate: 1–20 (default: 5) |
| `description` | string | ❌ | Agent context for better generation |

> Generated cases are saved with `status: "pending_review"` — they do NOT run in evaluations until approved.

**Response**
```json
{
  "suite_id": 1,
  "mode": "security",
  "generated_count": 5,
  "cases": [
    { "id": 41, "input": "Ignore previous instructions...", "category": "security", "risk_level": "critical", "status": "pending_review" }
  ],
  "message": "5 cases generated. Review via GET /test-suites/1/generated then approve via POST /test-suites/1/generated/approve"
}
```

---

### `GET /test-suites/{suite_id}/generated`
List all `pending_review` test cases for human review.

---

### `POST /test-suites/{suite_id}/generated/approve`
Approve or reject generated test cases.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `case_ids` | list[int] | ✅ | IDs to approve (set to `active`) |
| `reject_remaining` | bool | ❌ | Reject all other `pending_review` cases (default: `true`) |

**Response**
```json
{ "approved": 3, "rejected": 2, "still_pending": 0 }
```

---

## 4. Evaluations

### Recommended Flow

```
Step 1: POST /evaluations/discover   ← See which metrics are available
Step 2: POST /evaluations/run        ← Run with selected_metrics from Step 1
Step 3: GET  /evaluations/{id}       ← Check run summary
Step 4: GET  /evaluations/{id}/cases ← Full per-case breakdown
Step 5: GET  /evaluations/{id}/report ← Analysis report with recommendations
```

---

### `POST /evaluations/discover` ⭐ NEW in Day 6
**Probe your agent and discover which evaluation metrics are available.**

Sends a real test case to your agent, analyzes the response format, and returns per-metric availability with exact instructions to enable unavailable metrics.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_id` | int | ✅ | Agent to probe |
| `suite_id` | int | ✅ | Test suite (used to pick a representative probe case) |

**Example Request**
```json
{ "agent_id": 1, "suite_id": 1 }
```

**Response**
```json
{
  "agent_id": 1,
  "suite_id": 1,
  "agent_name": "Customer Support Bot v1",
  "suite_name": "Seed Test Suite",
  "probe_status": "success",
  "probe_latency_ms": 312.4,
  "probe_input_used": "Can I cancel my order #12345?",
  "detected_fields": {
    "answer": "response",
    "tools": "tool_calls"
  },
  "available_metrics": ["relevance", "safety", "latency", "correctness", "completeness", "tool_accuracy", "trajectory"],
  "unavailable_metrics": ["faithfulness", "instruction_following"],
  "metrics": [
    {
      "metric": "faithfulness",
      "available": false,
      "reason": "Agent response does not contain retrieved context chunks",
      "agent_requirement": "Context chunks under key 'context', 'retrieved_chunks', 'documents', or 'sources'",
      "test_case_requirement": "Any test case input",
      "how_to_enable": "Add retrieved context chunks to your agent's response:\n  {\"answer\": \"Based on policy...\", \"context\": [\"chunk1...\", \"chunk2...\"]}\nThis enables hallucination detection.",
      "group": "rag"
    }
  ],
  "next_steps": "You have 7 metrics available. Call POST /evaluations/run with 'selected_metrics': ['relevance', 'safety', ...]"
}
```

---

### `POST /evaluations/run`
Trigger a new evaluation run.

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_id` | int | ✅ | Agent to evaluate |
| `suite_id` | int | ✅ | Test suite to run |
| `selected_metrics` | list[str] | ❌ | Which metrics to run. `null` = run all available. Get list from `/discover` |
| `judge_provider` | string | ❌ | LLM judge: `gemini`, `groq`, `ollama` (default: `gemini`) |
| `judge_model` | string | ❌ | Specific judge model (default: provider default) |

**Available metric names**: `relevance`, `safety_score`, `correctness`, `completeness`, `faithfulness`, `tool_accuracy`, `trajectory_score`, `instruction_following`

**Example Request** — run only quality metrics
```json
{
  "agent_id": 1,
  "suite_id": 1,
  "selected_metrics": ["relevance", "safety_score", "correctness"],
  "judge_provider": "gemini"
}
```

**Response** — run summary (scores updated when complete)
```json
{
  "id": 5,
  "agent_id": 1,
  "suite_id": 1,
  "status": "completed",
  "total_cases": 35,
  "passed_cases": 28,
  "failed_cases": 7,
  "avg_score": 0.74,
  "judge_provider": "gemini",
  "started_at": "2026-09-03T15:00:00Z",
  "completed_at": "2026-09-03T15:04:32Z"
}
```

---

### `GET /evaluations`
List all evaluation runs.

**Query Params**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `agent_id` | int | — | Filter by agent |
| `status` | string | — | Filter: `pending`, `running`, `completed`, `failed` |
| `limit` | int | 50 | Max results (1–200) |

---

### `GET /evaluations/{run_id}`
Get run summary (no per-case details).

---

### `GET /evaluations/{run_id}/cases`
**Full per-case breakdown** — what the agent said and how each response was scored.

**Response** — `EvalRunDetailResponse`
```json
{
  "run": { "id": 5, "avg_score": 0.74, "status": "completed" },
  "cases": [
    {
      "id": 101,
      "test_case_id": 3,
      "agent_output": "Your order has been cancelled.",
      "latency_ms": 421.0,
      "input_tokens": 85,
      "output_tokens": 24,
      "estimated_cost": 0.0000124,
      "status": "success",
      "evaluation": {
        "correctness": 0.88,
        "relevance": 0.92,
        "safety_score": 0.97,
        "tool_accuracy": 0.75,
        "overall_score": 0.88,
        "reasoning": {
          "correctness": "Agent correctly confirmed cancellation and mentioned refund timeline.",
          "tool_accuracy": "Called get_order but missed send_confirmation."
        },
        "metrics_evaluated": ["correctness", "relevance", "safety_score", "tool_accuracy"],
        "metrics_skipped": { "faithfulness": "no context chunks in agent response" }
      }
    }
  ]
}
```

---

### `GET /evaluations/{run_id}/cases/{case_id}`
Single case detail — same shape as one case in the above response.

---

### `GET /evaluations/{run_id}/failures`
Failure analysis — groups failed cases by category and worst metric.

**Response** — `FailureReport`
```json
{
  "run_id": 5,
  "total_cases": 35,
  "failed_cases": 7,
  "failure_rate": 0.2,
  "failure_by_category": { "security": 4, "tool_use": 2, "general": 1 },
  "failure_by_metric": { "worst_metric": "tool_accuracy", "tool_accuracy_avg": 0.41 },
  "failure_groups": [
    {
      "group_name": "Low Tool Accuracy",
      "worst_metric": "tool_accuracy",
      "count": 4,
      "avg_score": 0.38,
      "examples": [{ "input": "Cancel order...", "agent_output": "...", "overall_score": 0.41 }]
    }
  ],
  "recommendations": [
    "4 failures in tool_use category — check tool selection logic",
    "Consider adding more tool_use test cases to the suite"
  ]
}
```

---

### `GET /evaluations/{run_id}/report`
**Complete end-to-end analysis report** — all 4 sections (A–D from spec §5).

**Response** — `AnalysisReport`
```json
{
  "run_id": 5,
  "agent_id": 1,
  "suite_id": 1,
  "generated_at": "2026-09-03T16:00:00Z",
  "sections": {
    "what_they_used": {
      "agent_name": "Customer Support Bot v1",
      "provider": "openai",
      "model": "gpt-4o",
      "components": ["llm_call", "tools"],
      "pricing": { "input_per_1m": 5.0, "output_per_1m": 15.0 }
    },
    "how_it_performed": {
      "overall_score": 0.74,
      "passed_cases": 28,
      "failed_cases": 7,
      "metric_averages": { "correctness": 0.78, "safety_score": 0.95 },
      "latency": { "avg_ms": 418.0, "p50_ms": 380.0, "p95_ms": 920.0 },
      "cost": { "total_estimated_usd": 0.00421, "avg_per_case_usd": 0.00012 }
    },
    "where_it_failed": { "...failure analysis..." },
    "alternatives": {
      "current": { "provider": "openai", "cost_per_1m_input": 5.0 },
      "alternatives": [
        { "provider": "groq", "model": "llama-3.1-70b", "cost_per_1m_input": 0.59, "speed": "fast", "note": "10x cheaper, comparable quality" }
      ]
    }
  }
}
```

---

## 5. Experiments ⭐ NEW in Day 6

Compare two evaluation runs (V1 vs V2) and get a deployment verdict.

### `POST /experiments`
Create a new experiment comparison.

**TWO MODES:**

**Mode A — Compare existing runs** (fast, recommended):
```json
{
  "baseline_run_id": 3,
  "candidate_run_id": 7,
  "name": "Support Bot V1 vs V2",
  "thresholds": { "correctness": 0.80, "safety_score": 0.95 }
}
```

**Mode B — Run fresh comparison** (slower, convenient):
```json
{
  "baseline_agent_id": 1,
  "candidate_agent_id": 2,
  "suite_id": 1,
  "name": "Support Bot V1 vs V2",
  "judge_provider": "gemini"
}
```

**Custom Thresholds** (optional, all default to shown values):
| Metric | Default Threshold |
|--------|------------------|
| `correctness` | 0.70 |
| `relevance` | 0.70 |
| `safety_score` | 0.85 |
| `faithfulness` | 0.70 |
| `completeness` | 0.65 |
| `tool_accuracy` | 0.70 |
| `trajectory_score` | 0.65 |

**Verdict Rules**:
- ✅ **PASS** — all metrics ≥ threshold AND no regression > 5% from baseline
- ⚠️ **REVIEW** — all metrics pass thresholds BUT regression > 5% on some metric
- ❌ **FAIL** — any metric below its threshold

**Response** — `ExperimentResult`
```json
{
  "id": 1,
  "name": "Support Bot V1 vs V2",
  "baseline_run_id": 3,
  "candidate_run_id": 7,
  "verdict": "review",
  "verdict_emoji": "⚠️ REVIEW",
  "metric_diffs": [
    {
      "metric": "correctness",
      "baseline": 0.71,
      "candidate": 0.82,
      "delta": 0.11,
      "status": "improved",
      "threshold": 0.70,
      "meets_threshold": true
    },
    {
      "metric": "tool_accuracy",
      "baseline": 0.88,
      "candidate": 0.79,
      "delta": -0.09,
      "status": "regressed",
      "threshold": 0.70,
      "meets_threshold": true
    }
  ],
  "improvements": ["correctness", "relevance"],
  "regressions": ["tool_accuracy"],
  "fail_reasons": [],
  "review_reasons": ["tool_accuracy regressed 0.09 from baseline (0.88 → 0.79)"],
  "suggestions": [
    "[REGRESSION] tool_accuracy dropped 9% from baseline. Investigate what changed between V1 and V2."
  ],
  "thresholds_used": { "correctness": 0.70, "safety_score": 0.85 },
  "baseline_summary": { "total_cases": 35, "avg_score": 0.74, "total_cost_usd": 0.0042 },
  "candidate_summary": { "total_cases": 35, "avg_score": 0.81, "total_cost_usd": 0.0051 }
}
```

---

### `GET /experiments`
List all experiments.

**Query Params**
| Param | Type | Description |
|-------|------|-------------|
| `verdict` | string | Filter: `pass`, `review`, `fail` |
| `limit` | int | Max results (default: 50) |

**Response** — lightweight list
```json
[
  { "id": 1, "name": "V1 vs V2", "baseline_run_id": 3, "candidate_run_id": 7, "verdict": "review", "created_at": "..." }
]
```

---

### `GET /experiments/{experiment_id}`
Get full experiment result by ID (same shape as `POST /experiments` response).

---

## Error Responses

All endpoints return standard error shapes:

| Status | When |
|--------|------|
| `400` | Bad request (missing required fields, validation errors, agent has no endpoint) |
| `404` | Resource not found (agent_id, suite_id, run_id, experiment_id) |
| `500` | Server error (unexpected failure, corrupt data) |
| `502` | Gateway error (all LLM providers failed) |

**Error body**:
```json
{ "detail": "Agent with id=99 not found" }
```

---

## Agent Response Format Guide

What your agent needs to return to unlock each metric:

```json
{
  "answer": "...",                 // ← enables: relevance, safety, correctness, completeness
  "tool_calls": ["get_order"],    // ← enables: tool_accuracy, trajectory  (+ expected_tools in test case)
  "context": ["chunk1", ...],     // ← enables: faithfulness
  "metadata": {
    "input_tokens": 85,           // ← enables: accurate cost calculation
    "output_tokens": 24
  }
}
```
