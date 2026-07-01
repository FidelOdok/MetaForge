"""Server-Sent Events streaming for runs (MET-547, Phase 1).

Clients connect to ``GET /v1/runs/{id}/events`` and receive a run's status
transitions in real time. The run store fires a synchronous ``on_transition``
callback; :class:`RunStreamManager` fans each transition out to per-run
asyncio queues that connected SSE generators drain until the run is terminal.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog

from orchestrator.harness.runs import Run

logger = structlog.get_logger(__name__)

# Sentinel pushed to a queue to tell the generator to close.
_CLOSE = object()


def run_event_payload(run: Run) -> dict[str, Any]:
    """The JSON body describing a run's current state (SSE + WS share this)."""
    return {
        "id": run.id,
        "status": str(run.status),
        "updated_at": run.updated_at,
        "approval_reason": run.approval_reason,
        "error": run.error,
    }


def run_event_to_sse(run: Run) -> str:
    """Format a run's current state as one SSE ``run.status`` event."""
    return f"event: run.status\ndata: {json.dumps(run_event_payload(run))}\n\n"


class RunStreamManager:
    """Fan run transitions out to per-run subscriber queues."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[object]]] = defaultdict(list)

    def publish(self, run: Run) -> None:
        """Push a run's current state to its subscribers (sync-callable)."""
        for queue in list(self._subscribers.get(run.id, ())):
            queue.put_nowait(run)

    def subscribe(self, run_id: str) -> asyncio.Queue[object]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        self._subscribers[run_id].append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[object]) -> None:
        subs = self._subscribers.get(run_id)
        if subs and queue in subs:
            subs.remove(queue)
        if subs is not None and not subs:
            self._subscribers.pop(run_id, None)

    def close(self, run_id: str) -> None:
        for queue in list(self._subscribers.get(run_id, ())):
            queue.put_nowait(_CLOSE)


async def run_event_stream(
    run_id: str,
    snapshot: Run,
    manager: RunStreamManager,
) -> AsyncIterator[str]:
    """Yield SSE frames for a run: a snapshot, then live transitions.

    Closes when the run reaches a terminal status or the manager signals close.
    """
    queue = manager.subscribe(run_id)
    try:
        # Immediate snapshot so a late subscriber sees current state.
        yield run_event_to_sse(snapshot)
        if snapshot.is_terminal:
            return
        while True:
            item = await queue.get()
            if item is _CLOSE:
                return
            assert isinstance(item, Run)
            yield run_event_to_sse(item)
            if item.is_terminal:
                return
    finally:
        manager.unsubscribe(run_id, queue)
        logger.info("run_stream_closed", run_id=run_id)


async def run_ws_loop(
    send_json: Callable[[dict[str, Any]], Awaitable[None]],
    run_id: str,
    snapshot: Run,
    manager: RunStreamManager,
) -> None:
    """Push run events over an injected ``send_json`` until terminal or close.

    Same subscribe/snapshot/drain shape as the SSE stream, but emits JSON
    payloads. ``send_json`` is injected so the loop is unit-testable without a
    real WebSocket.
    """
    queue = manager.subscribe(run_id)
    try:
        await send_json(run_event_payload(snapshot))
        if snapshot.is_terminal:
            return
        while True:
            item = await queue.get()
            if item is _CLOSE:
                return
            assert isinstance(item, Run)
            await send_json(run_event_payload(item))
            if item.is_terminal:
                return
    finally:
        manager.unsubscribe(run_id, queue)
        logger.info("run_ws_closed", run_id=run_id)
