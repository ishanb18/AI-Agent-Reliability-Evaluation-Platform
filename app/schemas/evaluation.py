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
    Optionally they can select specific metrics (from POST /evaluations/discover)
    and override the judge model (defaults to Gemini Flash).
    """
    agent_id: int = Field(
        description="ID of the agent to evaluate (must be a REST API agent with an endpoint)",
        examples=[1],
    )
    suite_id: int = Field(
        description="ID of the test suite to run against the agent",
        examples=[1],
    )
    version_id: Optional[int] = Field(
        default=None,
        description=(
            "Evaluate a specific agent version. If None, uses the parent agent's config. "
            "Get version IDs from GET /agents/{id}/versions."
        ),
        examples=[2],
    )
    selected_metrics: Optional[List[str]] = Field(
        default=None,
        description=(
            "Which metrics to evaluate. If None, runs ALL available metrics. "
            "Get the available list from POST /evaluations/discover first. "
            "Example: ['relevance', 'safety', 'correctness']"
        ),
        examples=[["relevance", "safety", "correctness", "tool_accuracy"]],
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


# ── Discovery Schemas (Day 6) ─────────────────────────────────────────────────

class EvalDiscoveryRequest(BaseModel):
    """
    Request body for POST /evaluations/discover.

    Probes the agent with a real test case and returns
    per-metric availability report with how-to-enable guidance.
    """
    agent_id: int = Field(
        description="ID of the agent to probe",
        examples=[1],
    )
    suite_id: int = Field(
        description="ID of the test suite to use as probe context",
        examples=[1],
    )


class MetricRequirement(BaseModel):
    """
    Availability status and requirements for one evaluation metric.
    """
    metric: str = Field(description="Metric name (e.g. 'correctness', 'faithfulness')")
    available: bool = Field(description="Whether this metric can run right now")
    reason: str = Field(description="Why it is available or blocked")
    agent_requirement: Optional[str] = Field(
        default=None,
        description="What the agent's response needs to include",
    )
    test_case_requirement: Optional[str] = Field(
        default=None,
        description="What the test cases need to have filled in",
    )
    how_to_enable: Optional[str] = Field(
        default=None,
        description="Step-by-step instructions to make this metric available",
    )
    group: Optional[str] = Field(
        default=None,
        description="Metric group: 'quality', 'performance', 'rag', 'tools'",
    )


class EvalDiscoveryResponse(BaseModel):
    """
    Response for POST /evaluations/discover.

    Contains the full metric availability report after probing the agent.
    Use 'available_metrics' as the list to pass into selected_metrics
    when calling POST /evaluations/run.
    """
    agent_id: int
    suite_id: int
    agent_name: str
    suite_name: str

    # How the probe went
    probe_status: str = Field(description="'success', 'failed', or 'timeout'")
    probe_error: Optional[str] = Field(default=None, description="Error message if probe failed")
    probe_latency_ms: Optional[float] = None
    probe_input_used: Optional[str] = Field(
        default=None, description="The test case input used for the probe"
    )

    # What the agent returned
    detected_fields: Dict[str, str] = Field(
        default_factory=dict,
        description="Detected response fields: {'answer': 'response', 'tools': 'tool_calls', ...}",
    )
    sample_agent_response: Optional[Dict] = Field(
        default=None,
        description="The actual agent response (truncated) for debugging",
    )

    # The metric availability report
    metrics: List[MetricRequirement] = Field(
        default_factory=list,
        description="Per-metric availability with reasons and how-to-enable instructions",
    )
    available_metrics: List[str] = Field(
        default_factory=list,
        description="Metrics you can select for POST /evaluations/run right now",
    )
    unavailable_metrics: List[str] = Field(
        default_factory=list,
        description="Metrics that need additional setup (see each metric's how_to_enable)",
    )

    # What to do next
    next_steps: Optional[str] = Field(
        default=None,
        description="Guidance on what to do next",
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
