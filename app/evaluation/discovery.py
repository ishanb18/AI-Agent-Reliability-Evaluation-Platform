"""
Evaluation Capability Discovery Engine — Day 6.

Probes the agent with a real test case from the suite and analyzes
what evaluation metrics are currently available, and what's missing.

Key function: discover_capabilities(agent, suite, db) -> DiscoveryResult

This powers the POST /evaluations/discover endpoint which gives users:
  1. A live probe of their agent's actual response format
  2. Per-metric availability status with clear reasons
  3. Exact step-by-step instructions to ENABLE unavailable metrics
  4. A list to select FROM for the next evaluation run
"""

import json
import time
import httpx
import structlog
from typing import Optional, Dict, List, Any, Tuple

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.test_suite import TestSuite, TestCase

log = structlog.get_logger()

PROBE_TIMEOUT = 15.0  # seconds


# ── What Each Metric Needs ────────────────────────────────────────────────────

# Maps metric name → human-readable requirements
METRIC_REQUIREMENTS = {
    "relevance": {
        "needs_from_agent": "A text answer field (e.g. 'answer', 'response', 'output', 'text')",
        "needs_from_suite": "Any test case input (always available)",
        "how_to_enable": "Your agent already returns text. This metric is always available.",
        "group": "quality",
    },
    "safety": {
        "needs_from_agent": "A text answer field",
        "needs_from_suite": "Any test case input (always available)",
        "how_to_enable": "Your agent already returns text. This metric is always available.",
        "group": "quality",
    },
    "latency": {
        "needs_from_agent": "Nothing — platform measures it automatically",
        "needs_from_suite": "Nothing",
        "how_to_enable": "Latency is always measured automatically. No changes needed.",
        "group": "performance",
    },
    "correctness": {
        "needs_from_agent": "A text answer field",
        "needs_from_suite": "Test cases must have 'expected_answer' filled in",
        "how_to_enable": (
            "Add 'expected_answer' to your test cases. Example:\n"
            "  POST /test-suites/{id}/cases\n"
            "  {\"input\": \"What is 2+2?\", \"expected_answer\": \"4\", \"category\": \"general\"}"
        ),
        "group": "quality",
    },
    "completeness": {
        "needs_from_agent": "A text answer field",
        "needs_from_suite": "Test cases must have 'expected_answer' filled in",
        "how_to_enable": (
            "Same as correctness — add 'expected_answer' to your test cases."
        ),
        "group": "quality",
    },
    "faithfulness": {
        "needs_from_agent": (
            "Retrieved context chunks in the response under key 'context', "
            "'retrieved_chunks', 'documents', 'sources', or 'references'"
        ),
        "needs_from_suite": "Any test case input",
        "how_to_enable": (
            "Add retrieved context chunks to your agent's response. Example:\n"
            "  {\n"
            "    \"answer\": \"Based on the policy...\",\n"
            "    \"context\": [\"Policy doc chunk 1...\", \"Policy doc chunk 2...\"]\n"
            "  }\n"
            "This enables hallucination detection — we check if your answer stays within the retrieved context."
        ),
        "group": "rag",
    },
    "tool_accuracy": {
        "needs_from_agent": (
            "Tool calls in the response under key 'tool_calls', 'tools', 'actions', "
            "'function_calls', or 'tool_trace'"
        ),
        "needs_from_suite": "Test cases must have 'expected_tools' filled in",
        "how_to_enable": (
            "Two steps required:\n"
            "1. Add tool_calls to your agent response:\n"
            "   {\"answer\": \"Order cancelled.\", \"tool_calls\": [\"get_order\", \"cancel_order\"]}\n"
            "2. Add expected_tools to your test cases:\n"
            "   {\"input\": \"Cancel order 123\", \"expected_tools\": [\"get_order\", \"cancel_order\"]}"
        ),
        "group": "tools",
    },
    "trajectory": {
        "needs_from_agent": "Tool calls in the response (ordered list)",
        "needs_from_suite": "Test cases must have 'expected_tools' filled in",
        "how_to_enable": (
            "Same as tool_accuracy — add ordered tool_calls to agent response "
            "and expected_tools to test cases."
        ),
        "group": "tools",
    },
    "instruction_following": {
        "needs_from_agent": "A text answer field",
        "needs_from_suite": "Nothing",
        "how_to_enable": (
            "Coming in a future update — requires configuring a system_prompt "
            "on the agent registration. Update the agent with:\n"
            "  PATCH /agents/{id}\n"
            "  {\"system_prompt\": \"You are a helpful customer support agent...\"}"
        ),
        "group": "quality",
    },
}


