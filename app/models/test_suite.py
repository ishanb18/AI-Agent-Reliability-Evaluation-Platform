"""
TestSuite + TestCase ORM Models — Organize and store test data for agent evaluation.

TestSuite: A named group/folder of test cases (e.g., "Security Tests", "RAG Edge Cases").
TestCase: Individual test question with expected answer, expected tools, and metadata.

Design decisions:
  - One-to-many relationship: TestSuite → TestCase via SQLAlchemy relationship()
  - TestSuite optionally links to an Agent via agent_id FK (nullable — generic suites work for any agent)
  - expected_tools stored as JSON string in Text (SQLite compatibility, same pattern as Agent.components)
  - cascade="all, delete-orphan" → deleting a suite auto-deletes its test cases
"""

import datetime
import json
from typing import Optional, List

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class TestSuite(Base):
    """
    A named collection/folder of test cases.
    Example: "Customer Support Edge Cases", "Security Injection Tests"

    Table name: test_suites
    """

    __tablename__ = "test_suites"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Link to Agent (optional) ──────────────────────────────────────────────
    # A suite CAN be linked to a specific agent, or be generic (agent_id=null).
    # nullable=True: allows "universal" suites that apply to any agent.
    agent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=True
    )

    # ── Relationship: Suite → Cases ───────────────────────────────────────────
    # back_populates: creates a two-way link (suite.test_cases ↔ case.suite)
    # cascade="all, delete-orphan": when a suite is deleted, all its cases are deleted too.
    # lazy="selectin": uses a second SELECT query to load cases efficiently (avoids N+1 problem).
    test_cases: Mapped[List["TestCase"]] = relationship(
        "TestCase",
        back_populates="suite",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TestCase(Base):
    """
    A single test case — one question/prompt with expected answer and metadata.

    Table name: test_cases
    """

    __tablename__ = "test_cases"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── Link to Suite ─────────────────────────────────────────────────────────
    suite_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("test_suites.id"), nullable=False
    )

    # ── Test Data ─────────────────────────────────────────────────────────────
    # input: The test prompt/question we send to the agent
    input: Mapped[str] = mapped_column(Text, nullable=False)
    # expected_answer: The ground truth correct answer (for comparison)
    expected_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # expected_tools: JSON string list of tools the agent SHOULD call
    # e.g. '["get_order", "cancel_order"]'
    expected_tools: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Classification ────────────────────────────────────────────────────────
    # category: What this test evaluates — "general", "rag", "tool_use", "instruction", "security"
    category: Mapped[str] = mapped_column(String(50), default="general")
    # risk_level: How critical this test is — "low", "medium", "high", "critical"
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    # source: Where this test came from — "user", "generated", "security", "seed"
    source: Mapped[str] = mapped_column(String(20), default="user")

    # ── Review Status (Day 5) ─────────────────────────────────────────────────
    # Controls the generate → review → approve workflow for auto-generated tests.
    # "active"         = normal test case, included in evaluation runs
    # "pending_review" = auto-generated, awaiting user approval (EXCLUDED from eval runs)
    # "rejected"       = user reviewed and rejected this case (EXCLUDED from eval runs)
    status: Mapped[str] = mapped_column(String(20), default="active")

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationship back to Suite ────────────────────────────────────────────
    suite: Mapped["TestSuite"] = relationship("TestSuite", back_populates="test_cases")

    # ── Helper Methods ────────────────────────────────────────────────────────
    def get_expected_tools_list(self) -> List[str]:
        """Deserialize expected_tools JSON string → Python list."""
        if not self.expected_tools:
            return []
        try:
            return json.loads(self.expected_tools)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_expected_tools_list(self, tools: List[str]):
        """Serialize Python list → JSON string for storage."""
        self.expected_tools = json.dumps(tools) if tools else None
