"""
Agent Pydantic Schemas — request/response validation shapes for /agents endpoints.

Schema Separation Pattern:
  - AgentCreate:   Used for POST /agents — only contains fields the USER should provide.
                   Does NOT include id, created_at, is_active (server-generated).
  - AgentUpdate:   Used for PATCH /agents/{id} — ALL fields optional (partial update).
  - AgentResponse: Used for GET responses — includes ALL fields including server-generated ones.

Why separate schemas?
  Without separation, a malicious user could POST {"id": 999, "is_active": false} and
  overwrite server-controlled fields. Create schemas act as a whitelist of allowed input fields.
"""

import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from enum import Enum


class ConnectionType(str, Enum):
    """Allowed connection methods for agent integration."""
    rest_api = "rest_api"
    python_sdk = "python_sdk"


class AgentCreate(BaseModel):
    """
    Request body for POST /agents — register a new agent.
    Only fields the user should provide. Server fills in the rest.
    """
    name: str = Field(
        ...,  # ... means required
        min_length=1,
        max_length=200,
        description="Human-readable name for the agent",
        examples=["Customer Support Bot v1"],
    )
    description: Optional[str] = Field(
        default=None,
        description="What this agent does",
        examples=["Handles customer queries about orders, returns, and shipping"],
    )
    connection_type: ConnectionType = Field(
        default=ConnectionType.rest_api,
        description="How we connect to this agent: rest_api or python_sdk",
    )
    endpoint: Optional[str] = Field(
        default=None,
        description="REST API URL where we send test queries (required for rest_api type)",
        examples=["https://mycompany.com/agent"],
    )
    version: str = Field(
        default="v1",
        max_length=50,
        description="Version tag for this agent",
    )
    provider: Optional[str] = Field(
        default=None,
        description="LLM provider the agent uses internally (e.g. openai, gemini, anthropic)",
        examples=["openai"],
    )
    model: Optional[str] = Field(
        default=None,
        description="Specific model the agent uses (e.g. gpt-4o, gemini-1.5-flash)",
        examples=["gpt-4o"],
    )
    framework: Optional[str] = Field(
        default=None,
        description="Framework the agent is built with (e.g. langgraph, crewai, custom)",
        examples=["langgraph"],
    )
    components: Optional[List[str]] = Field(
        default=None,
        description="Declared capabilities: llm_call, retrieval, tools, multi_step, system_prompt",
        examples=[["llm_call", "retrieval", "tools"]],
    )
    response_mapping: Optional[Dict[str, str]] = Field(
        default=None,
        description="Custom field mapping for non-standard response formats",
        examples=[{"answer_field": "result.text", "tools_field": "result.actions"}],
    )
    auto_discover: bool = Field(
        default=True,
        description="If True, platform auto-detects components from execution traces",
    )


class AgentUpdate(BaseModel):
    """
    Request body for PATCH /agents/{id} — partial update.
    ALL fields optional — only provided fields are updated.
    """
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    connection_type: Optional[ConnectionType] = None
    endpoint: Optional[str] = None
    version: Optional[str] = Field(default=None, max_length=50)
    provider: Optional[str] = None
    model: Optional[str] = None
    framework: Optional[str] = None
    components: Optional[List[str]] = None
    response_mapping: Optional[Dict[str, str]] = None
    auto_discover: Optional[bool] = None


class ConnectionTestResult(BaseModel):
    """Result of the connection handshake test when registering a REST API agent."""
    status: str = Field(description="connected, failed, or skipped")
    latency_ms: Optional[float] = Field(default=None, description="Response time in ms")
    status_code: Optional[int] = Field(default=None, description="HTTP status code returned")
    detected_fields: Optional[Dict[str, str]] = Field(
        default=None,
        description="Auto-detected response field mapping",
    )
    available_metrics: Optional[List[str]] = Field(
        default=None,
        description="Evaluation metrics available based on detected response format",
    )
    unavailable_metrics: Optional[List[str]] = Field(
        default=None,
        description="Metrics NOT available (agent doesn't return required data)",
    )
    error: Optional[str] = Field(default=None, description="Error message if connection failed")


class AgentResponse(BaseModel):
    """
    Response shape for GET /agents — includes all fields + server-generated ones.
    """
    id: int
    name: str
    description: Optional[str] = None
    connection_type: str
    endpoint: Optional[str] = None
    version: str
    provider: Optional[str] = None
    model: Optional[str] = None
    framework: Optional[str] = None
    components: Optional[List[str]] = None  # deserialized from JSON string
    response_mapping: Optional[Dict[str, str]] = None
    auto_discover: bool = True
    connection_status: Optional[str] = None
    last_tested_at: Optional[datetime.datetime] = None
    is_active: bool = True
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class AgentCreateResponse(BaseModel):
    """Response for POST /agents — agent profile + connection test result."""
    agent: AgentResponse
    connection_test: Optional[ConnectionTestResult] = None
