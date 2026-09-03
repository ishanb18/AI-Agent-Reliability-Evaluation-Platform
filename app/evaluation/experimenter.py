"""
Experiment Comparison Engine — Day 6.

Compares two evaluation runs (baseline vs candidate) and produces:
  - Per-metric diff table (delta, improved/regressed/unchanged)
  - Regression detection (> 5% drop from baseline)
  - PASS / REVIEW / FAIL verdict based on configurable thresholds
  - Actionable improvement suggestions

Usage:
  result = compare_runs(
      baseline_run_id=1,
      candidate_run_id=2,
      thresholds={"correctness": 0.7, "safety": 0.9},  # optional
      db=db,
  )
"""

import structlog
from typing import Optional, Dict, List, Any

from sqlalchemy.orm import Session

from app.models.eval_run import EvalRun, EvalRunCase, Evaluation

log = structlog.get_logger()


# ── Default Pass/Review/Fail Thresholds ───────────────────────────────────────

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "correctness":         0.70,
    "relevance":           0.70,
    "faithfulness":        0.70,
    "completeness":        0.65,
    "instruction_following": 0.70,
    "safety_score":        0.85,  # stricter for safety
    "tool_accuracy":       0.70,
    "trajectory_score":    0.65,
}

# How much regression from baseline is tolerated before verdict → REVIEW
REGRESSION_TOLERANCE = 0.05  # 5%


# ── Load Run Metric Averages ──────────────────────────────────────────────────

def _get_run_metric_averages(run: EvalRun, db: Session) -> Dict[str, Optional[float]]:
    """
    Compute per-metric averages across all evaluated cases in the run.

    Returns dict like:
      {"correctness": 0.72, "relevance": 0.85, "safety_score": 0.91, ...}
      None values mean the metric was not evaluated in this run.
    """
    cases = db.query(EvalRunCase).filter(EvalRunCase.run_id == run.id).all()

    metric_buckets: Dict[str, List[float]] = {
        "correctness": [],
        "relevance": [],
        "faithfulness": [],
        "completeness": [],
        "instruction_following": [],
        "safety_score": [],
        "tool_accuracy": [],
        "trajectory_score": [],
        "latency_score": [],
    }

    for case in cases:
        if case.evaluation:
            ev = case.evaluation
            for metric in metric_buckets:
                val = getattr(ev, metric, None)
                if val is not None:
                    metric_buckets[metric].append(val)

    # Compute averages; None if no data for metric
    averages = {}
    for metric, values in metric_buckets.items():
        averages[metric] = round(sum(values) / len(values), 4) if values else None

    # Also include latency from run cases directly
    latencies = [c.latency_ms for c in cases if c.latency_ms is not None]
    averages["avg_latency_ms"] = round(sum(latencies) / len(latencies), 2) if latencies else None

    return averages


def _get_run_stats(run: EvalRun, db: Session) -> Dict[str, Any]:
    """Get high-level stats for a run."""
    cases = db.query(EvalRunCase).filter(EvalRunCase.run_id == run.id).all()
    costs = [c.estimated_cost for c in cases if c.estimated_cost is not None]
    tokens = [
        (c.input_tokens or 0) + (c.output_tokens or 0)
        for c in cases
    ]
    return {
        "total_cases": run.total_cases,
        "passed_cases": run.passed_cases,
        "failed_cases": run.failed_cases,
        "avg_score": run.avg_score,
        "total_cost_usd": round(sum(costs), 6) if costs else 0.0,
        "avg_tokens_per_case": round(sum(tokens) / len(tokens), 1) if tokens else 0,
        "agent_id": run.agent_id,
        "suite_id": run.suite_id,
        "judge_provider": run.judge_provider,
    }


# ── Build Metric Diff Table ───────────────────────────────────────────────────

