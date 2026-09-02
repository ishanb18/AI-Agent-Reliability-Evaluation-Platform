"""
Agents Router — CRUD endpoints for managing AI agent registrations.

Endpoints:
  POST   /agents              Register a new agent + run connection handshake
  GET    /agents              List all agents (filter by is_active)
  GET    /agents/{agent_id}   Get agent profile by ID
  PATCH  /agents/{agent_id}   Partial update of agent profile
  DELETE /agents/{agent_id}   Soft-delete (sets is_active=False)
  POST   /agents/{agent_id}/test-connection   Re-test agent connection

The connection handshake feature:
  When a REST API agent is registered, we immediately send a test ping to their
  endpoint. This gives the user instant feedback: is the connection working? what
  response format does the agent use? which evaluation metrics are available?
"""

import json
import time
import datetime
from typing import Optional, List, Dict

import structlog
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.agent import Agent
from app.schemas.agent import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentCreateResponse,
    ConnectionTestResult,
)

log = structlog.get_logger()
router = APIRouter()


# ── Helper: Connection Handshake ──────────────────────────────────────────────

def _test_agent_connection(endpoint: str) -> ConnectionTestResult:
    """
    Sends a test ping to the agent's REST endpoint and analyzes the response.

    This is the "handshake" — we discover:
    1. Can we reach the agent at all?
    2. What response format does it use?
    3. Based on the format, which evaluation metrics are available?
    """
    test_payload = {"input": "Hello, are you there? This is a connection test."}

    try:
        start = time.time()
        response = httpx.post(endpoint, json=test_payload, timeout=10.0)
        latency_ms = round((time.time() - start) * 1000, 2)

        if response.status_code >= 400:
            return ConnectionTestResult(
                status="failed",
                latency_ms=latency_ms,
                status_code=response.status_code,
                error=f"Agent returned HTTP {response.status_code}",
            )

        # ── Auto-detect response fields ───────────────────────────────────
        detected_fields: Dict[str, str] = {}
        available_metrics: List[str] = ["latency"]  # always available (we measure it)
        unavailable_metrics: List[str] = []

        try:
            data = response.json()
        except Exception:
            # Agent returned non-JSON (plain text)
            return ConnectionTestResult(
                status="connected",
                latency_ms=latency_ms,
                status_code=response.status_code,
                detected_fields={"answer": "plain_text_body"},
                available_metrics=["correctness", "relevance", "safety", "latency"],
                unavailable_metrics=["tool_accuracy", "trajectory", "rag_faithfulness"],
            )

        # Look for answer field — try common key names
        answer_keys = ["answer", "response", "output", "text", "result", "message", "content"]
        for key in answer_keys:
            if key in data:
                val = data[key]
                # Handle nested: {"result": {"text": "..."}}
                if isinstance(val, dict):
                    for sub_key in ["text", "content", "message", "output"]:
                        if sub_key in val:
                            detected_fields["answer"] = f"{key}.{sub_key}"
                            break
                    if "answer" not in detected_fields:
                        detected_fields["answer"] = key
                else:
                    detected_fields["answer"] = key
                break

        if "answer" in detected_fields:
            available_metrics.extend(["correctness", "relevance", "safety"])
        else:
            unavailable_metrics.extend(["correctness", "relevance"])

        # Look for tool_calls field
        tool_keys = ["tool_calls", "tools", "actions", "function_calls", "tool_trace"]
        for key in tool_keys:
            if key in data:
                detected_fields["tools"] = key
                available_metrics.extend(["tool_accuracy", "trajectory"])
                break
        if "tools" not in detected_fields:
            unavailable_metrics.extend(["tool_accuracy", "trajectory"])

        # Look for context/retrieval field (RAG)
        context_keys = ["context", "retrieved_chunks", "documents", "sources", "references"]
        for key in context_keys:
            if key in data:
                detected_fields["context"] = key
                available_metrics.extend(["rag_faithfulness", "context_precision"])
                break
        if "context" not in detected_fields:
            unavailable_metrics.extend(["rag_faithfulness", "context_precision"])

        # Look for metadata
        meta_keys = ["metadata", "meta", "stats", "info"]
        for key in meta_keys:
            if key in data:
                detected_fields["metadata"] = key
                break

        return ConnectionTestResult(
            status="connected",
            latency_ms=latency_ms,
            status_code=response.status_code,
            detected_fields=detected_fields,
            available_metrics=list(set(available_metrics)),
            unavailable_metrics=list(set(unavailable_metrics)),
        )

    except httpx.TimeoutException:
        return ConnectionTestResult(
            status="failed",
            error="Connection timed out after 10 seconds. Is the agent running?",
        )
    except httpx.ConnectError:
        return ConnectionTestResult(
            status="failed",
            error=f"Could not connect to {endpoint}. Check the URL and ensure the agent is running.",
        )
    except Exception as e:
        return ConnectionTestResult(
            status="failed",
            error=f"Connection error: {str(e)}",
        )


# ── Helper: ORM → Response conversion ────────────────────────────────────────

