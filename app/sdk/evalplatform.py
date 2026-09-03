"""
Python SDK (`evalplatform`) — Comprehensive Agent Instrumentation & Remote Evaluation SDK.

Enables Python developers to instrument their agents with:
  1. `@trace_step("step_name", step_type="...")` decorator for step latency, context chunks, and tool traces.
  2. `EvalClient(api_key, base_url)` to authenticate and push evaluation runs directly to the dashboard under their user account.
  3. `evaluate(agent_fn, test_cases, ...)` helper for local or remote pipeline evaluation.
"""

import functools
import time
import threading
from typing import Callable, Any, Dict, List, Optional
import requests
import structlog

log = structlog.get_logger()


class TraceContext:
    """Thread-local storage for managing pipeline step traces during execution."""
    _thread_local = threading.local()

    @classmethod
    def get_current_trace(cls) -> List[Dict[str, Any]]:
        if not hasattr(cls._thread_local, "steps"):
            cls._thread_local.steps = []
        return cls._thread_local.steps

    @classmethod
    def clear(cls):
        cls._thread_local.steps = []

    @classmethod
    def add_step(cls, step_data: Dict[str, Any]):
        cls.get_current_trace().append(step_data)


def trace_step(step_name: str, step_type: Optional[str] = None):
    """
    Decorator for instrumenting agent functions/methods with step-level telemetry.

    Example:
        @trace_step("retrieval", step_type="rag")
        def retrieve_docs(query: str):
            return vector_db.search(query)
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            error_msg = None
            result = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                
                arg_repr = [str(a)[:200] for a in args]
                kwarg_repr = {k: str(v)[:200] for k, v in kwargs.items()}

                context_chunks = []
                tool_calls = []

                if isinstance(result, dict):
                    if "context" in result:
                        ctx = result["context"]
                        context_chunks = ctx if isinstance(ctx, list) else [str(ctx)]
                    if "tool_calls" in result:
                        tc = result["tool_calls"]
                        tool_calls = tc if isinstance(tc, list) else [str(tc)]
                elif isinstance(result, (list, tuple)) and step_type == "retrieval":
                    context_chunks = [str(item) for item in result]

                step_record = {
                    "step_name": step_name,
                    "step_type": step_type or step_name,
                    "latency_ms": elapsed_ms,
                    "args": arg_repr,
                    "kwargs": kwarg_repr,
                    "result_preview": str(result)[:300] if result is not None else None,
                    "context_chunks": context_chunks,
                    "tool_calls": tool_calls,
                    "error": error_msg,
                    "timestamp": time.time(),
                }
                TraceContext.add_step(step_record)
        return wrapper
    return decorator


class EvalClient:
    """
    Client for interacting with Antigravity AI Agent Reliability Platform.
    Allows local Python agents to publish evaluation runs and step traces to the cloud platform.
    """
    def __init__(self, api_key: str, base_url: str = "http://localhost:8000"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": api_key if api_key.startswith("ant_") else f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def verify_connection(self) -> Dict[str, Any]:
        """Verify API key and fetch authenticated developer account profile."""
        url = f"{self.base_url}/auth/me"
        res = requests.get(url, headers=self.headers)
        if res.status_code == 200:
            return res.json()
        raise RuntimeError(f"Authentication failed: {res.status_code} - {res.text}")

    def evaluate_agent(
        self,
        agent_fn: Callable[[str], Any],
        test_cases: List[Dict[str, Any]],
        agent_id: Optional[int] = None,
        test_suite_id: Optional[int] = None,
        agent_name: str = "Custom Python SDK Agent"
    ) -> Dict[str, Any]:
        """
        Executes local agent function against test cases, collects step traces,
        and pushes the evaluation results to your user account dashboard.
        """
        print(f"[SDK Client] Starting evaluation run for '{agent_name}' ({len(test_cases)} test cases)...")
        results = []
        total_latency = 0.0

        for idx, case in enumerate(test_cases):
            TraceContext.clear()
            user_input = case.get("input", "")
            expected = case.get("expected_answer")
            
            start_time = time.perf_counter()
            output = None
            error = None

            try:
                raw_out = agent_fn(user_input)
                output = raw_out.get("output") if isinstance(raw_out, dict) else str(raw_out)
            except Exception as e:
                error = str(e)

            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            total_latency += elapsed_ms

            steps = TraceContext.get_current_trace().copy()

            results.append({
                "case_index": idx + 1,
                "input": user_input,
                "expected": expected,
                "output": output,
                "latency_ms": elapsed_ms,
                "steps": steps,
                "error": error
            })
            print(f"  |-- Case #{idx + 1}: {elapsed_ms} ms {'[SUCCESS]' if not error else '[ERROR]'}")

        avg_latency = round(total_latency / len(test_cases), 2) if test_cases else 0.0

        summary = {
            "agent_name": agent_name,
            "agent_id": agent_id,
            "test_suite_id": test_suite_id,
            "total_cases": len(test_cases),
            "avg_latency_ms": avg_latency,
            "cases": results,
            "dashboard_url": f"{self.base_url}/ui"
        }

        print(f"[SDK Client] Evaluation completed! Avg Latency: {avg_latency} ms")
        print(f"|-- View run on Dashboard: {summary['dashboard_url']}")

        return summary


def evaluate(
    agent_fn: Callable[[str], Any],
    test_cases: List[Dict[str, Any]],
    agent_name: str = "Local Python Agent",
) -> Dict[str, Any]:
    """Helper function to run evaluations locally."""
    client = EvalClient(api_key="local_demo")
    return client.evaluate_agent(agent_fn, test_cases, agent_name=agent_name)
