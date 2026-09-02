"""
Analysis Report Generator — produces the end-to-end evaluation report.

Implements spec §5 "What We Tell The User About Their System":
  Section A: What They Used (agent config, provider, model, pricing)
  Section B: How the System Performed (metric averages, latency percentiles, cost)
  Section C: Where Things Went Wrong (failure analysis)
  Section D: What They Could Use Instead (model/tool alternatives)

Used by: GET /evaluations/{run_id}/report
"""

import structlog
from typing import Dict, Any, List, Optional
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation
from app.evaluation import failure_analyzer
from app.evaluation.deterministic import _PRICING_TABLE

log = structlog.get_logger()


# ── Alternative Model Recommendations ─────────────────────────────────────────

_MODEL_ALTERNATIVES = {
    "openai/gpt-4o": [
        {"provider": "openai", "model": "gpt-4o-mini", "cost_per_1m_input": 0.15,
         "speed": "fast", "note": "100x cheaper, ~90% quality for most tasks"},
        {"provider": "gemini", "model": "gemini-1.5-flash", "cost_per_1m_input": 0.075,
         "speed": "very fast", "note": "Free tier available, good RAG support"},
        {"provider": "groq", "model": "llama3-70b-8192", "cost_per_1m_input": 0.59,
         "speed": "fastest", "note": "Free tier, great for quick tasks"},
    ],
    "openai/gpt-4o-mini": [
        {"provider": "gemini", "model": "gemini-1.5-flash", "cost_per_1m_input": 0.075,
         "speed": "very fast", "note": "2x cheaper, comparable quality"},
        {"provider": "groq", "model": "llama3-8b-8192", "cost_per_1m_input": 0.05,
         "speed": "fastest", "note": "3x cheaper, good for simple tasks"},
    ],
    "gemini/gemini-1.5-pro": [
        {"provider": "gemini", "model": "gemini-1.5-flash", "cost_per_1m_input": 0.075,
         "speed": "very fast", "note": "47x cheaper, suitable for most use cases"},
        {"provider": "openai", "model": "gpt-4o-mini", "cost_per_1m_input": 0.15,
         "speed": "fast", "note": "23x cheaper, strong instruction following"},
    ],
    "anthropic/claude-3-5-sonnet": [
        {"provider": "openai", "model": "gpt-4o", "cost_per_1m_input": 5.00,
         "speed": "moderate", "note": "Similar quality, broader tool support"},
        {"provider": "gemini", "model": "gemini-1.5-pro", "cost_per_1m_input": 3.50,
         "speed": "moderate", "note": "Slightly cheaper, best context window"},
        {"provider": "groq", "model": "llama3-70b-8192", "cost_per_1m_input": 0.59,
         "speed": "fastest", "note": "5x cheaper, free tier available"},
    ],
}

# Default alternatives for unlisted models
_DEFAULT_ALTERNATIVES = [
    {"provider": "gemini", "model": "gemini-1.5-flash", "cost_per_1m_input": 0.075,
     "speed": "very fast", "note": "Free tier, fast, good quality"},
    {"provider": "groq", "model": "llama3-70b-8192", "cost_per_1m_input": 0.59,
     "speed": "fastest", "note": "Free tier, fastest inference"},
    {"provider": "ollama", "model": "llama3", "cost_per_1m_input": 0.0,
     "speed": "depends on hardware", "note": "Free, local, no API key needed"},
]


def _compute_percentiles(values: List[float], percentiles: List[int]) -> Dict[str, float]:
    """Compute specified percentiles from a list of values."""
    if not values:
        return {f"p{p}": None for p in percentiles}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    result = {}
    for p in percentiles:
        idx = int(p / 100 * (n - 1))
        result[f"p{p}"] = round(sorted_vals[idx], 2)
    return result


