"""
Experiment Pydantic Schemas — Day 6.

Covers request/response shapes for /experiments endpoints.
"""

import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ── Request Schema ────────────────────────────────────────────────────────────

class ExperimentCreate(BaseModel):
    """
    Request body for POST /experiments.

    Two modes:
      Mode A — Compare two existing runs (fastest):
        Provide baseline_run_id + candidate_run_id.
        Both must already be completed evaluation runs.

      Mode B — Run fresh comparison (convenience):
        Provide baseline_agent_id + candidate_agent_id + suite_id.
        Platform will run both agents against the suite and then compare.
        This takes longer but is simpler.
    """
    # Mode A: Compare existing runs
    baseline_run_id: Optional[int] = Field(
        default=None,
        description="ID of the baseline (V1) completed evaluation run",
        examples=[1],
    )
    candidate_run_id: Optional[int] = Field(
        default=None,
        description="ID of the candidate (V2) completed evaluation run",
        examples=[2],
    )

    # Mode B: Run fresh comparison
    baseline_agent_id: Optional[int] = Field(
        default=None,
        description="[Mode B] Agent ID for V1 — platform will run a fresh evaluation",
        examples=[1],
    )
    candidate_agent_id: Optional[int] = Field(
        default=None,
        description="[Mode B] Agent ID for V2 — platform will run a fresh evaluation",
        examples=[2],
    )
    suite_id: Optional[int] = Field(
        default=None,
        description="[Mode B] Test suite to run both agents against",
        examples=[1],
    )

    # Configuration
    name: Optional[str] = Field(
        default=None,
        description="Human-readable name for this experiment",
        examples=["Support Bot V1 vs V2"],
    )
    thresholds: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Custom per-metric pass thresholds (0.0-1.0). "
            "Defaults: correctness=0.70, safety=0.85, faithfulness=0.70, etc."
        ),
        examples=[{"correctness": 0.80, "safety_score": 0.95}],
    )
    judge_provider: Optional[str] = Field(
        default="gemini",
        description="[Mode B only] Which LLM provider to use as judge",
    )


# ── Response Schemas ──────────────────────────────────────────────────────────

class MetricDiff(BaseModel):
    """Comparison of one metric between baseline and candidate."""
    metric: str = Field(description="Metric name (e.g. 'correctness')")
    baseline: Optional[float] = Field(description="Baseline (V1) average score")
    candidate: Optional[float] = Field(description="Candidate (V2) average score")
    delta: Optional[float] = Field(description="Candidate - Baseline (positive = improved)")
    status: str = Field(description="'improved', 'regressed', 'unchanged', 'new', or 'removed'")
    threshold: Optional[float] = Field(description="Pass/fail threshold for this metric")
    meets_threshold: Optional[bool] = Field(description="Whether candidate meets the threshold")


class RunSummary(BaseModel):
    """High-level stats for one evaluation run in the comparison."""
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    avg_score: Optional[float] = None
    total_cost_usd: float = 0.0
    avg_tokens_per_case: float = 0.0
    agent_id: Optional[int] = None
    suite_id: Optional[int] = None
    judge_provider: Optional[str] = None


class ExperimentResult(BaseModel):
    """
    Full experiment result returned by POST /experiments and GET /experiments/{id}.
    """
    id: int
    name: Optional[str] = None

    # Run references
    baseline_run_id: int
    candidate_run_id: int
    baseline_agent_id: Optional[int] = None
    candidate_agent_id: Optional[int] = None
    suite_id: Optional[int] = None

    # The VERDICT
    verdict: str = Field(
        description="'pass', 'review', or 'fail'",
        examples=["pass"],
    )
    verdict_emoji: str = Field(
        description="Human-readable verdict with emoji",
        examples=["✅ PASS"],
    )

    # Metric comparison
    metric_diffs: List[MetricDiff] = []

    # Summaries
    baseline_summary: Optional[RunSummary] = None
    candidate_summary: Optional[RunSummary] = None

    # What drove the verdict
    thresholds_used: Dict[str, float] = {}
    improvements: List[str] = Field(
        default_factory=list,
        description="Metrics where candidate improved over baseline",
    )
    regressions: List[str] = Field(
        default_factory=list,
        description="Metrics where candidate regressed from baseline",
    )
    fail_reasons: List[str] = Field(
        default_factory=list,
        description="Why verdict is FAIL (which metrics are below threshold)",
    )
    review_reasons: List[str] = Field(
        default_factory=list,
        description="Why verdict is REVIEW (regressions from baseline)",
    )

    # Recommendations
    suggestions: List[str] = Field(
        default_factory=list,
        description="Actionable improvement suggestions",
    )

    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class ExperimentListItem(BaseModel):
    """Lightweight experiment item for list view."""
    id: int
    name: Optional[str] = None
    baseline_run_id: int
    candidate_run_id: int
    verdict: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}
