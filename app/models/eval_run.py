"""
Evaluation Run ORM Models — store the results of running a test suite against an agent.

Three tables that work together:

  EvalRun      — One complete evaluation run (agent + test suite → N test cases run)
  EvalRunCase  — One test case execution inside a run (what the agent returned)
  Evaluation   — The metric scores for one run case (correctness, relevance, etc.)

Data flow:
  POST /evaluations/run
        ↓
  EvalRun created (status=pending)
        ↓
  For each TestCase in the suite:
      Agent is invoked → EvalRunCase stored (raw output, latency, tokens, cost)
      Metrics computed → Evaluation stored (scores, reasoning, skipped metrics)
        ↓
  EvalRun updated (status=completed, avg_score, passed_cases, failed_cases)
"""

import json
import datetime
from typing import Optional, List

from sqlalchemy import (
    String, Text, Integer, Float, DateTime, Boolean,
    ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


# ── Table 1: EvalRun ──────────────────────────────────────────────────────────

class EvalRun(Base):
    """
    Represents one complete evaluation session.

    A user picks an agent + a test suite and hits "Run Evaluation".
    That creates one EvalRun. It tracks the overall progress and
    aggregated scores across all test cases in the suite.

    Table name: eval_runs
    """

    __tablename__ = "eval_runs"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── What is being evaluated ───────────────────────────────────────────────
    # Which agent are we evaluating? (must exist in agents table)
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False, index=True
    )
    # Which specific version of the agent? (nullable = unversioned, backwards compatible)
    version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("agent_versions.id"), nullable=True, index=True
    )
    # Which test suite are we running? (must exist in test_suites table)
    suite_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("test_suites.id"), nullable=False, index=True
    )

    # ── Run Status ────────────────────────────────────────────────────────────
    # "pending"   → created but not yet started
    # "running"   → actively executing test cases
    # "completed" → all cases done, scores aggregated
    # "failed"    → run crashed before completing (e.g. agent unreachable)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # ── Case Counts (filled in when run completes) ────────────────────────────
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)   # score >= 0.5
    failed_cases: Mapped[int] = mapped_column(Integer, default=0)   # score < 0.5

    # ── Aggregated Score ──────────────────────────────────────────────────────
    # Average across all metrics and all cases that successfully ran.
    # NULL until run completes.
    avg_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Which LLM was used as the judge ──────────────────────────────────────
    judge_provider: Mapped[str] = mapped_column(String(50), default="gemini")
    judge_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    # Access all case results via run.cases
    cases: Mapped[List["EvalRunCase"]] = relationship(
        "EvalRunCase",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# ── Table 2: EvalRunCase ──────────────────────────────────────────────────────

class EvalRunCase(Base):
    """
    Represents one test case execution within an evaluation run.

    For each TestCase in the suite, we invoke the agent and store:
    - What the agent actually returned (raw + parsed)
    - Performance metrics (latency, tokens, cost)
    - Whether the invocation succeeded or timed out

    Table name: eval_run_cases
    """

    __tablename__ = "eval_run_cases"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── Links to parent run and source test case ──────────────────────────────
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eval_runs.id"), nullable=False, index=True
    )
    test_case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("test_cases.id"), nullable=False, index=True
    )

    # ── What the Agent Returned ───────────────────────────────────────────────
    # The extracted answer text (after applying response_mapping).
    # This is what gets evaluated by the judge.
    agent_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # The full raw JSON response from the agent, stored for debugging.
    # Useful when something goes wrong and you want to see exactly what came back.
    agent_raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Tool calls extracted from the response (JSON-encoded list of tool names).
    # e.g. '["get_order", "cancel_order"]'
    # Used for tool_accuracy and trajectory evaluation.
    tool_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Retrieved context chunks extracted from the response (JSON-encoded list).
    # e.g. '["Policy: returns within 30 days...", "Refund process: ..."]'
    # Used for faithfulness evaluation in RAG agents.
    context_chunks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Performance Metrics ───────────────────────────────────────────────────
    # Measured from the moment we sent the request to when we got the response.
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Token counts extracted from the agent's metadata field (if available).
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Estimated cost in USD based on token counts and model pricing table.
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Number of steps the agent took (from metadata or tool_trace count).
    step_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Invocation Status ─────────────────────────────────────────────────────
    # "success" → agent responded with a valid response
    # "error"   → agent returned HTTP 4xx/5xx
    # "timeout" → agent did not respond within 30 seconds
    status: Mapped[str] = mapped_column(String(20), default="success")
    # If status != success, what went wrong.
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    run: Mapped["EvalRun"] = relationship("EvalRun", back_populates="cases")
    evaluation: Mapped[Optional["Evaluation"]] = relationship(
        "Evaluation",
        back_populates="run_case",
        uselist=False,         # one-to-one: one EvalRunCase → one Evaluation
        cascade="all, delete-orphan",
    )

    # ── Helper Methods ────────────────────────────────────────────────────────

    def get_tool_trace_list(self) -> List[str]:
        """Deserialize tool_trace JSON string → Python list of tool names."""
        if not self.tool_trace:
            return []
        try:
            return json.loads(self.tool_trace)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_tool_trace_list(self, tools: List[str]):
        """Serialize Python list of tool names → JSON string for storage."""
        self.tool_trace = json.dumps(tools) if tools else None

    def get_context_chunks_list(self) -> List[str]:
        """Deserialize context_chunks JSON string → Python list of chunks."""
        if not self.context_chunks:
            return []
        try:
            return json.loads(self.context_chunks)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_context_chunks_list(self, chunks: List[str]):
        """Serialize Python list of context chunks → JSON string for storage."""
        self.context_chunks = json.dumps(chunks) if chunks else None


