"""
Agent ORM Model — Represents a user's AI agent registered on the platform.

Each row = one agent profile. Stores connection details, declared components,
framework info, and response format mapping for flexible integration.

Design decisions:
  - components stored as JSON string in Text column (not ARRAY) for SQLite compatibility
  - response_mapping stored as JSON string for flexible field detection
  - soft-delete via is_active flag (preserves historical data integrity)
  - auto_discover flag tells the evaluation engine to inspect execution traces
"""

import datetime
import json
from typing import Optional, List, Dict

from sqlalchemy import String, Text, Integer, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Agent(Base):
    """
    Represents one registered AI agent / workflow on the platform.

    Table name: agents
    """

    __tablename__ = "agents"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Connection ────────────────────────────────────────────────────────────
    # connection_type: "rest_api" or "python_sdk"
    connection_type: Mapped[str] = mapped_column(String(20), nullable=False, default="rest_api")
    # endpoint: the REST API URL where we send test queries (null for SDK agents)
    endpoint: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── Agent Configuration ───────────────────────────────────────────────────
    version: Mapped[str] = mapped_column(String(50), default="v1")
    # provider: what LLM the user's agent uses internally ("openai", "gemini", etc.)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # model: specific model the agent uses ("gpt-4o", "gemini-1.5-flash")
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # framework: what framework the agent is built with ("langgraph", "crewai", "custom")
    framework: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # ── Components (JSON-encoded list) ────────────────────────────────────────
    # Stores declared capabilities like: ["llm_call", "retrieval", "tools", "multi_step", "system_prompt"]
    # Why Text and not ARRAY: SQLite (our fallback DB) doesn't support ARRAY columns.
    # We serialize/deserialize via json.loads()/json.dumps() in the Pydantic schema layer.
    components: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Response Format Mapping (JSON-encoded dict) ───────────────────────────
    # Lets user tell us where to find fields in their agent's response.
    # Example: {"answer_field": "result.text", "tools_field": "result.actions"}
    # If null, we auto-detect by looking for common key names.
    response_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Auto-Discovery Flag ───────────────────────────────────────────────────
    # If True, our evaluation engine will inspect execution traces to auto-detect
    # components the user didn't manually declare.
    auto_discover: Mapped[bool] = mapped_column(Boolean, default=True)

    # ── Connection Health ─────────────────────────────────────────────────────
    # Tracks the last time we successfully pinged the agent's endpoint.
    connection_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="untested")
    last_tested_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Soft Delete ───────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # ── Helper Methods ────────────────────────────────────────────────────────
    def get_components_list(self) -> List[str]:
        """Deserialize JSON string → Python list."""
        if not self.components:
            return []
        try:
            return json.loads(self.components)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_components_list(self, components: List[str]):
        """Serialize Python list → JSON string for storage."""
        self.components = json.dumps(components) if components else None

    def get_response_mapping(self) -> Dict[str, str]:
        """Deserialize response_mapping JSON string → Python dict."""
        if not self.response_mapping:
            return {}
        try:
            return json.loads(self.response_mapping)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_response_mapping(self, mapping: Dict[str, str]):
        """Serialize response_mapping dict → JSON string for storage."""
        self.response_mapping = json.dumps(mapping) if mapping else None
