"""
Agent Versions Router — Day 7.

Endpoints:
  POST  /agents/{id}/versions           Create a new version
  GET   /agents/{id}/versions           List all versions with latest scores
  GET   /agents/{id}/versions/{vid}     Get one version detail
  PATCH /agents/{id}/versions/{vid}     Update version notes/endpoint
  POST  /agents/{id}/compare            Quick experiment: compare two versions
  GET   /agents/{id}/history            All eval runs across all versions (timeline)
"""

import structlog
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.eval_run import EvalRun
from app.models.test_suite import TestSuite
from app.schemas.agent_version import (
    AgentVersionCreate,
    AgentVersionUpdate,
    AgentVersionResponse,
    AgentVersionListItem,
    AgentVersionCompareRequest,
    AgentHistoryItem,
)

log = structlog.get_logger()
router = APIRouter()


# ── Helper: resolve parent agent ──────────────────────────────────────────────

def _get_agent_or_404(agent_id: int, db: Session) -> Agent:
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent id={agent_id} not found")
    return agent


def _get_version_or_404(version_id: int, agent_id: int, db: Session) -> AgentVersion:
    v = db.query(AgentVersion).filter(
        AgentVersion.id == version_id,
        AgentVersion.agent_id == agent_id,
    ).first()
    if not v:
        raise HTTPException(
            status_code=404,
            detail=f"Version id={version_id} not found for agent id={agent_id}",
        )
    return v


def _enrich_version(v: AgentVersion, agent: Agent, db: Session) -> dict:
    """Add latest_eval_score and total_eval_runs to a version response."""
    runs = (
        db.query(EvalRun)
        .filter(EvalRun.version_id == v.id)
        .order_by(EvalRun.created_at.desc())
        .all()
    )
    latest_score = None
    for r in runs:
        if r.status == "completed" and r.avg_score is not None:
            latest_score = r.avg_score
            break

    return {
        "id": v.id,
        "agent_id": v.agent_id,
        "agent_name": agent.name,
        "version": v.version,
        "endpoint": v.endpoint or agent.endpoint,
        "provider": v.provider or agent.provider,
        "model": v.model or agent.model,
        "system_prompt": v.system_prompt,
        "notes": v.notes,
        "is_active": v.is_active,
        "latest_eval_score": latest_score,
        "total_eval_runs": len(runs),
        "created_at": v.created_at,
    }


# ── POST /agents/{id}/versions — Create Version ───────────────────────────────

@router.post("/{agent_id}/versions", response_model=AgentVersionResponse, status_code=201)
def create_agent_version(
    agent_id: int,
    request: AgentVersionCreate,
    db: Session = Depends(get_db),
):
    """
    Create a new version of an agent.

    All config fields are optional — anything not provided is inherited
    from the parent agent or the version being forked from.

    Example — test a cheaper model:
      POST /agents/1/versions
      { "version": "v2", "model": "gpt-4o-mini", "notes": "Cost reduction test" }

    Example — fork from an existing version:
      POST /agents/1/versions
      { "version": "v3", "fork_from_version_id": 2, "system_prompt": "Be concise." }
    """
    agent = _get_agent_or_404(agent_id, db)

    # Check version label is unique for this agent
    existing = db.query(AgentVersion).filter(
        AgentVersion.agent_id == agent_id,
        AgentVersion.version == request.version,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Version '{request.version}' already exists for agent id={agent_id}",
        )

    # Resolve config to fork from
    fork_endpoint = agent.endpoint
    fork_provider = agent.provider
    fork_model = agent.model
    fork_prompt = None

    if request.fork_from_version_id:
        source = db.query(AgentVersion).filter(
            AgentVersion.id == request.fork_from_version_id,
            AgentVersion.agent_id == agent_id,
        ).first()
        if not source:
            raise HTTPException(
                status_code=404,
                detail=f"fork_from_version_id={request.fork_from_version_id} not found for agent {agent_id}",
            )
        fork_endpoint = source.endpoint or fork_endpoint
        fork_provider = source.provider or fork_provider
        fork_model = source.model or fork_model
        fork_prompt = source.system_prompt

    # Apply overrides from the request (None = keep forked value)
    new_version = AgentVersion(
        agent_id=agent_id,
        version=request.version,
        endpoint=request.endpoint if request.endpoint is not None else (None if not request.fork_from_version_id else fork_endpoint),
        provider=request.provider or fork_provider,
        model=request.model or fork_model,
        system_prompt=request.system_prompt if request.system_prompt is not None else fork_prompt,
        notes=request.notes,
    )

    db.add(new_version)
    db.commit()
    db.refresh(new_version)

    log.info(
        "agent version created",
        agent_id=agent_id,
        version_id=new_version.id,
        version=new_version.version,
        model=new_version.model,
    )

    return AgentVersionResponse(**_enrich_version(new_version, agent, db))


