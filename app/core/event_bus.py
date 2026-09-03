"""
In-Memory Event Bus — Day 7.

Powers real-time evaluation progress streaming via Server-Sent Events (SSE).

Problem:
  POST /evaluations/run used to be synchronous — it blocked for the entire
  evaluation duration (up to 5 minutes for 35 cases × 3 LLM calls each).
  Users got nothing until the whole run finished.

Solution:
  1. POST /evaluations/run queues a background task and returns immediately
     with { run_id, status: "queued" }
  2. The background task runs the evaluation and emits events here as each
     test case completes
  3. GET /evaluations/{id}/stream subscribes to this bus and forwards events
     to the client as Server-Sent Events (SSE)

Architecture:
  - One asyncio.Queue per active run_id
  - Events are dicts serialized to JSON strings
  - A sentinel value (None) signals the stream is complete
  - Queues are automatically cleaned up when the run completes

Thread safety:
  asyncio.Queue is safe for single-threaded async code.
  For multi-worker production deployments, replace with Redis Pub/Sub.
"""

import asyncio
import json
from typing import Dict, AsyncIterator, Optional

import structlog

log = structlog.get_logger()

# ── Event Queue Registry ──────────────────────────────────────────────────────
# Maps run_id → asyncio.Queue
# Each queue holds JSON-serializable event dicts + None as terminal sentinel
_queues: Dict[int, asyncio.Queue] = {}


def create_queue(run_id: int) -> asyncio.Queue:
    """
    Create a new event queue for a run.
    Called when POST /evaluations/run starts a background task.
    """
    q: asyncio.Queue = asyncio.Queue()
    _queues[run_id] = q
    log.info("event queue created", run_id=run_id)
    return q


def get_queue(run_id: int) -> Optional[asyncio.Queue]:
    """Get existing queue for a run (None if run not active)."""
    return _queues.get(run_id)


def cleanup_queue(run_id: int):
    """Remove queue after run completes. Called by the background task."""
    if run_id in _queues:
        del _queues[run_id]
        log.info("event queue cleaned up", run_id=run_id)


async def emit(run_id: int, event: dict):
    """
    Emit an event for a run.
    Called by the evaluation orchestrator as each case completes.

    Event shapes:
      Case started:   {"event": "case_started", "case": 1, "total": 35, "input": "..."}
      Case complete:  {"event": "case_done", "case": 1, "total": 35, "score": 0.88,
                       "status": "success", "metrics": {"correctness": 0.9, ...},
                       "latency_ms": 421.0}
      Run complete:   {"event": "run_complete", "run_id": 12, "avg_score": 0.79,
                       "passed": 28, "failed": 7, "total": 35}
      Error:          {"event": "error", "message": "Agent unreachable"}
    """
    q = _queues.get(run_id)
    if q:
        await q.put(json.dumps(event))


async def emit_done(run_id: int):
    """
    Signal that the stream is complete.
    Puts a None sentinel into the queue — the SSE generator will close
    the stream when it sees this.
    """
    q = _queues.get(run_id)
    if q:
        await q.put(None)
        log.info("emitted done sentinel", run_id=run_id)


async def subscribe(run_id: int) -> AsyncIterator[str]:
    """
    Async generator that yields SSE-formatted event strings for a run.

    Usage in FastAPI SSE endpoint:
        async def event_generator():
            async for event_str in event_bus.subscribe(run_id):
                yield f"data: {event_str}\n\n"

    Yields None-terminated — stops when the run signals completion.
    If the queue doesn't exist yet (run just queued), waits up to 30s.
    """
    # Wait up to 30s for the queue to appear (run may not have started yet)
    waited = 0
    while run_id not in _queues and waited < 30:
        await asyncio.sleep(0.5)
        waited += 0.5

    q = _queues.get(run_id)
    if not q:
        # Run never started — emit an error event and close
        yield json.dumps({
            "event": "error",
            "message": f"Run {run_id} not found or never started",
        })
        return

    while True:
        try:
            # Wait up to 60s for next event (prevents hanging streams)
            event = await asyncio.wait_for(q.get(), timeout=60.0)
        except asyncio.TimeoutError:
            # Send a keepalive ping so the connection doesn't drop
            yield json.dumps({"event": "ping"})
            continue

        if event is None:
            # Terminal sentinel — run is complete, close stream
            break

        yield event