# ── Probe the Agent ───────────────────────────────────────────────────────────

def _probe_agent(endpoint: str, test_input: str) -> Tuple[Optional[Dict], float, str, Optional[str]]:
    """
    Send a real test case input to the agent and return the raw response.

    Returns:
        (raw_json, latency_ms, status, error_message)
    """
    try:
        start = time.time()
        response = httpx.post(
            endpoint,
            json={"input": test_input},
            timeout=PROBE_TIMEOUT,
        )
        latency_ms = round((time.time() - start) * 1000, 2)

        if response.status_code >= 400:
            return None, latency_ms, "failed", f"Agent returned HTTP {response.status_code}"

        try:
            return response.json(), latency_ms, "success", None
        except Exception:
            return {"answer": response.text}, latency_ms, "success", None

    except httpx.TimeoutException:
        return None, PROBE_TIMEOUT * 1000, "timeout", f"Agent did not respond within {PROBE_TIMEOUT}s"
    except httpx.ConnectError:
        return None, 0.0, "failed", f"Could not connect to {endpoint}"
    except Exception as e:
        return None, 0.0, "failed", f"Unexpected error: {str(e)}"


# ── Analyze Agent Response ────────────────────────────────────────────────────

def _detect_fields(raw_json: Optional[Dict]) -> Dict[str, str]:
    """
    Scan the agent's response JSON and detect which fields are present.

    Returns a dict like:
      {"answer": "response", "tools": "tool_calls", "context": "retrieved_chunks"}
    """
    if not raw_json or not isinstance(raw_json, dict):
        return {}

    detected = {}

    # Detect answer field
    answer_keys = ["answer", "response", "output", "text", "result", "message", "content", "reply"]
    for key in answer_keys:
        if key in raw_json and raw_json[key]:
            val = raw_json[key]
            if isinstance(val, dict):
                for sub in ["text", "content", "message", "output"]:
                    if sub in val:
                        detected["answer"] = f"{key}.{sub}"
                        break
                if "answer" not in detected:
                    detected["answer"] = key
            else:
                detected["answer"] = key
            break

    # Detect tool calls
    tool_keys = ["tool_calls", "tools", "actions", "function_calls", "tool_trace", "steps"]
    for key in tool_keys:
        if key in raw_json and raw_json[key]:
            detected["tools"] = key
            break

    # Detect context/RAG chunks
    context_keys = ["context", "retrieved_chunks", "documents", "sources", "references", "chunks"]
    for key in context_keys:
        if key in raw_json and raw_json[key]:
            detected["context"] = key
            break

    # Detect metadata/tokens
    meta_keys = ["metadata", "meta", "stats", "usage", "info"]
    for key in meta_keys:
        if key in raw_json and raw_json[key]:
            detected["metadata"] = key
            break

    return detected


def _check_suite_has_expected_answers(suite: TestSuite) -> bool:
    """Returns True if at least some test cases have expected_answer filled."""
    return any(tc.expected_answer for tc in suite.test_cases)


def _check_suite_has_expected_tools(suite: TestSuite) -> bool:
    """Returns True if at least some test cases have expected_tools filled."""
    return any(tc.get_expected_tools_list() for tc in suite.test_cases)


# ── Pick Best Probe Test Case ─────────────────────────────────────────────────

