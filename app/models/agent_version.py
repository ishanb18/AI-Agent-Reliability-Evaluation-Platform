"""
AgentVersion ORM Model — Day 7.

Enables proper agent versioning workspace where one parent Agent
can have multiple version children (v1, v2, v3...), each with their
own endpoint, model, system_prompt, and notes.

This fixes the Day 6 gap where users had to register completely
separate agents for V1 and V2 with no link between them.

Design decisions:
  - version_id=None in eval_runs means "unversioned" (backwards compatible)
  - Each version can override endpoint/model/prompt from the parent
  - is_active=False for soft delete; old runs still reference the version
"""

import datetime
import json
from typing import Optional, List

from sqlalchemy import Integer, String, Text, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class AgentVersion(Base):
    """
    Represents one version of a registered agent.

    Table name: agent_versions

    Example versioning story:
      Agent "Support Bot" (id=1)
        ├── v1: gpt-4o,      endpoint=http://bot-v1/chat  (baseline)
        ├── v2: gpt-4o-mini, endpoint=http://bot-v2/chat  (cheaper model test)
        └── v3: gpt-4o,      endpoint=http://bot-v3/chat  (new prompt)

    Evaluations and experiments reference version_id so you can compare
    "how did v1 score vs v2?" using the same test suite.
    """

    __tablename__ = "agent_versions"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Parent Agent ──────────────────────────────────────────────────────────
    agent_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Version Label ─────────────────────────────────────────────────────────
    # Human-readable version tag: "v1", "v2", "2.0.1", "prompt-experiment-3"
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")

    # ── Connection ────────────────────────────────────────────────────────────
    # Override endpoint for this version (if None, inherit from parent agent)
    endpoint: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── Model Config ──────────────────────────────────────────────────────────
    # The LLM provider this version uses (e.g. "openai", "gemini")
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # The exact model (e.g. "gpt-4o", "gpt-4o-mini", "gemini-1.5-flash")
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Prompt Config ─────────────────────────────────────────────────────────
    # System prompt for this version (enables instruction_following metric)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Change Notes ──────────────────────────────────────────────────────────
    # What changed from the previous version — human-readable description
    # Example: "Switched to gpt-4o-mini to reduce cost by ~80%"
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AgentVersion id={self.id} "
            f"agent_id={self.agent_id} "
            f"version='{self.version}' "
            f"model='{self.model}'>"
        )