# ── Table 3: Evaluation ───────────────────────────────────────────────────────

class Evaluation(Base):
    """
    The metric scores for one EvalRunCase.

    Every float score is on a 0.0 to 1.0 scale:
      1.0 = perfect / fully passes
      0.5 = borderline / partially passes
      0.0 = completely fails

    All score columns are NULLABLE. A NULL score means that metric was
    not evaluated for this case (data was unavailable, not that it failed).

    For example:
      faithfulness = NULL  →  agent didn't return context (not a RAG agent)
      tool_accuracy = NULL →  test case had no expected_tools
      correctness = NULL   →  test case had no expected_answer

    The metrics_evaluated and metrics_skipped columns record exactly which
    metrics ran and which were skipped (with reasons), for full auditability.

    Table name: evaluations
    """

    __tablename__ = "evaluations"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── Link to the run case this scores ─────────────────────────────────────
    run_case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eval_run_cases.id"), nullable=False, unique=True
    )

    # ── LLM-as-a-Judge Metrics (qualitative, require LLM call) ───────────────

    # Is the answer factually correct compared to expected_answer?
    # Requires: expected_answer in test case
    correctness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Does the answer address what was actually asked?
    # Requires: nothing — always attempted
    relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Is the answer grounded in the retrieved context? (RAG hallucination check)
    # Requires: context_chunks in agent response
    faithfulness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Does the answer cover all key points from expected_answer?
    # Requires: expected_answer in test case
    completeness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Does the answer follow agent's system prompt constraints?
    # Requires: agent has a system_prompt configured
    instruction_following: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Does the answer contain harmful/unsafe content?
    # Requires: nothing — always attempted
    safety_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Deterministic Metrics (rule-based, no LLM required) ──────────────────

    # Did the agent call the right tools?
    # Formula: len(actual ∩ expected) / len(expected)
    # Requires: test case has expected_tools AND agent returned tool_calls
    tool_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Did the agent call tools in the right order?
    # Formula: longest common subsequence / len(expected)
    # Requires: same as tool_accuracy
    trajectory_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Judge Metadata ────────────────────────────────────────────────────────
    # Which LLM was used to judge (mirrors EvalRun.judge_provider for easy querying)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Combined chain-of-thought reasoning from all judge calls.
    # Stored as JSON: {"correctness": "Score 0.9 because...", "relevance": "..."}
    # This makes every score auditable and explainable.
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Metric Tracking ───────────────────────────────────────────────────────
    # JSON list of metric names that were successfully evaluated.
    # e.g. '["correctness", "relevance", "safety", "latency"]'
    metrics_evaluated: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # JSON dict of metric names → reason they were skipped.
    # e.g. '{"faithfulness": "no context in response", "tool_accuracy": "no expected_tools"}'
    metrics_skipped: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    run_case: Mapped["EvalRunCase"] = relationship("EvalRunCase", back_populates="evaluation")

    # ── Helper Methods ────────────────────────────────────────────────────────

    def get_reasoning_dict(self) -> dict:
        """Deserialize reasoning JSON string → Python dict."""
        if not self.reasoning:
            return {}
        try:
            return json.loads(self.reasoning)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_reasoning_dict(self, reasoning: dict):
        """Serialize Python dict of per-metric reasoning → JSON string."""
        self.reasoning = json.dumps(reasoning) if reasoning else None

    def get_metrics_evaluated_list(self) -> List[str]:
        """Deserialize metrics_evaluated JSON string → Python list."""
        if not self.metrics_evaluated:
            return []
        try:
            return json.loads(self.metrics_evaluated)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_metrics_evaluated_list(self, metrics: List[str]):
        """Serialize Python list of evaluated metric names → JSON string."""
        self.metrics_evaluated = json.dumps(metrics) if metrics else None

    def get_metrics_skipped_dict(self) -> dict:
        """Deserialize metrics_skipped JSON string → Python dict."""
        if not self.metrics_skipped:
            return {}
        try:
            return json.loads(self.metrics_skipped)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_metrics_skipped_dict(self, skipped: dict):
        """Serialize Python dict of skipped metrics → JSON string."""
        self.metrics_skipped = json.dumps(skipped) if skipped else None

    def compute_overall_score(self) -> Optional[float]:
        """
        Compute average of all non-None scores.
        Used to determine if this case passed (>= 0.5) or failed (< 0.5).

        Returns None if no metrics were evaluated at all.
        """
        scores = [
            s for s in [
                self.correctness, self.relevance, self.faithfulness,
                self.completeness, self.safety_score, self.instruction_following,
                self.tool_accuracy, self.trajectory_score,
            ]
            if s is not None
        ]
        if not scores:
            return None
        return round(sum(scores) / len(scores), 4)
