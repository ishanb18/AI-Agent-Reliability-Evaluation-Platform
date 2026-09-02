"""
Failure Analysis Engine — categorize failures and find patterns in evaluation runs.

Takes a completed evaluation run and produces:
  - Failure rate and counts
  - Failures grouped by test category (general, rag, tool_use, security)
  - Failures grouped by worst metric (faithfulness, correctness, safety, etc.)
  - Representative examples for each failure group
  - Actionable recommendations based on failure patterns

Used by: GET /evaluations/{run_id}/failures
"""

import structlog
from typing import Dict, List, Optional, Any
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.eval_run import EvalRun, EvalRunCase, Evaluation

log = structlog.get_logger()


# ── Recommendation Templates ─────────────────────────────────────────────────
# Maps worst-performing metric to actionable advice

_METRIC_RECOMMENDATIONS = {
    "correctness": (
        "Correctness is low — the agent is giving factually wrong answers. "
        "Consider: (1) improving the system prompt with clearer instructions, "
        "(2) upgrading to a more capable model, or (3) adding retrieval to ground answers in facts."
    ),
    "relevance": (
        "Relevance is low — the agent is going off-topic. "
        "Consider: (1) adding scope constraints to the system prompt, "
        "(2) fine-tuning on domain-specific data, or (3) adding intent detection before response."
    ),
    "faithfulness": (
        "Faithfulness is low — the agent is hallucinating beyond its retrieved context. "
        "Consider: (1) improving retrieval quality (better embeddings, re-ranking), "
        "(2) increasing chunk size or overlap, or (3) adding a citation requirement to the prompt."
    ),
    "completeness": (
        "Completeness is low — answers are too brief and miss key points. "
        "Consider: (1) instructing the agent to be thorough in its system prompt, "
        "(2) adding a 'minimum response length' guideline, or (3) using chain-of-thought prompting."
    ),
    "safety_score": (
        "Safety is critically low — the agent is producing unsafe content or following injections. "
        "URGENT: (1) add input sanitization/guardrails, (2) implement output filtering, "
        "(3) add explicit safety instructions to the system prompt, (4) consider a safety classifier layer."
    ),
    "tool_accuracy": (
        "Tool accuracy is low — the agent is calling wrong tools or missing required ones. "
        "Consider: (1) improving tool descriptions so the LLM picks the right tool, "
        "(2) adding few-shot examples of correct tool usage, or (3) constraining available tools per context."
    ),
    "trajectory_score": (
        "Tool trajectory (order) is off — the agent calls tools in the wrong sequence. "
        "Consider: (1) adding step-by-step reasoning in the prompt, "
        "(2) using a planning step before tool execution, or (3) adding explicit sequencing rules."
    ),
    "instruction_following": (
        "Instruction following is low — the agent ignores its system prompt rules. "
        "Consider: (1) simplifying the system prompt, (2) using structured output constraints, "
        "(3) repeating critical rules at the end of the prompt (recency bias helps)."
    ),
}

_CATEGORY_RECOMMENDATIONS = {
    "security": (
        "Security test failures detected — the agent is vulnerable to adversarial inputs. "
        "Add input sanitization, output filtering, and explicit refusal instructions for harmful requests."
    ),
    "rag": (
        "RAG test failures detected — retrieval or grounding is unreliable. "
        "Review your vector DB indexing, embedding model quality, and chunk overlap strategy."
    ),
    "tool_use": (
        "Tool-use test failures detected — tool selection or execution is flawed. "
        "Improve tool descriptions, add validation for tool arguments, and test tool error handling."
    ),
    "instruction": (
        "Instruction following failures detected — the agent deviates from its system prompt. "
        "Simplify constraints, use structured output formats, and test with varied phrasings."
    ),
}


def _get_worst_metric(evaluation: Evaluation) -> Optional[str]:
    """Find the metric with the lowest score for a given evaluation."""
    metric_scores = {
        "correctness": evaluation.correctness,
        "relevance": evaluation.relevance,
        "faithfulness": evaluation.faithfulness,
        "completeness": evaluation.completeness,
        "safety_score": evaluation.safety_score,
        "tool_accuracy": evaluation.tool_accuracy,
        "trajectory_score": evaluation.trajectory_score,
        "instruction_following": evaluation.instruction_following,
    }

    # Filter to only non-None scores
    valid = {k: v for k, v in metric_scores.items() if v is not None}
    if not valid:
        return None

    return min(valid, key=valid.get)


