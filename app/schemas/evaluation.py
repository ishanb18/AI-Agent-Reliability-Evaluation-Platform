"""
Evaluation Pydantic Schemas — request/response shapes for /evaluations endpoints.

Schemas defined here:
  EvalRunCreate        → POST /evaluations/run  (what the user sends)
  EvalRunResponse      → run summary (status, counts, avg_score)
  EvalRunCaseResponse  → per-case results (output, latency, tokens, cost)
  EvaluationResponse   → metric scores + judge reasoning for one case
  EvalRunDetailResponse → combined: run summary + all case results + evaluations
"""

import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


# ── Request Schemas ───────────────────────────────────────────────────────────

class EvalRunCreate(BaseModel):
    """
    Request body for POST /evaluations/run.

    The user provides which agent to evaluate and which test suite to run.
    Optionally they can override the judge model (defaults to Gemini Flash).
    """
    agent_id: int = Field(
        description="ID of the agent to evaluate (must be a REST API agent with an endpoint)",
        examples=[1],
    )
    suite_id: int = Field(
        description="ID of the test suite to run against the agent",
        examples=[1],
    )
    judge_provider: Optional[str] = Field(
        default="gemini",
        description="Which LLM provider to use as the evaluation judge (gemini, groq, ollama)",
        examples=["gemini"],
    )
    judge_model: Optional[str] = Field(
        default=None,
        description="Specific judge model name. If None, provider default is used.",
        examples=["gemini-1.5-flash"],
    )


# ── Evaluation Score Schemas ──────────────────────────────────────────────────

class EvaluationResponse(BaseModel):
    """
    All metric scores for one test case evaluation.

    All score fields are 0.0 to 1.0 (or None if metric was skipped).
    None means the metric was NOT evaluated (data was unavailable),
    NOT that it failed. Check metrics_skipped for the reason.
    """
    id: int
    run_case_id: int

    # LLM-as-a-Judge scores (None = skipped)
    correctness: Optional[float] = None
    relevance: Optional[float] = None
    faithfulness: Optional[float] = None
    completeness: Optional[float] = None
    instruction_following: Optional[float] = None
    safety_score: Optional[float] = None

    # Deterministic scores (None = skipped)
    tool_accuracy: Optional[float] = None
    trajectory_score: Optional[float] = None

    # Judge info
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None

    # Per-metric reasoning from the judge LLM
    # Dict format: {"correctness": "Score 0.9 because...", "relevance": "..."}
    reasoning: Optional[Dict[str, str]] = None

    # Audit trail: exactly which metrics ran and which were skipped
    metrics_evaluated: Optional[List[str]] = None
    metrics_skipped: Optional[Dict[str, str]] = None  # metric_name → reason skipped

    # Overall average across all evaluated metrics
    overall_score: Optional[float] = None

    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


# ── Run Case Schema ───────────────────────────────────────────────────────────

class EvalRunCaseResponse(BaseModel):
    """
    What happened when we ran one test case against the agent.

    Captures:
    - What the agent returned (output, raw response)
    - How it performed (latency, tokens, cost)
    - Whether it succeeded or timed out
    - The evaluation scores (nested EvaluationResponse)
    """
    id: int
    run_id: int
    test_case_id: int

    # Agent's response (parsed + raw)
    agent_output: Optional[str] = None
    agent_raw_response: Optional[str] = None

    # Tool calls and context (if agent returned them)
    tool_trace: Optional[List[str]] = None       # deserialized from JSON
    context_chunks: Optional[List[str]] = None   # deserialized from JSON

    # Performance
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    step_count: Optional[int] = None

    # Invocation status
    status: str = "success"   # "success" | "error" | "timeout"
    error: Optional[str] = None

    # Nested evaluation scores (None if evaluation hasn't been computed yet)
    evaluation: Optional[EvaluationResponse] = None

    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


# ── Run Summary Schema ────────────────────────────────────────────────────────

class EvalRunResponse(BaseModel):
    """
    Summary of one complete evaluation run.

    Returned by:
    - POST /evaluations/run (after run completes)
    - GET /evaluations/{run_id}
    - GET /evaluations (list view)
    """
    id: int
    agent_id: int
    suite_id: int
    status: str   # "pending" | "running" | "completed" | "failed"

    # Counts (filled when status = "completed")
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0

    # Overall average score across all cases and metrics (0.0 to 1.0)
    avg_score: Optional[float] = None

    # Which LLM judged this run
    judge_provider: str = "gemini"
    judge_model: Optional[str] = None

    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class EvalRunDetailResponse(BaseModel):
    """
    Full detail response: run summary + all per-case results with evaluations.

    Returned by GET /evaluations/{run_id}/cases
    """
    run: EvalRunResponse
    cases: List[EvalRunCaseResponse] = []

    model_config = {"from_attributes": True}