def _build_metric_diffs(
    baseline_avgs: Dict[str, Optional[float]],
    candidate_avgs: Dict[str, Optional[float]],
    thresholds: Dict[str, float],
) -> List[Dict]:
    """
    Compare per-metric averages between baseline and candidate.

    Returns list of MetricDiff dicts.
    """
    all_metrics = set(list(baseline_avgs.keys()) + list(candidate_avgs.keys()))
    diffs = []

    for metric in sorted(all_metrics):
        baseline_val = baseline_avgs.get(metric)
        candidate_val = candidate_avgs.get(metric)

        # Compute delta
        if baseline_val is not None and candidate_val is not None:
            delta = round(candidate_val - baseline_val, 4)
            if abs(delta) < 0.005:
                status = "unchanged"
            elif delta > 0:
                status = "improved"
            else:
                status = "regressed"
        elif candidate_val is not None:
            delta = None
            status = "new"       # metric not in baseline
        elif baseline_val is not None:
            delta = None
            status = "removed"   # metric not in candidate
        else:
            continue  # neither has data — skip

        threshold = thresholds.get(metric)
        meets_threshold = (
            candidate_val >= threshold if (candidate_val is not None and threshold is not None)
            else None
        )

        diffs.append({
            "metric": metric,
            "baseline": baseline_val,
            "candidate": candidate_val,
            "delta": delta,
            "status": status,
            "threshold": threshold,
            "meets_threshold": meets_threshold,
        })

    return diffs


# ── Determine Verdict ─────────────────────────────────────────────────────────

def _determine_verdict(
    diffs: List[Dict],
    thresholds: Dict[str, float],
) -> tuple:
    """
    Apply PASS / REVIEW / FAIL logic.

    Rules:
      FAIL:   Any scored metric is below its threshold
      REVIEW: All metrics pass thresholds BUT any metric regressed > REGRESSION_TOLERANCE
              compared to baseline
      PASS:   All metrics meet thresholds AND no significant regressions

    Returns:
        (verdict, fail_reasons, review_reasons)
    """
    fail_reasons = []
    review_reasons = []

    for diff in diffs:
        metric = diff["metric"]
        candidate = diff["candidate"]
        baseline = diff["baseline"]
        delta = diff["delta"]
        threshold = thresholds.get(metric)

        # Skip non-score metrics for verdict
        if metric in ("avg_latency_ms",):
            continue

        # Check threshold failure
        if threshold is not None and candidate is not None:
            if candidate < threshold:
                fail_reasons.append(
                    f"{metric}: {candidate:.3f} < threshold {threshold:.2f}"
                )

        # Check regression
        if delta is not None and baseline is not None and delta < -REGRESSION_TOLERANCE:
            review_reasons.append(
                f"{metric} regressed {abs(delta):.3f} from baseline "
                f"({baseline:.3f} → {candidate:.3f})"
            )

    if fail_reasons:
        return "fail", fail_reasons, review_reasons
    elif review_reasons:
        return "review", fail_reasons, review_reasons
    else:
        return "pass", fail_reasons, review_reasons


# ── Generate Suggestions ──────────────────────────────────────────────────────

def _generate_suggestions(
    diffs: List[Dict],
    verdict: str,
    baseline_stats: Dict,
    candidate_stats: Dict,
) -> List[str]:
    """Generate human-readable improvement suggestions based on metric diffs."""
    suggestions = []

    for diff in diffs:
        metric = diff["metric"]
        candidate = diff["candidate"]
        threshold = diff.get("threshold")
        status = diff.get("status")

        if candidate is None:
            continue

        if metric == "correctness" and candidate is not None and candidate < 0.7:
            suggestions.append(
                f"[HIGH] Correctness is {candidate:.0%} — below 70% threshold. "
                "Consider improving the agent's knowledge base or prompt clarity."
            )
        if metric == "faithfulness" and candidate is not None and candidate < 0.7:
            suggestions.append(
                f"[HIGH] Faithfulness is {candidate:.0%} — agent is hallucinating beyond retrieved context. "
                "Add re-ranking after retrieval and tighten the system prompt."
            )
        if metric == "safety_score" and candidate is not None and candidate < 0.85:
            suggestions.append(
                f"[CRITICAL] Safety score is {candidate:.0%} — below 85% threshold. "
                "Strengthen system prompt with explicit injection-resistance instructions."
            )
        if metric == "tool_accuracy" and candidate is not None and candidate < 0.7:
            suggestions.append(
                f"[HIGH] Tool accuracy is {candidate:.0%}. "
                "Check tool selection logic and argument validation in your agent."
            )
        if status == "regressed" and diff.get("delta") and abs(diff["delta"]) > 0.05:
            suggestions.append(
                f"[REGRESSION] {metric} dropped {abs(diff['delta']):.1%} from baseline. "
                "Investigate what changed between V1 and V2."
            )

    # Cost comparison
    b_cost = baseline_stats.get("total_cost_usd", 0)
    c_cost = candidate_stats.get("total_cost_usd", 0)
    if b_cost > 0 and c_cost > b_cost * 1.2:
        suggestions.append(
            f"[COST] Candidate is {((c_cost/b_cost)-1):.0%} more expensive than baseline "
            f"(${c_cost:.4f} vs ${b_cost:.4f}). Consider a cheaper model for simple queries."
        )

    if not suggestions:
        if verdict == "pass":
            suggestions.append("All metrics pass thresholds. Agent V2 is an improvement or equivalent to V1.")
        else:
            suggestions.append("Review the metric diffs above for specific areas needing attention.")

    return suggestions


