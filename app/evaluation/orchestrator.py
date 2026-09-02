"""
Evaluation Orchestrator — coordinates the complete evaluation pipeline.

This is the central "brain" of Day 4. It wires together:
  1. Agent invocation        → POST test input to the agent's endpoint
  2. Response parsing        → extract answer, tools, context from raw JSON
  3. Deterministic scoring   → tool accuracy, trajectory, cost, latency (instant, free)
  4. LLM-as-a-Judge scoring  → correctness, relevance, faithfulness, safety, etc.
  5. DB persistence          → store EvalRunCase + Evaluation per test case
  6. Run aggregation         → update EvalRun with avg_score and pass/fail counts

Key design decisions:
  - Graceful degradation: if the agent only returns plain text, we still evaluate
    what we can (relevance, safety, latency) and skip the rest.
  - Metric skipping is tracked: every skipped metric records WHY it was skipped
    (stored in evaluations.metrics_skipped) for full auditability.
  - Per-case isolation: if one test case fails (timeout, parse error), we log
    the error and continue with the remaining cases. One bad case never kills the run.
  - The run is SYNCHRONOUS for the MVP — we process all cases in a single request.
    This is acceptable for small suites (35 seed cases). Long suites can be moved
    to background workers (Celery/Redis) in a future day.
"""

import json
import time
import datetime
import structlog
import httpx
from typing import Optional, Dict, Any, List, Tuple

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.test_suite import TestSuite, TestCase
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation
from app.evaluation import llm_judge, deterministic

log = structlog.get_logger()

# How long to wait for the agent to respond before timing out (seconds)
AGENT_TIMEOUT_SECONDS = 30.0


# ── Step 1: Invoke the Agent ──────────────────────────────────────────────────

def _invoke_agent(endpoint: str, test_input: str) -> Tuple[Optional[Dict], float, str, Optional[str]]:
    """
    Send a test case input to the agent's REST endpoint and return what came back.

    Handles three outcomes:
      - Success:  agent responded with JSON or plain text → return parsed data
      - HTTP Error: agent returned 4xx/5xx → return error status
      - Timeout:  agent didn't respond in 30s → return timeout status

    Args:
        endpoint:    The agent's URL (e.g. "https://myagent.com/chat")
        test_input:  The question/prompt from the test case

    Returns:
        Tuple of:
          - raw_json (dict or None): parsed JSON response, None if plain text or error
          - latency_ms (float): how long the call took
          - status (str): "success", "error", or "timeout"
          - error_message (str or None): description of what went wrong, if anything
    """
    payload = {"input": test_input}

    try:
        start = time.time()
        response = httpx.post(endpoint, json=payload, timeout=AGENT_TIMEOUT_SECONDS)
        latency_ms = round((time.time() - start) * 1000, 2)

        if response.status_code >= 400:
            return None, latency_ms, "error", f"Agent returned HTTP {response.status_code}"

        # Try to parse as JSON; fall back to treating body as plain text
        try:
            raw_json = response.json()
        except Exception:
            # Plain text response — wrap it so downstream parsing works uniformly
            raw_json = {"answer": response.text}

        return raw_json, latency_ms, "success", None

    except httpx.TimeoutException:
        return None, AGENT_TIMEOUT_SECONDS * 1000, "timeout", "Agent did not respond within 30 seconds"
    except httpx.ConnectError:
        return None, 0.0, "error", f"Could not connect to {endpoint}. Is the agent running?"
    except Exception as e:
        return None, 0.0, "error", f"Unexpected error invoking agent: {str(e)}"


# ── Step 2: Parse the Response ────────────────────────────────────────────────

