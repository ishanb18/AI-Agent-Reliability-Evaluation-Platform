"""
Evaluations Router — REST API endpoints for triggering and reading evaluation runs.

Endpoints:
  POST /evaluations/run                          Trigger a new evaluation run
  GET  /evaluations                              List all runs (filter by agent, status)
  GET  /evaluations/{run_id}                     Get run summary
  GET  /evaluations/{run_id}/cases               Get all case results + scores for a run
  GET  /evaluations/{run_id}/cases/{case_id}     Get single case detail + scores
  GET  /evaluations/{run_id}/failures            Failure analysis for a run (Day 5)
  GET  /evaluations/{run_id}/report              Full analysis report (Day 5)
"""

import json
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation
from app.providers import ModelGateway
from app.schemas.evaluation import (
    EvalRunCreate,
    EvalRunResponse,
    EvalRunCaseResponse,
    EvaluationResponse,
    EvalRunDetailResponse,
)
from app.schemas.analysis import FailureReport, AnalysisReport
from app.evaluation import orchestrator, failure_analyzer, report_generator

log = structlog.get_logger()
router = APIRouter()

# Module-level gateway instance — shared across all requests in this router
# This mirrors how main.py uses the same gateway singleton
_gateway: Optional[ModelGateway] = None


def get_gateway() -> ModelGateway:
    """
    Returns the shared ModelGateway instance.
    Lazily initialized on first call (singleton pattern).

    Why not use FastAPI Depends for this?
    The gateway maintains in-memory telemetry state across requests.
    Creating a new instance per request would reset all telemetry counters.
    A module-level singleton preserves state across requests.
    """
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway


# ── Helper: ORM → Pydantic conversion ────────────────────────────────────────

def _evaluation_to_response(evaluation: Evaluation) -> EvaluationResponse:
    """
    Convert Evaluation ORM object to EvaluationResponse Pydantic model.

    Deserializes JSON fields (reasoning, metrics_evaluated, metrics_skipped)
    back into Python dicts/lists and computes the overall_score.
    """
    return EvaluationResponse(
        id=evaluation.id,
        run_case_id=evaluation.run_case_id,
        correctness=evaluation.correctness,
        relevance=evaluation.relevance,
        faithfulness=evaluation.faithfulness,
        completeness=evaluation.completeness,
        instruction_following=evaluation.instruction_following,
        safety_score=evaluation.safety_score,
        tool_accuracy=evaluation.tool_accuracy,
        trajectory_score=evaluation.trajectory_score,
        judge_provider=evaluation.judge_provider,
        judge_model=evaluation.judge_model,
        reasoning=evaluation.get_reasoning_dict() or None,
        metrics_evaluated=evaluation.get_metrics_evaluated_list() or None,
        metrics_skipped=evaluation.get_metrics_skipped_dict() or None,
        overall_score=evaluation.compute_overall_score(),
        created_at=evaluation.created_at,
    )


def _run_case_to_response(run_case: EvalRunCase) -> EvalRunCaseResponse:
    """
    Convert EvalRunCase ORM object to EvalRunCaseResponse Pydantic model.

    Deserializes JSON fields (tool_trace, context_chunks) and
    nests the Evaluation if present.
    """
    eval_response = None
    if run_case.evaluation:
        eval_response = _evaluation_to_response(run_case.evaluation)

    return EvalRunCaseResponse(
        id=run_case.id,
        run_id=run_case.run_id,
        test_case_id=run_case.test_case_id,
        agent_output=run_case.agent_output,
        agent_raw_response=run_case.agent_raw_response,
        tool_trace=run_case.get_tool_trace_list() or None,
        context_chunks=run_case.get_context_chunks_list() or None,
        latency_ms=run_case.latency_ms,
        input_tokens=run_case.input_tokens,
        output_tokens=run_case.output_tokens,
        estimated_cost=run_case.estimated_cost,
        step_count=run_case.step_count,
        status=run_case.status,
        error=run_case.error,
        evaluation=eval_response,
        created_at=run_case.created_at,
    )


