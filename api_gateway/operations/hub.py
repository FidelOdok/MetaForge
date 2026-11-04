"""The project operation stream (FORGE-785, RT-015/RT-017).

The snapshot, replay and heartbeat pattern of
:mod:`api_gateway.twin.live`, backed by the durable
:class:`~api_gateway.operations.store.OperationStore` instead of an
in-memory ring, so a client that reconnects after a gateway restart is still
sent what it missed.

A client first reads ``GET /v1/projects/{id}/operations`` (the snapshot and
its ``cursor``), then opens the stream with ``?after=<cursor>`` or
``Last-Event-ID``. Frames:

* ``event: operation`` with ``id: <project_seq>``: one event;
* ``event: heartbeat`` every 15 s: ``{"server_time", "cursor"}``;
* ``event: resync``: ``{"reason", "cursor"}``, when the cursor is unknown
  (ahead of the project's log) or older than what is kept. The client
  refetches the snapshot and resumes from the cursor given.

A subscriber whose queue fills is not dropped and loses nothing: the stream
reads what it missed back from the store.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from api_gateway.operations.schemas import OperationEvent
from api_gateway.operations.store import OperationStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.hub")

__all__ = ["OperationHub", "SSE_HEADERS"]

#: Most events queued per subscriber before it is switched to reading the store.
SUBSCRIBER_QUEUE_LIMIT = 512

#: Most events replayed on reconnect before the client is asked to resync.
REPLAY_LIMIT = 5000

HEARTBEAT_SECONDS = 15.0

#: Pushes the first frame through a proxy that buffers (see ``twin/live.py``).
SSE_PADDING = ": " + " " * 2048 + "\n\n"

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

_CLOSE = object()
#: Put on a subscriber's queue in place of events it could not hold.
_GAP = object()


def _frame(event_name: str, body: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event_name}\ndata: {json.dumps(body)}\n\n"


def operation_frame(event: OperationEvent) -> str:
    return _frame("operation", event.wire(), event.project_seq)


class OperationHub:
    """Fans stored operation events out to each project's stream subscribers."""

    def __init__(
        self,
        store: OperationStore,
        *,
        metrics: Callable[[], Any] | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.store = store
        self._metrics = metrics
        self._heartbeat = heartbeat_seconds
        self._subscribers: dict[str, list[asyncio.Queue[object]]] = defaultdict(list)

    def _collector(self) -> Any | None:
        try:
            return self._metrics() if self._metrics is not None else None
        except Exception:  # noqa: BLE001 - a metric never breaks a stream
            return None

    def publish(self, event: OperationEvent) -> None:
        """Hand a stored event to the project's subscribers (sync-callable)."""
        for queue in list(self._subscribers.get(event.project_id, ())):
            if queue.qsize() >= SUBSCRIBER_QUEUE_LIMIT:
                # Never drop silently: empty the queue and tell the stream to
                # read what it missed from the store.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(_GAP)
                continue
            queue.put_nowait(event)

    def subscribe(self, project_id: str) -> asyncio.Queue[object]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        self._subscribers[project_id].append(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue[object]) -> None:
        subs = self._subscribers.get(project_id)
        if subs and queue in subs:
            subs.remove(queue)
        if subs is not None and not subs:
            self._subscribers.pop(project_id, None)

    def subscriber_count(self, project_id: str) -> int:
        return len(self._subscribers.get(project_id, ()))

    def close(self, project_id: str) -> None:
        for queue in list(self._subscribers.get(project_id, ())):
            queue.put_nowait(_CLOSE)

    def resync_reason(self, project_id: str, after: int) -> str | None:
        """Why a client resuming from ``after`` must refetch, or None."""
        cursor = self.store.cursor(project_id)
        if after > cursor:
            return "unknown_cursor"
        if after == cursor:
            return None
        oldest = self.store.oldest_seq(project_id)
        if oldest is None or after < oldest - 1:
            return "cursor_expired"
        if self.store.count_after(project_id, after) > REPLAY_LIMIT:
            return "cursor_expired"
        return None

    async def stream(self, project_id: str, after: int | None = None) -> AsyncIterator[str]:
        """SSE frames for a project: what was missed since ``after``, then live.

        ``after=None`` streams only what happens from now on.
        """
        queue = self.subscribe(project_id)
        collector = self._collector()
        if collector is not None:
            collector.record_operation_stream_active(1)
        try:
            # No await between subscribing and reading the cursor, so nothing
            # published in between is missed or sent twice.
            sent = self.store.cursor(project_id)
            yield SSE_PADDING
            if after is not None:
                # No await between subscribing and reading the store, so an
                # event is in the replay, the queue, or both (skipped below).
                with tracer.start_as_current_span("operations.stream.replay") as span:
                    span.set_attribute("project_id", project_id)
                    span.set_attribute("after", after)
                    reason = self.resync_reason(project_id, after)
                    if reason is not None:
                        cursor = self.store.cursor(project_id)
                        span.set_attribute("resync", reason)
                        if collector is not None:
                            collector.record_operation_stream_resync(reason)
                        logger.info(
                            "operation_stream_resync",
                            project_id=project_id,
                            after=after,
                            cursor=cursor,
                            reason=reason,
                        )
                        sent = cursor
                        missed: list[OperationEvent] = []
                        resync_frame = _frame("resync", {"reason": reason, "cursor": cursor})
                    else:
                        resync_frame = None
                        sent = after
                        missed = self.store.events_after(project_id, after)
                        span.set_attribute("replayed", len(missed))
                        if collector is not None:
                            collector.record_operation_stream_replay()
                if resync_frame is not None:
                    yield resync_frame
                for event in missed:
                    sent = event.project_seq
                    yield operation_frame(event)
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), self._heartbeat)
                except TimeoutError:
                    yield _frame(
                        "heartbeat",
                        {
                            "server_time": datetime.now(UTC).isoformat(),
                            "cursor": self.store.cursor(project_id),
                        },
                    )
                    continue
                if item is _CLOSE:
                    return
                if item is _GAP:
                    for event in self.store.events_after(project_id, sent):
                        if event.project_seq > sent:
                            sent = event.project_seq
                            yield operation_frame(event)
                    continue
                assert isinstance(item, OperationEvent)
                if item.project_seq <= sent:
                    continue
                sent = item.project_seq
                yield operation_frame(item)
        finally:
            self.unsubscribe(project_id, queue)
            if collector is not None:
                collector.record_operation_stream_active(-1)
            logger.info("operation_stream_closed", project_id=project_id)