def _parse_response(
    raw_json: Optional[Dict],
    response_mapping: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Extract structured data from the agent's raw JSON response.

    Uses a 4-level fallback chain to always get *something* from the response:

    Level 1: Use response_mapping if available
             e.g. response_mapping = {"answer": "response", "tools": "tool_calls"}
             → data["response"] gives the answer text

    Level 2: Auto-detect using common key names
             Scans for known keys: ["answer", "response", "output", "text", ...]

    Level 3: Nested key handling
             If value is a dict like {"result": {"text": "..."}} → extract sub-key

    Level 4: Fallback to entire JSON as answer
             If nothing matched, stringify the whole JSON as the "answer"

    Returns dict with keys: "answer", "tools", "context", "metadata", "input_tokens",
    "output_tokens", "step_count"  (all optional, None if not found)
    """
    if raw_json is None:
        return {"answer": None, "tools": None, "context": None, "metadata": None,
                "input_tokens": None, "output_tokens": None, "step_count": None}

    parsed: Dict[str, Any] = {
        "answer": None,
        "tools": None,
        "context": None,
        "metadata": None,
        "input_tokens": None,
        "output_tokens": None,
        "step_count": None,
    }

    # ── Level 1: Use response_mapping if provided ──────────────────────────
    if response_mapping:
        if "answer" in response_mapping:
            key = response_mapping["answer"]
            val = raw_json.get(key)
            # Handle nested: {"result": {"text": "..."}} → key = "result.text"
            if "." in key:
                parts = key.split(".", 1)
                outer = raw_json.get(parts[0], {})
                if isinstance(outer, dict):
                    val = outer.get(parts[1])
            parsed["answer"] = str(val) if val is not None else None

        if "tools" in response_mapping:
            parsed["tools"] = raw_json.get(response_mapping["tools"])

        if "context" in response_mapping:
            parsed["context"] = raw_json.get(response_mapping["context"])

        if "metadata" in response_mapping:
            parsed["metadata"] = raw_json.get(response_mapping["metadata"])

    # ── Level 2: Auto-detect answer if not found via mapping ───────────────
    if parsed["answer"] is None:
        answer_keys = ["answer", "response", "output", "text", "result", "message", "content", "reply"]
        for key in answer_keys:
            if key in raw_json:
                val = raw_json[key]
                # Level 3: handle nested value
                if isinstance(val, dict):
                    for sub_key in ["text", "content", "message", "output"]:
                        if sub_key in val:
                            parsed["answer"] = str(val[sub_key])
                            break
                    if parsed["answer"] is None:
                        parsed["answer"] = json.dumps(val)  # stringify nested dict
                else:
                    parsed["answer"] = str(val)
                break

    # ── Level 4: Final fallback — use entire JSON as answer ────────────────
    if parsed["answer"] is None:
        parsed["answer"] = json.dumps(raw_json)

    # ── Auto-detect tools if not found via mapping ─────────────────────────
    if parsed["tools"] is None:
        tool_keys = ["tool_calls", "tools", "actions", "function_calls", "tool_trace", "steps"]
        for key in tool_keys:
            if key in raw_json:
                parsed["tools"] = raw_json[key]
                break

    # ── Auto-detect context if not found via mapping ───────────────────────
    if parsed["context"] is None:
        context_keys = ["context", "retrieved_chunks", "documents", "sources", "references", "chunks"]
        for key in context_keys:
            if key in raw_json:
                parsed["context"] = raw_json[key]
                break

    # ── Extract token counts from metadata ────────────────────────────────
    meta = parsed.get("metadata") or {}
    if isinstance(meta, dict):
        parsed["input_tokens"] = meta.get("input_tokens") or meta.get("prompt_tokens")
        parsed["output_tokens"] = meta.get("output_tokens") or meta.get("completion_tokens")
        parsed["step_count"] = meta.get("steps") or meta.get("step_count")

    return parsed


def _extract_tool_names(tools_data: Any) -> List[str]:
    """
    Extract a flat list of tool names from whatever format the agent returned.

    Agents return tools in many formats:
      ["get_order", "cancel_order"]                         → already a list of strings
      [{"name": "get_order"}, {"name": "cancel_order"}]    → list of dicts with "name" key
      [{"tool": "get_order"}, {"tool": "cancel_order"}]    → list of dicts with "tool" key
      "get_order"                                           → single string

    We normalize all of these to: ["get_order", "cancel_order"]
    """
    if tools_data is None:
        return []

    if isinstance(tools_data, str):
        return [tools_data]

    if isinstance(tools_data, list):
        names = []
        for item in tools_data:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                # Try common key names for the tool name
                name = item.get("name") or item.get("tool") or item.get("function") or item.get("action")
                if name:
                    names.append(str(name))
        return names

    return []


def _extract_context_strings(context_data: Any) -> List[str]:
    """
    Extract a flat list of context chunk strings from whatever format the agent returned.

    Agents return context in many formats:
      ["chunk1 text", "chunk2 text"]                  → already a list of strings
      [{"content": "chunk1"}, {"content": "chunk2"}]  → list of dicts
      [{"text": "chunk1", "score": 0.9}]              → list of dicts with extra fields
      "single chunk text"                              → single string
    """
    if context_data is None:
        return []

    if isinstance(context_data, str):
        return [context_data]

    if isinstance(context_data, list):
        chunks = []
        for item in context_data:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                # Try common content field names
                text = item.get("content") or item.get("text") or item.get("chunk") or item.get("passage")
                if text:
                    chunks.append(str(text))
                else:
                    # Last resort: stringify the whole dict
                    chunks.append(json.dumps(item))
        return chunks

    return []


# ── Step 3 + 4: Score One Test Case ──────────────────────────────────────────

def _score_case(
    test_case: TestCase,
    agent: Agent,
    parsed_data: Dict[str, Any],
    latency_ms: float,
    gateway,
    judge_provider: str,
    judge_model: Optional[str],
) -> tuple:
    """
    Run all applicable evaluators on one test case and return the scores.

    Decides which metrics to run based on what data is available:
      - Always: relevance, safety, latency_score, cost
      - If expected_answer exists: correctness, completeness
      - If context_chunks exist: faithfulness
      - If tools data exists: tool_accuracy, trajectory
      - If agent has system_prompt: instruction_following

    Skipped metrics are recorded with the reason why they were skipped.

    Returns:
        (scores dict, reasoning dict, metrics_evaluated list, metrics_skipped dict)
    """
    scores: Dict[str, Optional[float]] = {}
    reasoning: Dict[str, str] = {}
    metrics_evaluated: List[str] = []
    metrics_skipped: Dict[str, str] = {}

    answer = parsed_data.get("answer")
    expected_answer = test_case.expected_answer
    expected_tools = test_case.get_expected_tools_list()
    actual_tools = _extract_tool_names(parsed_data.get("tools"))
    context_chunks = _extract_context_strings(parsed_data.get("context"))
    question = test_case.input
    system_prompt = getattr(agent, "system_prompt", None)  # future field

    agent_provider = agent.provider or "unknown"
    agent_model = agent.model or "unknown"
    input_tokens = parsed_data.get("input_tokens")
    output_tokens = parsed_data.get("output_tokens")

    # ── Deterministic: Latency Score ─────────────────────────────────────────
    scores["latency_score"] = deterministic.score_latency(latency_ms)
    reasoning["latency_score"] = f"Response took {latency_ms:.0f}ms"
    metrics_evaluated.append("latency_score")

    # ── Deterministic: Cost Estimation ───────────────────────────────────────
    scores["estimated_cost"] = deterministic.calculate_cost(
        agent_provider, agent_model, input_tokens, output_tokens
    )
    metrics_evaluated.append("estimated_cost")

    # ── LLM Judge: Relevance (always runs) ───────────────────────────────────
    if answer:
        score, reason = llm_judge.evaluate_relevance(question, answer, gateway, judge_provider, judge_model)
        scores["relevance"] = score
        reasoning["relevance"] = reason
        if score is not None:
            metrics_evaluated.append("relevance")
        else:
            metrics_skipped["relevance"] = reason
    else:
        metrics_skipped["relevance"] = "agent returned no answer text"

    # ── LLM Judge: Safety (always runs — uses category-aware rubric) ─────────
    if answer:
        score, reason = llm_judge.evaluate_safety(
            question, answer, gateway, judge_provider, judge_model,
            category=test_case.category,
        )
        scores["safety_score"] = score
        reasoning["safety_score"] = reason
        if score is not None:
            metrics_evaluated.append("safety_score")
        else:
            metrics_skipped["safety_score"] = reason
    else:
        metrics_skipped["safety_score"] = "agent returned no answer text"

    # ── LLM Judge: Correctness (needs expected_answer, uses category rubric) ──
    if answer and expected_answer:
        score, reason = llm_judge.evaluate_correctness(
            question, answer, expected_answer, gateway, judge_provider, judge_model,
            category=test_case.category,
        )
        scores["correctness"] = score
        reasoning["correctness"] = reason
        if score is not None:
            metrics_evaluated.append("correctness")
        else:
            metrics_skipped["correctness"] = reason
    else:
        metrics_skipped["correctness"] = "no expected_answer in test case"

    # ── LLM Judge: Completeness (needs expected_answer, uses category rubric) ─
    if answer and expected_answer:
        score, reason = llm_judge.evaluate_completeness(
            question, answer, expected_answer, gateway, judge_provider, judge_model,
            category=test_case.category,
        )
        scores["completeness"] = score
        reasoning["completeness"] = reason
        if score is not None:
            metrics_evaluated.append("completeness")
        else:
            metrics_skipped["completeness"] = reason
    else:
        metrics_skipped["completeness"] = "no expected_answer in test case"

    # ── LLM Judge: Faithfulness (needs context chunks from RAG) ───────────────
    if answer and context_chunks:
        score, reason = llm_judge.evaluate_faithfulness(
            question, answer, context_chunks, gateway, judge_provider, judge_model
        )
        scores["faithfulness"] = score
        reasoning["faithfulness"] = reason
        if score is not None:
            metrics_evaluated.append("faithfulness")
        else:
            metrics_skipped["faithfulness"] = reason
    else:
        metrics_skipped["faithfulness"] = "no context chunks in agent response (not a RAG agent or RAG context not returned)"

    # ── Deterministic: Tool Accuracy (needs tools from both sides) ────────────
    if actual_tools and expected_tools:
        scores["tool_accuracy"] = deterministic.evaluate_tool_accuracy(actual_tools, expected_tools)
        reasoning["tool_accuracy"] = (
            f"Agent called: {actual_tools}. Expected: {expected_tools}. "
            f"Score: {scores['tool_accuracy']}"
        )
        metrics_evaluated.append("tool_accuracy")
    elif actual_tools and not expected_tools:
        metrics_skipped["tool_accuracy"] = "agent used tools but test case has no expected_tools defined"
    elif not actual_tools and expected_tools:
        # Agent called NO tools but we expected some → that's a failure (score 0)
        scores["tool_accuracy"] = 0.0
        reasoning["tool_accuracy"] = f"Agent called no tools. Expected: {expected_tools}."
        metrics_evaluated.append("tool_accuracy")
    else:
        metrics_skipped["tool_accuracy"] = "no tools in agent response and no expected_tools in test case"

    # ── Deterministic: Trajectory (needs tools from both sides) ───────────────
    if actual_tools and expected_tools:
        scores["trajectory_score"] = deterministic.evaluate_trajectory(actual_tools, expected_tools)
        reasoning["trajectory_score"] = (
            f"Tool sequence: {actual_tools}. Expected order: {expected_tools}. "
            f"Score: {scores['trajectory_score']}"
        )
        metrics_evaluated.append("trajectory_score")
    elif not actual_tools and expected_tools:
        scores["trajectory_score"] = 0.0
        reasoning["trajectory_score"] = f"Agent called no tools. Expected sequence: {expected_tools}."
        metrics_evaluated.append("trajectory_score")
    else:
        metrics_skipped["trajectory_score"] = "tool data unavailable for sequence comparison"

    # ── LLM Judge: Instruction Following (needs system_prompt) ───────────────
    if answer and system_prompt:
        score, reason = llm_judge.evaluate_instruction_following(
            question, answer, system_prompt, gateway, judge_provider, judge_model
        )
        scores["instruction_following"] = score
        reasoning["instruction_following"] = reason
        if score is not None:
            metrics_evaluated.append("instruction_following")
        else:
            metrics_skipped["instruction_following"] = reason
    else:
        metrics_skipped["instruction_following"] = "agent has no system_prompt configured"

    return scores, reasoning, metrics_evaluated, metrics_skipped


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_evaluation(
    agent_id: int,
    suite_id: int,
    db: Session,
    gateway,
    judge_provider: str = "gemini",
    judge_model: Optional[str] = None,
) -> EvalRun:
    """
    The main evaluation function — runs a complete test suite against an agent.

    Flow:
      1. Load agent + test suite + test cases from DB
      2. Validate: agent must have endpoint, suite must have cases
      3. Create EvalRun record with status="running"
      4. For each test case:
           a. Invoke agent endpoint with test input
           b. Parse the JSON response using response_mapping
           c. Run deterministic evaluators (latency, cost, tool accuracy, trajectory)
           d. Run LLM-as-a-Judge (relevance, safety, correctness, faithfulness, etc.)
           e. Store EvalRunCase + Evaluation in DB
      5. Aggregate scores → update EvalRun with avg_score, passed/failed counts, status="completed"
      6. Return the completed EvalRun

    Args:
        agent_id:       ID of the agent to evaluate (must be REST API type with endpoint)
        suite_id:       ID of the test suite to run
        db:             SQLAlchemy database session
        gateway:        ModelGateway instance (from app.providers)
        judge_provider: Which LLM to use as judge ("gemini", "groq", "ollama")
        judge_model:    Specific model string (None = use provider default)

    Returns:
        Completed EvalRun ORM object (with all cases and evaluations committed to DB)

    Raises:
        ValueError: if agent or suite not found, or agent has no endpoint
    """
    # ── Load and validate ─────────────────────────────────────────────────────
    agent: Optional[Agent] = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise ValueError(f"Agent with id={agent_id} not found")

    if agent.connection_type != "rest_api" or not agent.endpoint:
        raise ValueError(
            f"Agent '{agent.name}' must be a REST API agent with an endpoint to run evaluations"
        )

    suite: Optional[TestSuite] = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise ValueError(f"Test suite with id={suite_id} not found")

    test_cases = suite.test_cases
    if not test_cases:
        raise ValueError(f"Test suite '{suite.name}' has no test cases")

    # Day 5: filter to only active test cases (exclude pending_review and rejected)
    active_cases = [tc for tc in test_cases if getattr(tc, 'status', 'active') == 'active']
    if not active_cases:
        raise ValueError(
            f"Test suite '{suite.name}' has {len(test_cases)} test cases but none are active "
            f"(they may all be pending review or rejected)"
        )

    # ── Deserialize agent's response_mapping ──────────────────────────────────
    response_mapping: Optional[Dict[str, str]] = agent.get_response_mapping()

    log.info(
        "evaluation started",
        agent_id=agent_id,
        agent_name=agent.name,
        suite_id=suite_id,
        suite_name=suite.name,
        total_cases=len(active_cases),
        skipped_inactive=len(test_cases) - len(active_cases),
        judge_provider=judge_provider,
    )

    # ── Create EvalRun record ─────────────────────────────────────────────────
    eval_run = EvalRun(
        agent_id=agent_id,
        suite_id=suite_id,
        status="running",
        total_cases=len(active_cases),
        judge_provider=judge_provider,
        judge_model=judge_model,
        started_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(eval_run)
    db.commit()
    db.refresh(eval_run)

    log.info("eval_run created", run_id=eval_run.id)

    # ── Process each test case ────────────────────────────────────────────────
    all_scores: List[float] = []
    passed = 0
    failed = 0

    for idx, test_case in enumerate(active_cases):
        log.info(
            "processing test case",
            run_id=eval_run.id,
            case_idx=idx + 1,
            total=len(active_cases),
            test_case_id=test_case.id,
        )

        # ── Step 1: Invoke the agent ──────────────────────────────────────────
        raw_json, latency_ms, invoke_status, invoke_error = _invoke_agent(
            agent.endpoint, test_case.input
        )

        # ── Step 2: Parse the response ────────────────────────────────────────
        parsed_data = _parse_response(raw_json, response_mapping)

        # Extract structured lists
        actual_tools = _extract_tool_names(parsed_data.get("tools"))
        context_chunks = _extract_context_strings(parsed_data.get("context"))

        # ── Create EvalRunCase ────────────────────────────────────────────────
        run_case = EvalRunCase(
            run_id=eval_run.id,
            test_case_id=test_case.id,
            agent_output=parsed_data.get("answer"),
            agent_raw_response=json.dumps(raw_json) if raw_json else None,
            latency_ms=latency_ms,
            input_tokens=parsed_data.get("input_tokens"),
            output_tokens=parsed_data.get("output_tokens"),
            estimated_cost=deterministic.calculate_cost(
                agent.provider or "unknown",
                agent.model or "unknown",
                parsed_data.get("input_tokens"),
                parsed_data.get("output_tokens"),
            ),
            step_count=parsed_data.get("step_count") or (len(actual_tools) if actual_tools else None),
            status=invoke_status,
            error=invoke_error,
        )
        if actual_tools:
            run_case.set_tool_trace_list(actual_tools)
        if context_chunks:
            run_case.set_context_chunks_list(context_chunks)

        db.add(run_case)
        db.flush()  # get run_case.id without committing yet

        # ── Step 3 + 4: Score the case (skip if invocation failed) ───────────
        if invoke_status == "success":
            try:
                scores, reasoning, metrics_evaluated, metrics_skipped = _score_case(
                    test_case=test_case,
                    agent=agent,
                    parsed_data=parsed_data,
                    latency_ms=latency_ms,
                    gateway=gateway,
                    judge_provider=judge_provider,
                    judge_model=judge_model,
                )

                # Create Evaluation record
                evaluation = Evaluation(
                    run_case_id=run_case.id,
                    correctness=scores.get("correctness"),
                    relevance=scores.get("relevance"),
                    faithfulness=scores.get("faithfulness"),
                    completeness=scores.get("completeness"),
                    instruction_following=scores.get("instruction_following"),
                    safety_score=scores.get("safety_score"),
                    tool_accuracy=scores.get("tool_accuracy"),
                    trajectory_score=scores.get("trajectory_score"),
                    judge_provider=judge_provider,
                    judge_model=judge_model,
                )
                evaluation.set_reasoning_dict(reasoning)
                evaluation.set_metrics_evaluated_list(metrics_evaluated)
                evaluation.set_metrics_skipped_dict(metrics_skipped)
                db.add(evaluation)

                # Track overall score for aggregation
                overall = evaluation.compute_overall_score()
                if overall is not None:
                    all_scores.append(overall)
                    if overall >= 0.5:
                        passed += 1
                    else:
                        failed += 1

                log.info(
                    "test case scored",
                    run_id=eval_run.id,
                    test_case_id=test_case.id,
                    overall_score=overall,
                    metrics_evaluated=metrics_evaluated,
                    metrics_skipped=list(metrics_skipped.keys()),
                )

            except Exception as e:
                # Scoring failed — don't crash the whole run, just log and continue
                log.error(
                    "scoring failed for test case",
                    run_id=eval_run.id,
                    test_case_id=test_case.id,
                    error=str(e),
                )
                failed += 1
        else:
            # Invocation failed (timeout or HTTP error)
            log.warning(
                "test case invocation failed, skipping evaluation",
                run_id=eval_run.id,
                test_case_id=test_case.id,
                status=invoke_status,
                error=invoke_error,
            )
            failed += 1

        db.commit()  # commit after each case — partial results are saved if run crashes

    # ── Aggregate and complete the run ────────────────────────────────────────
    avg_score = round(sum(all_scores) / len(all_scores), 4) if all_scores else None

    eval_run.status = "completed"
    eval_run.passed_cases = passed
    eval_run.failed_cases = failed
    eval_run.avg_score = avg_score
    eval_run.completed_at = datetime.datetime.now(datetime.timezone.utc)

    db.commit()
    db.refresh(eval_run)

    log.info(
        "evaluation completed",
        run_id=eval_run.id,
        total_cases=len(test_cases),
        passed=passed,
        failed=failed,
        avg_score=avg_score,
    )

    return eval_run
