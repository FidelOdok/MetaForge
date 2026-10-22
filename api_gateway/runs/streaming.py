"""Server-Sent Events streaming for runs (MET-547, Phase 1).

Clients connect to ``GET /v1/runs/{id}/events`` and receive a run's status
transitions in real time. The run store fires a synchronous ``on_transition``
callback; :class:`RunStreamManager` fans each transition out to per-run
asyncio queues that connected SSE generators drain until the run is terminal.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from orchestrator.harness.runs import Run

logger = structlog.get_logger(__name__)

# Sentinel pushed to a queue to tell the generator to close.
_CLOSE = object()

#: Most tool events held per subscriber before the oldest is dropped (FORGE-644).
#: A slow client must never grow the gateway's memory without bound.
SUBSCRIBER_QUEUE_LIMIT = 256

#: Most tool events kept per run for a reconnecting client to replay.
REPLAY_LIMIT = 200

#: A comment sent when nothing else has been, so a proxy does not close an
#: idle stream (a phase can think for a minute between tool calls).
SSE_HEARTBEAT = ": keep-alive\n\n"
HEARTBEAT_SECONDS = 15.0

#: Headers that keep a proxy from buffering or rewriting the stream.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


@dataclass(frozen=True)
class ToolEvent:
    """One tool call edge on a run's stream (FORGE-643).

    Carries no arguments and no result, only what a viewer needs to show
    "tool X is running" and how long it took.
    """

    run_id: str
    seq: int
    call_id: str
    tool: str
    phase: str
    status: str
    duration_ms: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "call_id": self.call_id,
            "tool": self.tool,
            "phase": self.phase,
            "status": self.status,
            "duration_ms": self.duration_ms,
        }


def tool_event_to_sse(event: ToolEvent) -> str:
    """Format a tool event as one SSE ``tool.progress`` frame with an ``id:``."""
    return f"id: {event.seq}\nevent: tool.progress\ndata: {json.dumps(event.payload())}\n\n"


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
        self._seq: dict[str, int] = defaultdict(int)
        self._replay: dict[str, deque[ToolEvent]] = defaultdict(lambda: deque(maxlen=REPLAY_LIMIT))

    def publish(self, run: Run) -> None:
        """Push a run's current state to its subscribers (sync-callable)."""
        for queue in list(self._subscribers.get(run.id, ())):
            queue.put_nowait(run)
        if run.is_terminal and not self._subscribers.get(run.id):
            self._seq.pop(run.id, None)
            self._replay.pop(run.id, None)

    def publish_tool(
        self,
        run_id: str,
        *,
        call_id: str,
        tool: str,
        phase: str,
        status: str,
        duration_ms: int | None = None,
    ) -> ToolEvent:
        """Push one tool event to a run's subscribers (sync-callable).

        Each subscriber's queue is bounded: when a slow client is full its
        oldest tool event is dropped, never a run status and never the new one.
        """
        self._seq[run_id] += 1
        event = ToolEvent(
            run_id=run_id,
            seq=self._seq[run_id],
            call_id=call_id,
            tool=tool,
            phase=phase,
            status=status,
            duration_ms=duration_ms,
        )
        self._replay[run_id].append(event)
        for queue in list(self._subscribers.get(run_id, ())):
            if queue.qsize() >= SUBSCRIBER_QUEUE_LIMIT:
                _drop_oldest_tool_event(queue)
            queue.put_nowait(event)
        return event

    def replay_after(self, run_id: str, last_event_id: int) -> list[ToolEvent]:
        """Tool events a reconnecting client missed, oldest first."""
        return [e for e in self._replay.get(run_id, ()) if e.seq > last_event_id]

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


def _drop_oldest_tool_event(queue: asyncio.Queue[object]) -> None:
    """Make room in a full queue by discarding its oldest tool event."""
    kept: list[object] = []
    dropped = False
    while not queue.empty():
        item = queue.get_nowait()
        if not dropped and isinstance(item, ToolEvent):
            dropped = True
            continue
        kept.append(item)
    for item in kept:
        queue.put_nowait(item)


async def run_event_stream(
    run_id: str,
    snapshot: Run,
    manager: RunStreamManager,
    last_event_id: int | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for a run: a snapshot, then live transitions.

    Closes when the run reaches a terminal status or the manager signals close.
    ``last_event_id`` is the ``Last-Event-ID`` of a reconnecting client: the
    tool events it missed are replayed after the snapshot.
    """
    queue = manager.subscribe(run_id)
    try:
        # Immediate snapshot so a late subscriber sees current state.
        yield run_event_to_sse(snapshot)
        sent_seq = last_event_id if last_event_id is not None else 0
        if last_event_id is not None:
            for missed in manager.replay_after(run_id, last_event_id):
                sent_seq = missed.seq
                yield tool_event_to_sse(missed)
        if snapshot.is_terminal:
            return
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield SSE_HEARTBEAT
                continue
            if item is _CLOSE:
                return
            if isinstance(item, ToolEvent):
                # An event can sit in the queue and in the replay both, when
                # it was published between subscribing and replaying.
                if item.seq <= sent_seq:
                    continue
                sent_seq = item.seq
                # Progress never ends the stream: only a run status does.
                yield tool_event_to_sse(item)
                continue
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
            if isinstance(item, ToolEvent):
                continue
            assert isinstance(item, Run)
            await send_json(run_event_payload(item))
            if item.is_terminal:
                return
    finally:
        manager.unsubscribe(run_id, queue)
        logger.info("run_ws_closed", run_id=run_id)