# ── GET /agents/{id}/versions — List Versions ─────────────────────────────────

@router.get("/{agent_id}/versions", response_model=List[AgentVersionListItem])
def list_agent_versions(
    agent_id: int,
    include_inactive: bool = Query(default=False, description="Include deactivated versions"),
    db: Session = Depends(get_db),
):
    """
    List all versions of an agent with their latest evaluation scores.

    Returns versions in creation order (oldest first = V1 first).
    Use this to see the full version history and pick which versions to compare.
    """
    agent = _get_agent_or_404(agent_id, db)

    query = db.query(AgentVersion).filter(AgentVersion.agent_id == agent_id)
    if not include_inactive:
        query = query.filter(AgentVersion.is_active == True)
    versions = query.order_by(AgentVersion.created_at.asc()).all()

    return [
        AgentVersionListItem(**_enrich_version(v, agent, db))
        for v in versions
    ]


# ── GET /agents/{id}/versions/{vid} — Get One Version ────────────────────────

@router.get("/{agent_id}/versions/{version_id}", response_model=AgentVersionResponse)
def get_agent_version(
    agent_id: int,
    version_id: int,
    db: Session = Depends(get_db),
):
    """Get full detail for one agent version including latest eval score."""
    agent = _get_agent_or_404(agent_id, db)
    v = _get_version_or_404(version_id, agent_id, db)
    return AgentVersionResponse(**_enrich_version(v, agent, db))


# ── PATCH /agents/{id}/versions/{vid} — Update Version ───────────────────────

@router.patch("/{agent_id}/versions/{version_id}", response_model=AgentVersionResponse)
def update_agent_version(
    agent_id: int,
    version_id: int,
    request: AgentVersionUpdate,
    db: Session = Depends(get_db),
):
    """
    Partially update a version's config or notes.
    Only fields provided in the request body are changed.
    """
    agent = _get_agent_or_404(agent_id, db)
    v = _get_version_or_404(version_id, agent_id, db)

    if request.endpoint is not None:
        v.endpoint = request.endpoint
    if request.model is not None:
        v.model = request.model
    if request.system_prompt is not None:
        v.system_prompt = request.system_prompt
    if request.notes is not None:
        v.notes = request.notes
    if request.is_active is not None:
        v.is_active = request.is_active

    db.commit()
    db.refresh(v)

    return AgentVersionResponse(**_enrich_version(v, agent, db))


# ── POST /agents/{id}/compare — Quick Version Comparison ─────────────────────

