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

import asyncio
import json
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.database import get_db, SessionLocal
from app.models.eval_run import EvalRun as EvalRun_model, EvalRunCase, Evaluation
from app.providers import ModelGateway
from app.schemas.evaluation import (
    EvalRunCreate,
    EvalRunResponse,
    EvalRunCaseResponse,
    EvaluationResponse,
    EvalRunDetailResponse,
    EvalDiscoveryRequest,
    EvalDiscoveryResponse,
    MetricRequirement,
)
from app.schemas.analysis import FailureReport, AnalysisReport
from app.evaluation import orchestrator, failure_analyzer, report_generator, discovery
from app.core import event_bus

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


def _run_to_response(run: EvalRun_model) -> EvalRunResponse:
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


# ── Endpoint 1: Trigger Evaluation Run (Async — Day 7) ─────────────────────────

@router.post("/run", status_code=202)
def trigger_evaluation_run(
    run_request: EvalRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger a new evaluation run. Returns immediately with run_id.

    The evaluation runs in the background. Track progress via:
      GET /evaluations/{run_id}/stream  ← real-time SSE stream (recommended)
      GET /evaluations/{run_id}         ← poll for completion
    """
    import datetime
    from app.models.agent import Agent
    from app.models.test_suite import TestSuite

    log.info(
        "evaluation run requested",
        agent_id=run_request.agent_id,
        suite_id=run_request.suite_id,
        judge_provider=run_request.judge_provider,
        version_id=run_request.version_id,
    )

    gateway = get_gateway()

    # Validate upfront before queuing
    agent = db.query(Agent).filter(Agent.id == run_request.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent id={run_request.agent_id} not found")
    if agent.connection_type != "rest_api" or not agent.endpoint:
        raise HTTPException(status_code=400, detail=f"Agent must be REST API type with endpoint")
    suite = db.query(TestSuite).filter(TestSuite.id == run_request.suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite id={run_request.suite_id} not found")

    # Create EvalRun immediately so we have a run_id to return
    eval_run = EvalRun_model(
        agent_id=run_request.agent_id,
        suite_id=run_request.suite_id,
        version_id=run_request.version_id,
        status="queued",
        total_cases=0,
        judge_provider=run_request.judge_provider or "gemini",
        judge_model=run_request.judge_model,
        started_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(eval_run)
    db.commit()
    db.refresh(eval_run)
    run_id = eval_run.id

    # Queue background evaluation
    background_tasks.add_task(
        _run_evaluation_background,
        run_id=run_id,
        agent_id=run_request.agent_id,
        suite_id=run_request.suite_id,
        judge_provider=run_request.judge_provider or "gemini",
        judge_model=run_request.judge_model,
        selected_metrics=run_request.selected_metrics,
        version_id=run_request.version_id,
        gateway=gateway,
    )

    log.info("evaluation queued", run_id=run_id)

    return {
        "run_id": run_id,
        "status": "queued",
        "agent_id": run_request.agent_id,
        "suite_id": run_request.suite_id,
        "version_id": run_request.version_id,
        "message": f"Evaluation queued. Stream: GET /evaluations/{run_id}/stream",
        "stream_url": f"/evaluations/{run_id}/stream",
    }


def _run_evaluation_background(
    run_id: int,
    agent_id: int,
    suite_id: int,
    judge_provider: str,
    judge_model: Optional[str],
    selected_metrics: Optional[List[str]],
    version_id: Optional[int],
    gateway,
):
    """Background worker: runs evaluation and emits events to event_bus."""
    db = SessionLocal()
    q: asyncio.Queue = asyncio.Queue()
    event_bus._queues[run_id] = q

    def emit_sync(event: dict):
        q.put_nowait(json.dumps(event))

    try:
        # Mark as running
        run = db.query(EvalRun_model).filter(EvalRun_model.id == run_id).first()
        if run:
            run.status = "running"
            db.commit()

        emit_sync({"event": "started", "run_id": run_id})

        try:
            completed = orchestrator.run_evaluation(
                agent_id=agent_id,
                suite_id=suite_id,
                db=db,
                gateway=gateway,
                judge_provider=judge_provider,
                judge_model=judge_model,
                selected_metrics=selected_metrics,
                version_id=version_id,
                event_emitter=emit_sync,
            )
            emit_sync({
                "event": "run_complete",
                "run_id": run_id,
                "avg_score": completed.avg_score,
                "passed": completed.passed_cases,
                "failed": completed.failed_cases,
                "total": completed.total_cases,
                "status": "completed",
            })
        except Exception as e:
            log.error("background eval failed", run_id=run_id, error=str(e))
            emit_sync({"event": "error", "run_id": run_id, "message": str(e)})
            r = db.query(EvalRun_model).filter(EvalRun_model.id == run_id).first()
            if r:
                r.status = "failed"
                db.commit()
    finally:
        q.put_nowait(None)   # terminal sentinel — closes SSE stream
        db.close()
        # Clean up queue after a short delay
        import threading, time
        def cleanup():
            time.sleep(5)
            event_bus.cleanup_queue(run_id)
        threading.Thread(target=cleanup, daemon=True).start()


# ── SSE Stream Endpoint (Day 7) ──────────────────────────────────────────────

@router.get("/stream/{run_id}")
async def stream_eval_progress(run_id: int, db: Session = Depends(get_db)):
    """
    Server-Sent Events stream for real-time evaluation progress.

    Connect right after POST /evaluations/run. Events:
      {"event": "started", "run_id": 12}
      {"event": "case_done", "case": 3, "total": 35, "score": 0.88, "status": "success"}
      {"event": "run_complete", "avg_score": 0.79, "passed": 28, "failed": 7}
      {"event": "error", "message": "..."}

    If run already finished, returns a single run_complete event immediately.
    """
    # Already completed — no live queue
    if run_id not in event_bus._queues:
        run = db.query(EvalRun_model).filter(EvalRun_model.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        if run.status in ("completed", "failed"):
            async def already_done():
                yield f"data: {json.dumps({'event': run.status, 'run_id': run_id, 'avg_score': run.avg_score, 'passed': run.passed_cases, 'failed': run.failed_cases, 'total': run.total_cases})}\n\n"
            return StreamingResponse(already_done(), media_type="text/event-stream")

    async def event_generator():
        async for event_str in event_bus.subscribe(run_id):
            yield f"data: {event_str}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



# ── Endpoint: Discover Capabilities (Day 6) ────────────────────────────────────

@router.post("/discover", response_model=EvalDiscoveryResponse)
def discover_evaluation_capabilities(
    request: EvalDiscoveryRequest,
    db: Session = Depends(get_db),
):
    """
    Probe an agent and discover which evaluation metrics are available.

    RECOMMENDED first step before running POST /evaluations/run.

    What this does:
    1. Looks up the agent and test suite
    2. Picks a representative test case from the suite
    3. Sends it to the agent's endpoint
    4. Analyzes the response format
    5. Returns per-metric availability with:
       - Whether each metric can run right now
       - Why it can or cannot run
       - Exact step-by-step instructions to ENABLE unavailable metrics
       - The 'available_metrics' list to copy into POST /evaluations/run

    Example flow:
      POST /evaluations/discover  → {"available_metrics": ["relevance", "safety", "latency"]}
      POST /evaluations/run       → {"selected_metrics": ["relevance", "safety", "latency"]}
    """
    from app.models.agent import Agent
    from app.models.test_suite import TestSuite

    agent = db.query(Agent).filter(Agent.id == request.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent id={request.agent_id} not found")
    if agent.connection_type != "rest_api" or not agent.endpoint:
        raise HTTPException(
            status_code=400,
            detail="Discovery only available for REST API agents with an endpoint",
        )

    suite = db.query(TestSuite).filter(TestSuite.id == request.suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite id={request.suite_id} not found")

    result = discovery.discover_capabilities(agent=agent, suite=suite, db=db)

    return EvalDiscoveryResponse(
        agent_id=result["agent_id"],
        suite_id=result["suite_id"],
        agent_name=result["agent_name"],
        suite_name=result["suite_name"],
        probe_status=result["probe_status"],
        probe_error=result.get("probe_error"),
        probe_latency_ms=result.get("probe_latency_ms"),
        probe_input_used=result.get("probe_input_used"),
        detected_fields=result.get("detected_fields", {}),
        sample_agent_response=result.get("sample_agent_response"),
        metrics=[
            MetricRequirement(
                metric=m["metric"],
                available=m["available"],
                reason=m["reason"],
                agent_requirement=m.get("agent_requirement"),
                test_case_requirement=m.get("test_case_requirement"),
                how_to_enable=m.get("how_to_enable"),
                group=m.get("group"),
            )
            for m in result.get("metrics", [])
        ],
        available_metrics=result.get("available_metrics", []),
        unavailable_metrics=result.get("unavailable_metrics", []),
        next_steps=result.get("next_steps"),
    )


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
    query = db.query(EvalRun_model)

    if agent_id is not None:
        query = query.filter(EvalRun_model.agent_id == agent_id)
    if status is not None:
        query = query.filter(EvalRun_model.status == status)

    runs = query.order_by(EvalRun_model.created_at.desc()).limit(limit).all()
    return [_run_to_response(r) for r in runs]


# ── Endpoint 3: Get Run Summary ───────────────────────────────────────────────

@router.get("/{run_id}", response_model=EvalRunResponse)
def get_evaluation_run(run_id: int, db: Session = Depends(get_db)):
    """
    Get the summary of one evaluation run by ID.

    Returns aggregated scores (avg_score, passed_cases, failed_cases)
    but NOT the per-case breakdown. Use /cases for that.
    """
    run = db.query(EvalRun_model).filter(EvalRun_model.id == run_id).first()
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
    run = db.query(EvalRun_model).filter(EvalRun_model.id == run_id).first()
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


@router.get("/{run_id}/export")
def export_analysis_report(
    run_id: int,
    format: str = Query(default="markdown", description="Export format: markdown or json"),
    db: Session = Depends(get_db),
):
    """
    Export the evaluation report as a downloadable Markdown document or JSON structure.
    """
    from fastapi.responses import Response

    try:
        report = report_generator.generate_report(run_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if format.lower() == "json":
        return report

    # Render Markdown report
    md = [
        f"# Evaluation Report — Run #{run_id}",
        f"**Agent**: {report.agent_summary.name} (v{report.agent_summary.version})",
        f"**Provider / Model**: {report.agent_summary.provider} / {report.agent_summary.model}",
        f"**Overall Score**: `{report.performance.avg_score or 'N/A'}`",
        "",
        "## Performance Overview",
        f"- **Total Cases**: {report.performance.total_cases}",
        f"- **Passed**: {report.performance.passed_cases}",
        f"- **Failed**: {report.performance.failed_cases}",
        f"- **P50 Latency**: {report.performance.latency_p50_ms} ms",
        f"- **P95 Latency**: {report.performance.latency_p95_ms} ms",
        f"- **Est. Cost / Run**: ${report.performance.estimated_cost_per_run:.4f}",
        "",
        "## Metric Averages",
    ]
    for metric, val in report.performance.metric_averages.items():
        score_str = f"{val * 100:.1f}%" if isinstance(val, (int, float)) else "N/A"
        md.append(f"- **{metric}**: {score_str}")

    md.extend(["", "## Key Recommendations"])
    for rec in report.failure_analysis.recommendations:
        md.append(f"1. {rec}")

    md.extend(["", "## Model Alternatives & Price Comparison"])
    for alt in report.alternatives:
        md.append(f"- **{alt.model}** ({alt.provider}): {alt.cost_per_m_input} — *{alt.notes}*")

    md_content = "\n".join(md)
    filename = f"eval_report_run_{run_id}.md"

    return Response(
        content=md_content,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/gate")
def cicd_deployment_gate(
    run_id: int = Query(..., description="Evaluation run ID to evaluate for deployment"),
    min_score: float = Query(0.70, description="Minimum acceptable average correctness score (0.0 to 1.0)"),
    max_latency_p95: float = Query(5000.0, description="Maximum P95 latency in ms"),
    db: Session = Depends(get_db)
):
    """
    CI/CD Deployment Gate API Endpoint.
    
    Evaluates whether an evaluation run meets quality and performance criteria.
    Returns HTTP 200 with verdict='PASS' if deployable, or HTTP 422 if failed.
    """
    run = db.query(EvalRun_model).filter(EvalRun_model.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run #{run_id} not found")

    cases = db.query(EvalRunCase).filter(EvalRunCase.run_id == run_id).all()
    if not cases:
        raise HTTPException(status_code=400, detail=f"Run #{run_id} has no completed test cases")

    total_cases = len(cases)
    scores = []
    latencies = []

    for c in cases:
        evals = db.query(Evaluation).filter(Evaluation.run_case_id == c.id).all()
        for ev in evals:
            if ev.score is not None:
                scores.append(ev.score)
        if c.latency_ms is not None:
            latencies.append(c.latency_ms)

    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    latencies.sort()
    p95_index = int(0.95 * len(latencies)) if latencies else 0
    p95_latency = latencies[p95_index] if latencies else 0.0

    passed = (avg_score >= min_score) and (p95_latency <= max_latency_p95)
    verdict = "PASS" if passed else "FAIL"

    result = {
        "run_id": run_id,
        "verdict": verdict,
        "deployable": passed,
        "avg_score": avg_score,
        "min_score_required": min_score,
        "p95_latency_ms": p95_latency,
        "max_latency_p95_allowed": max_latency_p95,
        "total_cases_evaluated": total_cases,
    }

    if not passed:
        raise HTTPException(status_code=422, detail=result)

    return result