def _agent_to_response(agent: Agent) -> AgentResponse:
    """Convert SQLAlchemy Agent model to Pydantic AgentResponse, deserializing JSON fields."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        connection_type=agent.connection_type,
        endpoint=agent.endpoint,
        version=agent.version,
        provider=agent.provider,
        model=agent.model,
        framework=agent.framework,
        components=agent.get_components_list(),
        response_mapping=agent.get_response_mapping() or None,
        auto_discover=agent.auto_discover,
        connection_status=agent.connection_status,
        last_tested_at=agent.last_tested_at,
        is_active=agent.is_active,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=AgentCreateResponse, status_code=201)
def create_agent(agent_data: AgentCreate, db: Session = Depends(get_db)):
    """
    Register a new AI agent on the platform.

    For REST API agents with an endpoint, automatically runs a connection
    handshake test to verify connectivity and detect response format.
    """
    log.info("creating agent", name=agent_data.name, connection_type=agent_data.connection_type)

    # ── Create ORM object ─────────────────────────────────────────────────
    agent = Agent(
        name=agent_data.name,
        description=agent_data.description,
        connection_type=agent_data.connection_type.value,
        endpoint=agent_data.endpoint,
        version=agent_data.version,
        provider=agent_data.provider,
        model=agent_data.model,
        framework=agent_data.framework,
        auto_discover=agent_data.auto_discover,
    )

    # Serialize list fields to JSON strings
    if agent_data.components:
        agent.set_components_list(agent_data.components)
    if agent_data.response_mapping:
        agent.set_response_mapping(agent_data.response_mapping)

    db.add(agent)
    db.commit()
    db.refresh(agent)

    # ── Run connection handshake for REST API agents ──────────────────────
    connection_test = None
    if agent.connection_type == "rest_api" and agent.endpoint:
        log.info("running connection handshake", endpoint=agent.endpoint)
        connection_test = _test_agent_connection(agent.endpoint)

        # Update agent's connection status in DB
        agent.connection_status = connection_test.status
        agent.last_tested_at = datetime.datetime.now(datetime.timezone.utc)

        # If handshake detected a response mapping and user didn't provide one, save it
        if connection_test.detected_fields and not agent_data.response_mapping:
            agent.set_response_mapping(connection_test.detected_fields)

        db.commit()
        db.refresh(agent)

        log.info(
            "connection handshake complete",
            agent_id=agent.id,
            status=connection_test.status,
            detected_fields=connection_test.detected_fields,
        )
    else:
        # SDK agents or agents without endpoints skip the handshake
        connection_test = ConnectionTestResult(status="skipped")

    return AgentCreateResponse(
        agent=_agent_to_response(agent),
        connection_test=connection_test,
    )


@router.get("", response_model=List[AgentResponse])
def list_agents(
    is_active: Optional[bool] = Query(default=True, description="Filter by active status"),
    db: Session = Depends(get_db),
):
    """List all registered agents. Defaults to showing only active agents."""
    query = db.query(Agent)
    if is_active is not None:
        query = query.filter(Agent.is_active == is_active)
    agents = query.order_by(Agent.created_at.desc()).all()
    return [_agent_to_response(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """Get a single agent profile by ID."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with id={agent_id} not found")
    return _agent_to_response(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
def update_agent(agent_id: int, updates: AgentUpdate, db: Session = Depends(get_db)):
    """
    Partial update of an agent profile.
    Only provided fields are updated — omitted fields remain unchanged.
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with id={agent_id} not found")

    # Only update fields that were explicitly provided (not None)
    update_data = updates.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field == "components":
            agent.set_components_list(value)
        elif field == "response_mapping":
            agent.set_response_mapping(value)
        elif field == "connection_type":
            agent.connection_type = value.value if hasattr(value, "value") else value
        else:
            setattr(agent, field, value)

    db.commit()
    db.refresh(agent)
    log.info("agent updated", agent_id=agent_id, fields=list(update_data.keys()))

    return _agent_to_response(agent)


@router.delete("/{agent_id}", status_code=200)
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    """
    Soft-delete an agent — sets is_active=False.
    The agent record is preserved for historical data integrity
    (test runs that reference this agent won't break).
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with id={agent_id} not found")

    agent.is_active = False
    db.commit()
    log.info("agent soft-deleted", agent_id=agent_id)

    return {"detail": f"Agent '{agent.name}' (id={agent_id}) deactivated", "is_active": False}


@router.post("/{agent_id}/test-connection", response_model=ConnectionTestResult)
def test_connection(agent_id: int, db: Session = Depends(get_db)):
    """
    Re-test connection to an agent's endpoint.
    Useful after the user fixes their agent server and wants to verify it works.
    """
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with id={agent_id} not found")

    if agent.connection_type != "rest_api" or not agent.endpoint:
        raise HTTPException(
            status_code=400,
            detail="Connection test only available for REST API agents with an endpoint",
        )

    result = _test_agent_connection(agent.endpoint)

    # Update connection status
    agent.connection_status = result.status
    agent.last_tested_at = datetime.datetime.now(datetime.timezone.utc)
    if result.detected_fields:
        agent.set_response_mapping(result.detected_fields)
    db.commit()

    log.info("connection re-tested", agent_id=agent_id, status=result.status)
    return result
