"""
AgentVersion Pydantic Schemas — Day 7.

Request/response shapes for /agents/{id}/versions endpoints.
"""

import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


# ── Request Schemas ───────────────────────────────────────────────────────────

class AgentVersionCreate(BaseModel):
    """
    Request body for POST /agents/{id}/versions.

    Creates a new version of an agent. All fields are optional —
    anything not provided is inherited from the parent agent or
    the version being forked from.

    Typical usage:
      Fork to test a new model:
        { "version": "v2", "model": "gpt-4o-mini", "notes": "Testing cheaper model" }

      Fork to test a new prompt:
        { "version": "v3", "system_prompt": "You are a strict customer agent...",
          "notes": "Tighter system prompt to reduce hallucination" }

      Fork with completely different endpoint (rebuilt agent):
        { "version": "v4", "endpoint": "http://new-bot:9001/chat",
          "model": "claude-3.5-sonnet", "notes": "Migrated to Claude" }
    """
    version: str = Field(
        description="Version label. Examples: 'v2', '2.0.0', 'gpt4o-mini-test'",
        examples=["v2"],
    )
    endpoint: Optional[str] = Field(
        default=None,
        description="Override the agent endpoint for this version. If None, inherits from parent.",
        examples=["http://localhost:9001/chat"],
    )
    provider: Optional[str] = Field(
        default=None,
        description="LLM provider this version uses (openai, gemini, anthropic, etc.)",
        examples=["openai"],
    )
    model: Optional[str] = Field(
        default=None,
        description="Exact model name for this version",
        examples=["gpt-4o-mini"],
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt for this version (enables instruction_following metric)",
        examples=["You are a helpful and concise customer support agent."],
    )
    notes: Optional[str] = Field(
        default=None,
        description="Human-readable description of what changed in this version",
        examples=["Switched to gpt-4o-mini to reduce cost by ~80%"],
    )
    fork_from_version_id: Optional[int] = Field(
        default=None,
        description=(
            "Copy config from this existing version as starting point. "
            "If None, copies from the parent agent's config."
        ),
        examples=[1],
    )


class AgentVersionUpdate(BaseModel):
    """Request body for PATCH /agents/{id}/versions/{vid}. All fields optional."""
    endpoint: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class AgentVersionCompareRequest(BaseModel):
    """
    Request body for POST /agents/{id}/compare.

    Shortcut to run a full experiment between two versions of the same agent.
    Equivalent to running two eval runs then POST /experiments — but in one call.
    """
    baseline_version_id: int = Field(
        description="ID of the baseline version (V1)",
        examples=[1],
    )
    candidate_version_id: int = Field(
        description="ID of the candidate version (V2)",
        examples=[2],
    )
    suite_id: int = Field(
        description="Test suite to run both versions against",
        examples=[1],
    )
    name: Optional[str] = Field(
        default=None,
        description="Name for this experiment",
        examples=["Support Bot v1 vs v2"],
    )
    thresholds: Optional[Dict[str, float]] = Field(
        default=None,
        description="Custom per-metric pass thresholds",
        examples=[{"correctness": 0.80}],
    )
    judge_provider: Optional[str] = Field(
        default="gemini",
        description="Which LLM judge to use",
    )


# ── Response Schemas ──────────────────────────────────────────────────────────

class AgentVersionResponse(BaseModel):
    """Full detail for one agent version."""
    id: int
    agent_id: int
    agent_name: str
    version: str
    endpoint: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    # Latest eval stats (from most recent completed run for this version)
    latest_eval_score: Optional[float] = Field(
        default=None,
        description="Average score from the most recent completed evaluation run",
    )
    total_eval_runs: int = Field(
        default=0,
        description="Total number of evaluation runs using this version",
    )
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class AgentVersionListItem(BaseModel):
    """Lightweight version item for list view."""
    id: int
    agent_id: int
    version: str
    model: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    latest_eval_score: Optional[float] = None
    total_eval_runs: int = 0
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class AgentHistoryItem(BaseModel):
    """One eval run in the agent version history timeline."""
    run_id: int
    version_id: Optional[int] = None
    version_label: Optional[str] = None
    suite_id: int
    suite_name: Optional[str] = None
    status: str
    avg_score: Optional[float] = None
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    judge_provider: Optional[str] = None
    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