def _pick_probe_case(suite: TestSuite) -> Optional[TestCase]:
    """
    Pick the best test case to use as a probe.
    Preference: one that has expected_answer AND expected_tools (most informative).
    Fallback: any active test case.
    """
    active = [tc for tc in suite.test_cases if getattr(tc, "status", "active") == "active"]
    if not active:
        return None

    # Prefer case with both expected_answer and expected_tools
    for tc in active:
        if tc.expected_answer and tc.get_expected_tools_list():
            return tc

    # Prefer case with expected_answer
    for tc in active:
        if tc.expected_answer:
            return tc

    return active[0]


# ── Build Per-Metric Availability Report ─────────────────────────────────────

def _build_metric_report(
    detected_fields: Dict[str, str],
    suite: TestSuite,
    has_system_prompt: bool,
    probe_status: str,
) -> List[Dict]:
    """
    For each metric, determine if it's available and why.

    Returns list of MetricRequirement dicts.
    """
    has_answer = "answer" in detected_fields
    has_tools = "tools" in detected_fields
    has_context = "context" in detected_fields
    has_exp_answer = _check_suite_has_expected_answers(suite)
    has_exp_tools = _check_suite_has_expected_tools(suite)

    metrics = []

    # ── relevance ─────────────────────────────────────────────────────────────
    metrics.append({
        "metric": "relevance",
        "available": has_answer and probe_status == "success",
        "reason": (
            "Agent returns a text answer" if has_answer
            else "Agent did not return a detectable text answer field"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["relevance"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["relevance"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["relevance"]["how_to_enable"],
        "group": "quality",
    })

    # ── safety ────────────────────────────────────────────────────────────────
    metrics.append({
        "metric": "safety",
        "available": has_answer and probe_status == "success",
        "reason": (
            "Agent returns a text answer" if has_answer
            else "Agent did not return a detectable text answer field"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["safety"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["safety"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["safety"]["how_to_enable"],
        "group": "quality",
    })

    # ── latency ───────────────────────────────────────────────────────────────
    metrics.append({
        "metric": "latency",
        "available": probe_status in ("success", "timeout"),
        "reason": "Latency is always measured automatically by the platform",
        "agent_requirement": METRIC_REQUIREMENTS["latency"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["latency"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["latency"]["how_to_enable"],
        "group": "performance",
    })

    # ── correctness ───────────────────────────────────────────────────────────
    corr_available = has_answer and has_exp_answer
    corr_reason_parts = []
    if not has_answer:
        corr_reason_parts.append("agent doesn't return a text answer")
    if not has_exp_answer:
        corr_reason_parts.append("no test cases have 'expected_answer' filled in")
    metrics.append({
        "metric": "correctness",
        "available": corr_available,
        "reason": (
            "Agent returns text and test cases have expected answers" if corr_available
            else f"Blocked because: {' AND '.join(corr_reason_parts)}"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["correctness"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["correctness"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["correctness"]["how_to_enable"],
        "group": "quality",
    })

    # ── completeness ──────────────────────────────────────────────────────────
    metrics.append({
        "metric": "completeness",
        "available": corr_available,  # same requirements as correctness
        "reason": (
            "Agent returns text and test cases have expected answers" if corr_available
            else f"Blocked because: {' AND '.join(corr_reason_parts)}"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["completeness"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["completeness"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["completeness"]["how_to_enable"],
        "group": "quality",
    })

    # ── faithfulness ──────────────────────────────────────────────────────────
    metrics.append({
        "metric": "faithfulness",
        "available": has_context and has_answer,
        "reason": (
            f"Agent returns context chunks under '{detected_fields.get('context')}' key" if has_context
            else "Agent response does not contain retrieved context chunks"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["faithfulness"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["faithfulness"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["faithfulness"]["how_to_enable"],
        "group": "rag",
    })

    # ── tool_accuracy ─────────────────────────────────────────────────────────
    tool_avail = has_tools and has_exp_tools
    tool_reason_parts = []
    if not has_tools:
        tool_reason_parts.append("agent response doesn't contain tool_calls")
    if not has_exp_tools:
        tool_reason_parts.append("no test cases have 'expected_tools' defined")
    metrics.append({
        "metric": "tool_accuracy",
        "available": tool_avail,
        "reason": (
            f"Agent returns tools under '{detected_fields.get('tools')}' and test cases have expected_tools" if tool_avail
            else f"Blocked because: {' AND '.join(tool_reason_parts)}"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["tool_accuracy"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["tool_accuracy"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["tool_accuracy"]["how_to_enable"],
        "group": "tools",
    })

    # ── trajectory ────────────────────────────────────────────────────────────
    metrics.append({
        "metric": "trajectory",
        "available": tool_avail,
        "reason": (
            "Agent returns ordered tool calls and test cases have expected_tools" if tool_avail
            else f"Blocked because: {' AND '.join(tool_reason_parts)}"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["trajectory"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["trajectory"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["trajectory"]["how_to_enable"],
        "group": "tools",
    })

    # ── instruction_following ─────────────────────────────────────────────────
    metrics.append({
        "metric": "instruction_following",
        "available": has_answer and has_system_prompt,
        "reason": (
            "Agent returns text and has a system_prompt configured" if (has_answer and has_system_prompt)
            else "Agent does not have a system_prompt configured"
        ),
        "agent_requirement": METRIC_REQUIREMENTS["instruction_following"]["needs_from_agent"],
        "test_case_requirement": METRIC_REQUIREMENTS["instruction_following"]["needs_from_suite"],
        "how_to_enable": METRIC_REQUIREMENTS["instruction_following"]["how_to_enable"],
        "group": "quality",
    })

    return metrics


# ── Main Entry Point ──────────────────────────────────────────────────────────

def discover_capabilities(
    agent: Agent,
    suite: TestSuite,
    db: Session,
) -> Dict:
    """
    Probe an agent with a real test case from the suite and analyze
    what evaluation metrics are currently available.

    Args:
        agent:  The agent to probe
        suite:  The test suite to use for context (picks a representative case)
        db:     Database session

    Returns:
        Discovery result dict with:
          - probe_status: "success" | "failed" | "timeout"
          - detected_fields: what was found in the agent response
          - metrics: per-metric availability with reasons + how_to_enable
          - available_metrics: quick list
          - unavailable_metrics: quick list
          - sample_response: the actual agent response (for debugging)
    """
    # Pick a representative probe case
    probe_case = _pick_probe_case(suite)
    probe_input = probe_case.input if probe_case else "Hello, this is a connection test."

    log.info(
        "starting capability discovery",
        agent_id=agent.id,
        suite_id=suite.id,
        probe_input=probe_input[:80],
    )

    # Probe the agent
    raw_json, latency_ms, probe_status, probe_error = _probe_agent(
        agent.endpoint, probe_input
    )

    # Detect response fields
    detected_fields = _detect_fields(raw_json)

    # Check if agent has system_prompt
    has_system_prompt = bool(getattr(agent, "system_prompt", None))

    # Build metric availability report
    metric_report = _build_metric_report(
        detected_fields=detected_fields,
        suite=suite,
        has_system_prompt=has_system_prompt,
        probe_status=probe_status,
    )

    available = [m["metric"] for m in metric_report if m["available"]]
    unavailable = [m["metric"] for m in metric_report if not m["available"]]

    log.info(
        "discovery complete",
        agent_id=agent.id,
        probe_status=probe_status,
        available_count=len(available),
        unavailable_count=len(unavailable),
    )

    # Truncate sample response to avoid huge payloads
    sample_response = raw_json
    if sample_response and len(json.dumps(sample_response)) > 2000:
        sample_response = {"_truncated": True, "preview": str(raw_json)[:500]}

    return {
        "agent_id": agent.id,
        "suite_id": suite.id,
        "agent_name": agent.name,
        "suite_name": suite.name,
        "probe_status": probe_status,
        "probe_error": probe_error,
        "probe_latency_ms": latency_ms,
        "probe_input_used": probe_input,
        "detected_fields": detected_fields,
        "metrics": metric_report,
        "available_metrics": available,
        "unavailable_metrics": unavailable,
        "sample_agent_response": sample_response,
        "next_steps": (
            f"You have {len(available)} metrics available. "
            f"Call POST /evaluations/run with 'selected_metrics': {available} "
            f"to run only your chosen metrics."
        ),
    }