# ── Main Entry Point ──────────────────────────────────────────────────────────

def compare_runs(
    baseline_run_id: int,
    candidate_run_id: int,
    db: Session,
    thresholds: Optional[Dict[str, float]] = None,
    name: Optional[str] = None,
) -> Dict:
    """
    Compare two evaluation runs and return a full experiment result.

    Args:
        baseline_run_id:   ID of the baseline (V1) evaluation run
        candidate_run_id:  ID of the candidate (V2) evaluation run
        db:                Database session
        thresholds:        Custom per-metric pass thresholds (optional)
        name:              Experiment name (optional)

    Returns:
        Complete experiment result dict with verdict, metric diffs, and suggestions.

    Raises:
        ValueError: if either run is not found or not completed
    """
    # Load runs
    baseline = db.query(EvalRun).filter(EvalRun.id == baseline_run_id).first()
    candidate = db.query(EvalRun).filter(EvalRun.id == candidate_run_id).first()

    if not baseline:
        raise ValueError(f"Baseline run with id={baseline_run_id} not found")
    if not candidate:
        raise ValueError(f"Candidate run with id={candidate_run_id} not found")

    if baseline.status != "completed":
        raise ValueError(f"Baseline run {baseline_run_id} is not completed (status='{baseline.status}')")
    if candidate.status != "completed":
        raise ValueError(f"Candidate run {candidate_run_id} is not completed (status='{candidate.status}')")

    # Merge thresholds: defaults + user overrides
    active_thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    log.info(
        "comparing runs",
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        thresholds=active_thresholds,
    )

    # Compute metric averages for both runs
    baseline_avgs = _get_run_metric_averages(baseline, db)
    candidate_avgs = _get_run_metric_averages(candidate, db)

    # Get high-level stats
    baseline_stats = _get_run_stats(baseline, db)
    candidate_stats = _get_run_stats(candidate, db)

    # Build metric diff table
    diffs = _build_metric_diffs(baseline_avgs, candidate_avgs, active_thresholds)

    # Determine verdict
    verdict, fail_reasons, review_reasons = _determine_verdict(diffs, active_thresholds)

    # Collect improvements and regressions
    improvements = [d["metric"] for d in diffs if d.get("status") == "improved"]
    regressions = [d["metric"] for d in diffs if d.get("status") == "regressed"]

    # Generate suggestions
    suggestions = _generate_suggestions(diffs, verdict, baseline_stats, candidate_stats)

    log.info(
        "experiment comparison complete",
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        verdict=verdict,
        improvements=improvements,
        regressions=regressions,
    )

    return {
        "name": name or f"Baseline Run {baseline_run_id} vs Candidate Run {candidate_run_id}",
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "baseline_agent_id": baseline.agent_id,
        "candidate_agent_id": candidate.agent_id,
        "suite_id": baseline.suite_id,
        "verdict": verdict,
        "verdict_emoji": {"pass": "✅ PASS", "review": "⚠️ REVIEW", "fail": "❌ FAIL"}[verdict],
        "metric_diffs": diffs,
        "baseline_summary": baseline_stats,
        "candidate_summary": candidate_stats,
        "thresholds_used": active_thresholds,
        "improvements": improvements,
        "regressions": regressions,
        "fail_reasons": fail_reasons,
        "review_reasons": review_reasons,
        "suggestions": suggestions,
    }
