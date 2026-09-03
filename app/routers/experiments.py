"""
Experiments Router — Day 6.

Endpoints:
  POST  /experiments                Create a V1 vs V2 comparison experiment
  GET   /experiments                List all experiments
  GET   /experiments/{id}           Get full experiment result with verdict
"""

import datetime
import structlog
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.experiment import Experiment
from app.models.eval_run import EvalRun
from app.models.agent import Agent
from app.models.test_suite import TestSuite
from app.schemas.experiment import (
    ExperimentCreate,
    ExperimentResult,
    ExperimentListItem,
    MetricDiff,
    RunSummary,
)
from app.evaluation import experimenter, orchestrator
from app.providers import ModelGateway

log = structlog.get_logger()
router = APIRouter()

# Shared gateway singleton
_gateway: Optional[ModelGateway] = None


def _get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway


# ── Helper: Build ExperimentResult from DB Experiment ────────────────────────

def _build_result_response(exp: Experiment, result_data: dict) -> ExperimentResult:
    """Build a full ExperimentResult from an Experiment ORM + result dict."""

    # Build MetricDiff list
    raw_diffs = result_data.get("metric_diffs", [])
    metric_diffs = [
        MetricDiff(
            metric=d["metric"],
            baseline=d.get("baseline"),
            candidate=d.get("candidate"),
            delta=d.get("delta"),
            status=d.get("status", "unknown"),
            threshold=d.get("threshold"),
            meets_threshold=d.get("meets_threshold"),
        )
        for d in raw_diffs
    ]

    # Build RunSummary objects
    bl_raw = result_data.get("baseline_summary", {})
    ca_raw = result_data.get("candidate_summary", {})

    baseline_summary = RunSummary(**bl_raw) if bl_raw else None
    candidate_summary = RunSummary(**ca_raw) if ca_raw else None

    return ExperimentResult(
        id=exp.id,
        name=exp.name or result_data.get("name"),
        baseline_run_id=exp.baseline_run_id,
        candidate_run_id=exp.candidate_run_id,
        baseline_agent_id=result_data.get("baseline_agent_id"),
        candidate_agent_id=result_data.get("candidate_agent_id"),
        suite_id=result_data.get("suite_id"),
        verdict=exp.verdict or result_data.get("verdict", "unknown"),
        verdict_emoji=result_data.get("verdict_emoji", "❓ UNKNOWN"),
        metric_diffs=metric_diffs,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        thresholds_used=result_data.get("thresholds_used", {}),
        improvements=result_data.get("improvements", []),
        regressions=result_data.get("regressions", []),
        fail_reasons=result_data.get("fail_reasons", []),
        review_reasons=result_data.get("review_reasons", []),
        suggestions=result_data.get("suggestions", []),
        created_at=exp.created_at,
    )


# ── POST /experiments — Create Experiment ────────────────────────────────────

