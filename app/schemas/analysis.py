"""
Analysis Pydantic Schemas — request/response shapes for Day 5 endpoints.

Covers:
  - Test case generation (request + response + approve)
  - Failure analysis report
  - End-to-end analysis report (spec §5 sections A-D)
"""

import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# ── Test Generation Schemas ───────────────────────────────────────────────────

class GenerationMode(str, Enum):
    """Types of test case generation."""
    edge = "edge"                 # Unusual but valid boundary cases
    adversarial = "adversarial"   # Tricky, misleading inputs
    security = "security"         # Injection, jailbreak, PII probes


class GenerateTestsRequest(BaseModel):
    """Request body for POST /test-suites/{id}/generate."""
    mode: GenerationMode = Field(
        description="Type of test cases to generate",
        examples=["security"],
    )
    count: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of test cases to generate (1-20)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional agent description for better generation context",
        examples=["A customer support chatbot for an e-commerce store"],
    )


class GeneratedCasePreview(BaseModel):
    """Preview of one auto-generated test case (before approval)."""
    id: int
    input: str
    expected_answer: Optional[str] = None
    category: str
    risk_level: str
    source: str
    status: str = "pending_review"


class GenerateTestsResponse(BaseModel):
    """Response for POST /test-suites/{id}/generate."""
    suite_id: int
    mode: str
    generated_count: int
    cases: List[GeneratedCasePreview]
    message: str = Field(
        description="Instruction for the user about next steps",
        examples=["5 test cases generated. Review them and approve via POST /test-suites/{id}/generated/approve"],
    )


class ApproveTestsRequest(BaseModel):
    """Request body for POST /test-suites/{id}/generated/approve."""
    case_ids: List[int] = Field(
        description="IDs of generated test cases to approve (make active)",
        examples=[[1, 3, 5, 7]],
    )
    reject_remaining: bool = Field(
        default=True,
        description="If true, reject all pending_review cases NOT in the approved list",
    )


class ApproveTestsResponse(BaseModel):
    """Response for POST /test-suites/{id}/generated/approve."""
    approved: int = Field(description="Number of cases approved (now active)")
    rejected: int = Field(description="Number of cases rejected")
    still_pending: int = Field(description="Cases still pending review (if reject_remaining=False)")


# ── Failure Analysis Schemas ──────────────────────────────────────────────────

class FailureExample(BaseModel):
    """One representative example from a failure group."""
    test_case_id: Optional[int] = None
    input: str
    agent_output: str
    overall_score: Optional[float] = None
    worst_metric_score: Optional[float] = None


class FailureGroup(BaseModel):
    """One cluster of similar failures."""
    group_name: str = Field(examples=["Low Faithfulness"])
    worst_metric: str = Field(examples=["faithfulness"])
    count: int
    avg_score: Optional[float] = None
    examples: List[FailureExample] = []


class FailureReport(BaseModel):
    """Full failure analysis for one evaluation run."""
    run_id: int
    total_cases: int
    failed_cases: int
    failure_rate: float
    failure_by_category: Dict[str, int] = Field(
        examples=[{"security": 4, "rag": 3, "general": 1}]
    )
    failure_by_metric: Dict[str, Any] = Field(
        description="Worst metric and per-metric averages"
    )
    failure_groups: List[FailureGroup] = []
    recommendations: List[str] = []


# ── Analysis Report Schemas (Spec §5) ─────────────────────────────────────────

class AgentProfile(BaseModel):
    """Section A: What the agent uses."""
    agent_name: str
    provider: Optional[str] = None
    model: Optional[str] = None
    framework: Optional[str] = None
    components: List[str] = []
    connection_type: str = "rest_api"
    version: str = "v1"
    pricing: Optional[Dict[str, Optional[float]]] = None


class LatencyStats(BaseModel):
    """Latency breakdown."""
    avg_ms: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    p99_ms: Optional[float] = None


class TokenStats(BaseModel):
    """Token usage stats."""
    total_input: int = 0
    total_output: int = 0
    avg_per_case: float = 0.0


class CostStats(BaseModel):
    """Cost breakdown."""
    total_estimated_usd: float = 0.0
    avg_per_case_usd: float = 0.0


class PerformanceSummary(BaseModel):
    """Section B: How the system performed."""
    overall_score: Optional[float] = None
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    metric_averages: Dict[str, float] = {}
    latency: LatencyStats = LatencyStats()
    tokens: TokenStats = TokenStats()
    cost: CostStats = CostStats()


class AlternativeModel(BaseModel):
    """One model alternative recommendation."""
    provider: str
    model: str
    cost_per_1m_input: float
    speed: str
    note: str


class AlternativesSummary(BaseModel):
    """Section D: What they could use instead."""
    current: Dict[str, Any]
    alternatives: List[AlternativeModel] = []


class AnalysisReport(BaseModel):
    """Complete end-to-end analysis report (spec §5)."""
    run_id: int
    agent_id: int
    suite_id: int
    generated_at: Optional[str] = None
    sections: Dict[str, Any] = Field(
        description="Four sections: what_they_used, how_it_performed, where_it_failed, alternatives"
    )