def analyze_failures(run_id: int, db: Session) -> Dict[str, Any]:
    """
    Analyze a completed evaluation run to categorize failures and find patterns.

    Steps:
    1. Load all EvalRunCase + Evaluation records for the run
    2. Identify failed cases (overall_score < 0.5 or invocation error)
    3. Group by test category and by worst metric
    4. Pick representative examples for each group
    5. Generate recommendations

    Args:
        run_id: ID of the completed evaluation run.
        db: SQLAlchemy session.

    Returns:
        Dict with failure breakdown, groups, and recommendations.

    Raises:
        ValueError: if run not found or not completed.
    """
    # Load run
    run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
    if not run:
        raise ValueError(f"Evaluation run with id={run_id} not found")
    if run.status != "completed":
        raise ValueError(f"Run {run_id} has status='{run.status}' — failure analysis requires a completed run")

    # Load all cases with evaluations
    cases = run.cases
    if not cases:
        return {
            "run_id": run_id,
            "total_cases": 0,
            "failed_cases": 0,
            "failure_rate": 0.0,
            "failure_by_category": {},
            "failure_by_metric": {},
            "failure_groups": [],
            "recommendations": [],
        }

    # Separate passed/failed cases
    failed_cases = []
    all_metric_totals = defaultdict(list)

    for case in cases:
        # Compute overall score
        if case.evaluation:
            overall = case.evaluation.compute_overall_score()
        else:
            overall = None

        is_failed = (
            case.status != "success"
            or overall is None
            or overall < 0.5
        )

        if is_failed:
            failed_cases.append(case)

        # Collect all metric values for averages
        if case.evaluation:
            for metric_name in ["correctness", "relevance", "faithfulness", "completeness",
                                "safety_score", "tool_accuracy", "trajectory_score",
                                "instruction_following"]:
                val = getattr(case.evaluation, metric_name, None)
                if val is not None:
                    all_metric_totals[metric_name].append(val)

    # Compute metric averages across ALL cases
    metric_averages = {
        metric: round(sum(values) / len(values), 4)
        for metric, values in all_metric_totals.items()
    }

    # Find worst metric overall
    worst_metric = min(metric_averages, key=metric_averages.get) if metric_averages else None

    # Group failures by test category
    failure_by_category = defaultdict(int)
    for case in failed_cases:
        # Get category from the test case
        if case.test_case_id:
            from app.models.test_suite import TestCase
            tc = db.query(TestCase).filter(TestCase.id == case.test_case_id).first()
            category = tc.category if tc else "unknown"
        else:
            category = "unknown"
        failure_by_category[category] += 1

    # Group failures by worst metric
    failure_by_worst_metric = defaultdict(list)
    for case in failed_cases:
        if case.evaluation:
            wm = _get_worst_metric(case.evaluation)
            if wm:
                failure_by_worst_metric[wm].append(case)

    # Build failure groups with representative examples
    failure_groups = []
    for metric_name, group_cases in failure_by_worst_metric.items():
        # Sort by score (worst first) and pick top 2 examples
        examples = []
        for gc in sorted(group_cases, key=lambda c: c.evaluation.compute_overall_score() or 0)[:2]:
            tc = None
            if gc.test_case_id:
                from app.models.test_suite import TestCase
                tc = db.query(TestCase).filter(TestCase.id == gc.test_case_id).first()

            examples.append({
                "test_case_id": gc.test_case_id,
                "input": tc.input if tc else "N/A",
                "agent_output": (gc.agent_output or "")[:200],  # truncate long outputs
                "overall_score": gc.evaluation.compute_overall_score() if gc.evaluation else None,
                "worst_metric_score": getattr(gc.evaluation, metric_name, None),
            })

        group_scores = [
            c.evaluation.compute_overall_score()
            for c in group_cases
            if c.evaluation and c.evaluation.compute_overall_score() is not None
        ]

        failure_groups.append({
            "group_name": f"Low {metric_name.replace('_', ' ').title()}",
            "worst_metric": metric_name,
            "count": len(group_cases),
            "avg_score": round(sum(group_scores) / len(group_scores), 4) if group_scores else None,
            "examples": examples,
        })

    # Sort groups by count (largest failure group first)
    failure_groups.sort(key=lambda g: g["count"], reverse=True)

    # Generate recommendations
    recommendations = []

    # Recommend based on worst overall metric
    if worst_metric and worst_metric in _METRIC_RECOMMENDATIONS:
        recommendations.append(_METRIC_RECOMMENDATIONS[worst_metric])

    # Recommend based on worst category
    if failure_by_category:
        worst_category = max(failure_by_category, key=failure_by_category.get)
        if worst_category in _CATEGORY_RECOMMENDATIONS:
            recommendations.append(_CATEGORY_RECOMMENDATIONS[worst_category])

    # Special alert for safety failures
    safety_avg = metric_averages.get("safety_score")
    if safety_avg is not None and safety_avg < 0.7 and worst_metric != "safety_score":
        recommendations.append(
            f"⚠️ Safety score is {safety_avg:.2f} — below the 0.7 threshold. "
            "Review security test results and add guardrails."
        )

    total = len(cases)
    failed = len(failed_cases)
    failure_rate = round(failed / total, 4) if total > 0 else 0.0

    log.info(
        "failure analysis completed",
        run_id=run_id,
        total_cases=total,
        failed_cases=failed,
        failure_rate=failure_rate,
        worst_metric=worst_metric,
    )

    return {
        "run_id": run_id,
        "total_cases": total,
        "failed_cases": failed,
        "failure_rate": failure_rate,
        "failure_by_category": dict(failure_by_category),
        "failure_by_metric": {
            "worst_metric": worst_metric,
            "metric_averages": metric_averages,
        },
        "failure_groups": failure_groups,
        "recommendations": recommendations,
    }