@router.post("", response_model=ExperimentResult, status_code=201)
def create_experiment(
    request: ExperimentCreate,
    db: Session = Depends(get_db),
):
    """
    Create a new V1 vs V2 experiment comparison.

    TWO MODES:

    Mode A — Compare existing evaluation runs (fast):
      {
        "baseline_run_id": 1,
        "candidate_run_id": 2,
        "name": "Support Bot V1 vs V2"
      }
      Both runs must already be completed.

    Mode B — Run fresh comparison (slower, more convenient):
      {
        "baseline_agent_id": 1,
        "candidate_agent_id": 2,
        "suite_id": 1,
        "name": "Support Bot V1 vs V2"
      }
      Platform runs both agents against the suite, then compares.

    Returns:
      Full ExperimentResult with:
        - metric_diffs table (baseline vs candidate per metric)
        - verdict: PASS / REVIEW / FAIL
        - improvements, regressions, suggestions
    """
    baseline_run_id = request.baseline_run_id
    candidate_run_id = request.candidate_run_id

    # ── Mode B: Run fresh evaluations first ──────────────────────────────────
    if baseline_run_id is None or candidate_run_id is None:
        if not all([request.baseline_agent_id, request.candidate_agent_id, request.suite_id]):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provide either (baseline_run_id + candidate_run_id) for Mode A, "
                    "or (baseline_agent_id + candidate_agent_id + suite_id) for Mode B."
                ),
            )

        gateway = _get_gateway()

        # Validate agents exist
        for agent_id, label in [
            (request.baseline_agent_id, "baseline"),
            (request.candidate_agent_id, "candidate"),
        ]:
            agent = db.query(Agent).filter(Agent.id == agent_id).first()
            if not agent:
                raise HTTPException(status_code=404, detail=f"{label} agent id={agent_id} not found")
            if agent.connection_type != "rest_api" or not agent.endpoint:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} agent '{agent.name}' must be a REST API agent with an endpoint",
                )

        # Validate suite
        suite = db.query(TestSuite).filter(TestSuite.id == request.suite_id).first()
        if not suite:
            raise HTTPException(status_code=404, detail=f"Test suite id={request.suite_id} not found")

        log.info(
            "running fresh evaluation for experiment",
            baseline_agent=request.baseline_agent_id,
            candidate_agent=request.candidate_agent_id,
            suite=request.suite_id,
        )

        # Run baseline evaluation
        try:
            baseline_run = orchestrator.run_evaluation(
                agent_id=request.baseline_agent_id,
                suite_id=request.suite_id,
                db=db,
                gateway=gateway,
                judge_provider=request.judge_provider or "gemini",
            )
            baseline_run_id = baseline_run.id
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Baseline evaluation failed: {str(e)}")

        # Run candidate evaluation
        try:
            candidate_run = orchestrator.run_evaluation(
                agent_id=request.candidate_agent_id,
                suite_id=request.suite_id,
                db=db,
                gateway=gateway,
                judge_provider=request.judge_provider or "gemini",
            )
            candidate_run_id = candidate_run.id
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Candidate evaluation failed: {str(e)}")

    # ── Mode A: Validate existing runs ───────────────────────────────────────
    else:
        baseline_run = db.query(EvalRun).filter(EvalRun.id == baseline_run_id).first()
        if not baseline_run:
            raise HTTPException(status_code=404, detail=f"Baseline run id={baseline_run_id} not found")

        candidate_run = db.query(EvalRun).filter(EvalRun.id == candidate_run_id).first()
        if not candidate_run:
            raise HTTPException(status_code=404, detail=f"Candidate run id={candidate_run_id} not found")

    # ── Compare the runs ──────────────────────────────────────────────────────
    try:
        result_data = experimenter.compare_runs(
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            db=db,
            thresholds=request.thresholds,
            name=request.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── Persist the experiment ────────────────────────────────────────────────
    exp = Experiment(
        name=request.name or result_data.get("name"),
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        verdict=result_data["verdict"],
    )
    exp.set_result(result_data)
    exp.set_config({"thresholds": request.thresholds or {}})

    db.add(exp)
    db.commit()
    db.refresh(exp)

    log.info(
        "experiment created",
        experiment_id=exp.id,
        verdict=exp.verdict,
        baseline_run=baseline_run_id,
        candidate_run=candidate_run_id,
    )

    return _build_result_response(exp, result_data)


# ── GET /experiments — List All ───────────────────────────────────────────────

@router.get("", response_model=List[ExperimentListItem])
def list_experiments(
    verdict: Optional[str] = Query(
        default=None,
        description="Filter by verdict: pass, review, fail",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    List all experiments.

    Optionally filter by verdict (pass/review/fail).
    Returns lightweight summaries — use GET /experiments/{id} for full details.
    """
    query = db.query(Experiment)

    if verdict:
        query = query.filter(Experiment.verdict == verdict.lower())

    experiments = query.order_by(Experiment.created_at.desc()).limit(limit).all()

    return [
        ExperimentListItem(
            id=exp.id,
            name=exp.name,
            baseline_run_id=exp.baseline_run_id,
            candidate_run_id=exp.candidate_run_id,
            verdict=exp.verdict,
            created_at=exp.created_at,
        )
        for exp in experiments
    ]


# ── GET /experiments/{id} — Get Full Result ───────────────────────────────────

@router.get("/{experiment_id}", response_model=ExperimentResult)
def get_experiment(experiment_id: int, db: Session = Depends(get_db)):
    """
    Get full experiment result by ID.

    Returns:
      - Complete metric diff table
      - Verdict with reasoning
      - Improvements, regressions, fail/review reasons
      - Actionable suggestions
    """
    exp = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    if not exp:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment with id={experiment_id} not found",
        )

    result_data = exp.get_result()
    if not result_data:
        raise HTTPException(
            status_code=500,
            detail=f"Experiment {experiment_id} has no result data (possibly corrupted)",
        )

    return _build_result_response(exp, result_data)