def generate_report(run_id: int, db: Session) -> Dict[str, Any]:
    """
    Generate the complete end-to-end analysis report for an evaluation run.

    Args:
        run_id: ID of the completed evaluation run.
        db: SQLAlchemy session.

    Returns:
        Dict with sections A (agent profile), B (performance), C (failures), D (alternatives).

    Raises:
        ValueError: if run not found or not completed.
    """
    # Load run and agent
    run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
    if not run:
        raise ValueError(f"Evaluation run with id={run_id} not found")
    if run.status != "completed":
        raise ValueError(f"Run {run_id} has status='{run.status}' — report requires a completed run")

    agent = db.query(Agent).filter(Agent.id == run.agent_id).first()
    if not agent:
        raise ValueError(f"Agent with id={run.agent_id} not found")

    cases = run.cases

    # ── Section A: What They Used ─────────────────────────────────────────────
    components = agent.get_components_list()
    provider_key = f"{(agent.provider or 'unknown').lower()}/{(agent.model or 'unknown').lower()}"
    pricing = _PRICING_TABLE.get(provider_key, (None, None))

    section_a = {
        "agent_name": agent.name,
        "provider": agent.provider,
        "model": agent.model,
        "framework": agent.framework,
        "components": components,
        "connection_type": agent.connection_type,
        "version": agent.version,
        "pricing": {
            "input_per_1m_tokens": pricing[0],
            "output_per_1m_tokens": pricing[1],
        } if pricing[0] is not None else None,
    }

    # ── Section B: How the System Performed ───────────────────────────────────
    metric_totals = defaultdict(list)
    latencies = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    for case in cases:
        if case.latency_ms is not None:
            latencies.append(case.latency_ms)
        if case.input_tokens:
            total_input_tokens += case.input_tokens
        if case.output_tokens:
            total_output_tokens += case.output_tokens
        if case.estimated_cost:
            total_cost += case.estimated_cost

        if case.evaluation:
            for metric in ["correctness", "relevance", "faithfulness", "completeness",
                           "safety_score", "tool_accuracy", "trajectory_score",
                           "instruction_following"]:
                val = getattr(case.evaluation, metric, None)
                if val is not None:
                    metric_totals[metric].append(val)

    metric_averages = {
        metric: round(sum(values) / len(values), 4)
        for metric, values in metric_totals.items()
    }

    latency_percentiles = _compute_percentiles(latencies, [50, 95, 99])
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else None
    n_cases = len(cases) or 1

    section_b = {
        "overall_score": run.avg_score,
        "total_cases": run.total_cases,
        "passed_cases": run.passed_cases,
        "failed_cases": run.failed_cases,
        "metric_averages": metric_averages,
        "latency": {
            "avg_ms": avg_latency,
            "p50_ms": latency_percentiles.get("p50"),
            "p95_ms": latency_percentiles.get("p95"),
            "p99_ms": latency_percentiles.get("p99"),
        },
        "tokens": {
            "total_input": total_input_tokens,
            "total_output": total_output_tokens,
            "avg_per_case": round((total_input_tokens + total_output_tokens) / n_cases, 1),
        },
        "cost": {
            "total_estimated_usd": round(total_cost, 6),
            "avg_per_case_usd": round(total_cost / n_cases, 8),
        },
    }

    # ── Section C: Where Things Went Wrong ────────────────────────────────────
    try:
        section_c = failure_analyzer.analyze_failures(run_id, db)
    except ValueError:
        section_c = {"error": "Could not generate failure analysis"}

    # ── Section D: What They Could Use Instead ────────────────────────────────
    current_key = f"{(agent.provider or '').lower()}/{(agent.model or '').lower()}"
    alternatives = _MODEL_ALTERNATIVES.get(current_key, _DEFAULT_ALTERNATIVES)

    section_d = {
        "current": {
            "provider": agent.provider,
            "model": agent.model,
            "cost_per_1m_input": pricing[0] if pricing[0] is not None else "unknown",
        },
        "alternatives": alternatives,
    }

    log.info("analysis report generated", run_id=run_id, agent=agent.name)

    return {
        "run_id": run_id,
        "agent_id": agent.id,
        "suite_id": run.suite_id,
        "generated_at": run.completed_at.isoformat() if run.completed_at else None,
        "sections": {
            "what_they_used": section_a,
            "how_it_performed": section_b,
            "where_it_failed": section_c,
            "alternatives": section_d,
        },
    }