def _run_to_response(run: EvalRun) -> EvalRunResponse:
    """Convert EvalRun ORM object to EvalRunResponse Pydantic model."""
    return EvalRunResponse(
        id=run.id,
        agent_id=run.agent_id,
        suite_id=run.suite_id,
        status=run.status,
        total_cases=run.total_cases,
        passed_cases=run.passed_cases,
        failed_cases=run.failed_cases,
        avg_score=run.avg_score,
        judge_provider=run.judge_provider,
        judge_model=run.judge_model,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


# ── Endpoint 1: Trigger Evaluation Run ───────────────────────────────────────

@router.post("/run", response_model=EvalRunResponse, status_code=201)
def trigger_evaluation_run(
    run_request: EvalRunCreate,
    db: Session = Depends(get_db),
):
    """
    Trigger a new evaluation run: run all test cases from a suite against an agent.

    What happens:
    1. Validates the agent exists and has a REST endpoint.
    2. Validates the test suite exists and has test cases.
    3. Invokes the agent for each test case (POST to agent's endpoint).
    4. Parses each response using the agent's response_mapping.
    5. Runs deterministic + LLM-as-a-Judge evaluators on each response.
    6. Stores EvalRunCase + Evaluation records in DB for each case.
    7. Returns the completed EvalRun with aggregated scores.

    Note: This runs synchronously. For large suites (100+ cases), this
    endpoint may take several minutes. Async/background execution is planned
    for a future day.
    """
    log.info(
        "evaluation run requested",
        agent_id=run_request.agent_id,
        suite_id=run_request.suite_id,
        judge_provider=run_request.judge_provider,
    )

    gateway = get_gateway()

    try:
        eval_run = orchestrator.run_evaluation(
            agent_id=run_request.agent_id,
            suite_id=run_request.suite_id,
            db=db,
            gateway=gateway,
            judge_provider=run_request.judge_provider or "gemini",
            judge_model=run_request.judge_model,
        )
    except ValueError as e:
        # Validation errors (agent not found, no endpoint, empty suite)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("evaluation run failed with unexpected error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Evaluation run failed: {str(e)}")

    return _run_to_response(eval_run)


# ── Endpoint 2: List All Runs ─────────────────────────────────────────────────

@router.get("", response_model=List[EvalRunResponse])
def list_evaluation_runs(
    agent_id: Optional[int] = Query(default=None, description="Filter runs by agent ID"),
    status: Optional[str] = Query(default=None, description="Filter by status: pending, running, completed, failed"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of runs to return"),
    db: Session = Depends(get_db),
):
    """
    List all evaluation runs, optionally filtered by agent or status.

    Returns lightweight run summaries (no per-case details).
    Use GET /evaluations/{run_id}/cases to get per-case results.
    """
    query = db.query(EvalRun)

    if agent_id is not None:
        query = query.filter(EvalRun.agent_id == agent_id)
    if status is not None:
        query = query.filter(EvalRun.status == status)

    runs = query.order_by(EvalRun.created_at.desc()).limit(limit).all()
    return [_run_to_response(r) for r in runs]


# ── Endpoint 3: Get Run Summary ───────────────────────────────────────────────

@router.get("/{run_id}", response_model=EvalRunResponse)
def get_evaluation_run(run_id: int, db: Session = Depends(get_db)):
    """
    Get the summary of one evaluation run by ID.

    Returns aggregated scores (avg_score, passed_cases, failed_cases)
    but NOT the per-case breakdown. Use /cases for that.
    """
    run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Evaluation run with id={run_id} not found")
    return _run_to_response(run)


# ── Endpoint 4: Get All Cases for a Run ──────────────────────────────────────

@router.get("/{run_id}/cases", response_model=EvalRunDetailResponse)
def get_run_cases(run_id: int, db: Session = Depends(get_db)):
    """
    Get the full per-case breakdown for one evaluation run.

    Returns:
    - Run summary (same as GET /evaluations/{run_id})
    - All EvalRunCase records with:
        - Agent's output for each test case
        - Latency, tokens, cost
        - Nested Evaluation scores for each case
        - Which metrics were evaluated vs skipped (and why)
        - Judge's reasoning for each metric score

    Use this to see exactly what the agent said and how each response was scored.
    """
    run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Evaluation run with id={run_id} not found")

    # cases are loaded via lazy="selectin" on the relationship (from eval_run.py)
    return EvalRunDetailResponse(
        run=_run_to_response(run),
        cases=[_run_case_to_response(c) for c in run.cases],
    )


# ── Endpoint 5: Get Single Case Detail ───────────────────────────────────────

@router.get("/{run_id}/cases/{case_id}", response_model=EvalRunCaseResponse)
def get_run_case_detail(run_id: int, case_id: int, db: Session = Depends(get_db)):
    """
    Get the detailed evaluation for a single test case within a run.

    Returns:
    - Full agent response (output + raw JSON)
    - Tool calls made (if any)
    - Context chunks used (if RAG agent)
    - Performance metrics (latency, tokens, cost)
    - All metric scores with judge reasoning
    - Which metrics were skipped and why
    """
    run_case = (
        db.query(EvalRunCase)
        .filter(EvalRunCase.id == case_id, EvalRunCase.run_id == run_id)
        .first()
    )
    if not run_case:
        raise HTTPException(
            status_code=404,
            detail=f"Case with id={case_id} not found in run {run_id}",
        )
    return _run_case_to_response(run_case)


# ── Day 5: Failure Analysis & Report Endpoints ──────────────────────────────

@router.get("/{run_id}/failures", response_model=FailureReport)
def get_failure_analysis(run_id: int, db: Session = Depends(get_db)):
    """
    Get failure analysis for a completed evaluation run.

    Analyzes all failed test cases and groups them by:
    - Test category (general, rag, tool_use, security)
    - Worst metric (which metric scored lowest)

    Returns failure groups with representative examples and
    actionable recommendations for improvement.

    Requires: run must have status='completed'.
    """
    try:
        result = failure_analyzer.analyze_failures(run_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result


@router.get("/{run_id}/report", response_model=AnalysisReport)
def get_analysis_report(run_id: int, db: Session = Depends(get_db)):
    """
    Get the complete end-to-end analysis report for an evaluation run.

    Produces spec §5 sections A-D:
      A. What They Used (agent config, provider, model, pricing)
      B. How the System Performed (metric averages, latency P50/P95/P99, cost)
      C. Where Things Went Wrong (failure analysis with recommendations)
      D. What They Could Use Instead (cheaper/faster model alternatives)

    Requires: run must have status='completed'.
    """
    try:
        result = report_generator.generate_report(run_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result