@router.post("/{agent_id}/compare")
def compare_agent_versions(
    agent_id: int,
    request: AgentVersionCompareRequest,
    db: Session = Depends(get_db),
):
    """
    Shortcut: Run a full experiment comparing two versions of this agent.

    Equivalent to:
      1. POST /evaluations/run  { agent_id, suite_id, version_id: baseline_version_id }
      2. POST /evaluations/run  { agent_id, suite_id, version_id: candidate_version_id }
      3. POST /experiments      { baseline_run_id, candidate_run_id, ... }

    But in one call. Returns the full ExperimentResult with PASS/REVIEW/FAIL verdict.
    """
    from app.evaluation import orchestrator, experimenter
    from app.providers import ModelGateway
    from app.schemas.experiment import ExperimentResult, MetricDiff, RunSummary

    agent = _get_agent_or_404(agent_id, db)

    # Validate both versions belong to this agent
    baseline_v = _get_version_or_404(request.baseline_version_id, agent_id, db)
    candidate_v = _get_version_or_404(request.candidate_version_id, agent_id, db)

    # Validate suite
    suite = db.query(TestSuite).filter(TestSuite.id == request.suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite id={request.suite_id} not found")

    # Resolve endpoints
    baseline_endpoint = baseline_v.endpoint or agent.endpoint
    candidate_endpoint = candidate_v.endpoint or agent.endpoint

    if not baseline_endpoint:
        raise HTTPException(status_code=400, detail=f"Baseline version has no endpoint configured")
    if not candidate_endpoint:
        raise HTTPException(status_code=400, detail=f"Candidate version has no endpoint configured")

    gateway = ModelGateway()

    log.info(
        "running version comparison",
        agent_id=agent_id,
        baseline_version=baseline_v.version,
        candidate_version=candidate_v.version,
        suite_id=request.suite_id,
    )

    # Run baseline evaluation
    try:
        baseline_run = orchestrator.run_evaluation(
            agent_id=agent_id,
            suite_id=request.suite_id,
            db=db,
            gateway=gateway,
            judge_provider=request.judge_provider or "gemini",
            version_id=baseline_v.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Baseline evaluation failed: {e}")

    # Run candidate evaluation
    try:
        candidate_run = orchestrator.run_evaluation(
            agent_id=agent_id,
            suite_id=request.suite_id,
            db=db,
            gateway=gateway,
            judge_provider=request.judge_provider or "gemini",
            version_id=candidate_v.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Candidate evaluation failed: {e}")

    # Compare
    try:
        result_data = experimenter.compare_runs(
            baseline_run_id=baseline_run.id,
            candidate_run_id=candidate_run.id,
            db=db,
            thresholds=request.thresholds,
            name=request.name or f"{agent.name}: {baseline_v.version} vs {candidate_v.version}",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Persist experiment
    from app.models.experiment import Experiment
    exp = Experiment(
        name=result_data.get("name"),
        baseline_run_id=baseline_run.id,
        candidate_run_id=candidate_run.id,
        verdict=result_data["verdict"],
    )
    exp.set_result(result_data)
    exp.set_config({"agent_id": agent_id, "thresholds": request.thresholds or {}})
    db.add(exp)
    db.commit()

    return {
        "experiment_id": exp.id,
        "agent_id": agent_id,
        "agent_name": agent.name,
        "baseline_version": baseline_v.version,
        "candidate_version": candidate_v.version,
        "baseline_run_id": baseline_run.id,
        "candidate_run_id": candidate_run.id,
        "verdict": result_data["verdict"],
        "verdict_emoji": result_data["verdict_emoji"],
        "metric_diffs": result_data["metric_diffs"],
        "improvements": result_data["improvements"],
        "regressions": result_data["regressions"],
        "fail_reasons": result_data["fail_reasons"],
        "review_reasons": result_data["review_reasons"],
        "suggestions": result_data["suggestions"],
    }


# ── GET /agents/{id}/history — Full Eval Timeline ────────────────────────────

@router.get("/{agent_id}/history", response_model=List[AgentHistoryItem])
def get_agent_history(
    agent_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Get full evaluation history for an agent across ALL versions.

    Returns runs in reverse-chronological order (newest first).
    Each item shows which version was used and what score was achieved.
    Useful for plotting score trends over time as you iterate versions.
    """
    agent = _get_agent_or_404(agent_id, db)

    runs = (
        db.query(EvalRun)
        .filter(EvalRun.agent_id == agent_id)
        .order_by(EvalRun.created_at.desc())
        .limit(limit)
        .all()
    )

    # Build version label map
    versions = db.query(AgentVersion).filter(AgentVersion.agent_id == agent_id).all()
    version_map = {v.id: v.version for v in versions}

    # Build suite name map
    suite_ids = list({r.suite_id for r in runs})
    suites = db.query(TestSuite).filter(TestSuite.id.in_(suite_ids)).all()
    suite_map = {s.id: s.name for s in suites}

    return [
        AgentHistoryItem(
            run_id=r.id,
            version_id=getattr(r, "version_id", None),
            version_label=version_map.get(getattr(r, "version_id", None)) if getattr(r, "version_id", None) else None,
            suite_id=r.suite_id,
            suite_name=suite_map.get(r.suite_id),
            status=r.status,
            avg_score=r.avg_score,
            total_cases=r.total_cases,
            passed_cases=r.passed_cases,
            failed_cases=r.failed_cases,
            judge_provider=r.judge_provider,
            started_at=r.started_at,
            completed_at=r.completed_at,
        )
        for r in runs
    ]
