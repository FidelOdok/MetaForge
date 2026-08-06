"""Server-Sent Events (SSE) streaming for chat threads.

Provides real-time event streaming for chat threads via the SSE protocol.
Clients connect to ``GET /v1/chat/threads/{id}/stream`` and receive events
as new messages are created, agents start typing, or errors occur.

Event types:
- ``message.created`` -- a new message was added to the thread
- ``agent.typing``    -- an agent is processing a response
- ``agent.done``      -- an agent finished processing
- ``error``           -- an error occurred during processing
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.chat.streaming")


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class StreamEventType(StrEnum):
    """SSE event types emitted by the chat stream."""

    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    AGENT_TYPING = "agent.typing"
    AGENT_STEP = "agent.step"
    AGENT_THINKING = "agent.thinking"
    AGENT_ACTION_STARTED = "agent.action_started"
    AGENT_DONE = "agent.done"
    CONTEXT_STATS = "context.stats"
    SCOPE_CHANGED = "scope.changed"
    ERROR = "error"


class StreamEvent(BaseModel):
    """A single SSE event to be pushed to connected clients.

    Attributes
    ----------
    event:
        The event type (``message.created``, ``agent.typing``, etc.).
    data:
        JSON-serializable payload for the event.
    thread_id:
        The thread this event belongs to.
    timestamp:
        When the event was created (ISO 8601).
    """

    event: StreamEventType
    data: dict[str, Any] = Field(default_factory=dict)
    thread_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # MET-593: per-thread monotonic id, assigned by the manager at broadcast.
    # Lets a reconnecting client resume via the standard Last-Event-ID header
    # instead of silently losing whatever was broadcast during the gap.
    event_id: int | None = None

    def to_sse(self) -> str:
        """Format as an SSE wire-protocol string.

        Returns a string in the format::

            event: message.created
            data: {"key": "value", ...}

        with a trailing blank line to delimit the event.
        """
        payload = {
            "data": self.data,
            "thread_id": self.thread_id,
            "timestamp": self.timestamp.isoformat(),
        }
        id_line = f"id: {self.event_id}\n" if self.event_id is not None else ""
        return f"{id_line}event: {self.event.value}\ndata: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Stream manager
# ---------------------------------------------------------------------------


class ChatStreamManager:
    """Manages active SSE connections per thread.

    Each connected client gets an ``asyncio.Queue`` that receives
    :class:`StreamEvent` instances.  The manager provides methods to
    subscribe, unsubscribe, and broadcast events to all listeners on a
    given thread.
    """

    # Recent-event ring per thread (MET-593). Sized for the largest realistic
    # reconnect gap (a long CAD turn's steps + thinking bursts); in-memory and
    # per-process, matching the broadcast singleton's scope.
    _RING_SIZE = 256

    def __init__(self) -> None:
        self._connections: dict[str, list[asyncio.Queue[StreamEvent | None]]] = defaultdict(list)
        self._next_id: dict[str, int] = defaultdict(int)
        self._recent: dict[str, deque[StreamEvent]] = {}

    # -- connection lifecycle -----------------------------------------------

    def subscribe(self, thread_id: str) -> asyncio.Queue[StreamEvent | None]:
        """Register a new SSE client for *thread_id*.

        Returns an ``asyncio.Queue`` that the caller should read from
        in its streaming loop.
        """
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        self._connections[thread_id].append(queue)
        logger.info(
            "stream_client_subscribed",
            thread_id=thread_id,
            active_connections=len(self._connections[thread_id]),
        )
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue[StreamEvent | None]) -> None:
        """Remove a client queue from *thread_id* listeners."""
        conns = self._connections.get(thread_id, [])
        try:
            conns.remove(queue)
        except ValueError:
            pass
        if not conns:
            self._connections.pop(thread_id, None)
        logger.info(
            "stream_client_unsubscribed",
            thread_id=thread_id,
            remaining_connections=len(self._connections.get(thread_id, [])),
        )

    def connection_count(self, thread_id: str) -> int:
        """Return the number of active connections for *thread_id*."""
        return len(self._connections.get(thread_id, []))

    def active_threads(self) -> list[str]:
        """Return thread IDs that have at least one active connection."""
        return list(self._connections.keys())

    # -- event broadcasting -------------------------------------------------

    async def broadcast(self, event: StreamEvent) -> int:
        """Push *event* to all listeners on ``event.thread_id``.

        Returns the number of clients that received the event.
        """
        with tracer.start_as_current_span("stream.broadcast") as span:
            span.set_attribute("thread_id", event.thread_id)
            span.set_attribute("event_type", event.event.value)

            thread_id = event.thread_id
            # MET-593: assign the resume id and remember the event.
            self._next_id[thread_id] += 1
            event.event_id = self._next_id[thread_id]
            self._recent.setdefault(thread_id, deque(maxlen=self._RING_SIZE)).append(event)
            conns = self._connections.get(thread_id, [])
            count = 0
            for queue in conns:
                try:
                    queue.put_nowait(event)
                    count += 1
                except asyncio.QueueFull:
                    logger.warning(
                        "stream_queue_full",
                        thread_id=thread_id,
                    )
            span.set_attribute("clients_notified", count)
            logger.debug(
                "stream_event_broadcast",
                thread_id=thread_id,
                event_type=event.event.value,
                clients_notified=count,
            )
            return count

    def replay_since(self, thread_id: str, last_event_id: int) -> list[StreamEvent]:
        """Buffered events with id > ``last_event_id`` (MET-593 resume)."""
        return [
            e
            for e in self._recent.get(thread_id, ())
            if e.event_id is not None and e.event_id > last_event_id
        ]

    async def close_all(self, thread_id: str) -> None:
        """Send a sentinel (``None``) to all listeners on *thread_id* and clean up."""
        self._recent.pop(thread_id, None)
        self._next_id.pop(thread_id, None)
        conns = self._connections.pop(thread_id, [])
        for queue in conns:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        logger.info("stream_closed_all", thread_id=thread_id, closed=len(conns))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

stream_manager = ChatStreamManager()


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


async def notify_new_message(
    thread_id: str,
    message_data: dict[str, Any],
) -> int:
    """Push a ``message.created`` event to all SSE clients on *thread_id*.

    Parameters
    ----------
    thread_id:
        The thread the message belongs to.
    message_data:
        Serializable dict with message fields (id, content, actor_id, etc.).

    Returns
    -------
    int
        Number of clients that received the event.
    """
    event = StreamEvent(
        event=StreamEventType.MESSAGE_CREATED,
        data=message_data,
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_message_delta(thread_id: str, text: str, agent_id: str = "agent") -> int:
    """Push a ``message.delta`` event — one token/chunk of a streaming answer."""
    event = StreamEvent(
        event=StreamEventType.MESSAGE_DELTA,
        data={"delta": text, "agent_id": agent_id},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_agent_typing(thread_id: str, agent_id: str = "agent") -> int:
    """Push an ``agent.typing`` event."""
    event = StreamEvent(
        event=StreamEventType.AGENT_TYPING,
        data={"agent_id": agent_id},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_agent_thinking(thread_id: str, delta: str, kind: str = "draft") -> int:
    """Push an ``agent.thinking`` event — a live text delta from the model
    WHILE it generates (MET-591). Ephemeral typing-indicator-grade content:
    clients render it in the thinking line; the persisted final message stays
    authoritative (a rendered thinking draft may become a tool-call preamble
    rather than the answer).

    ``kind`` types the delta (MET-592, Claude block-tag pattern): ``draft``
    for ordinary response text mid-loop, ``reasoning`` for extended-thinking
    blocks — clients may render them differently.
    """
    event = StreamEvent(
        event=StreamEventType.AGENT_THINKING,
        data={"delta": delta, "kind": kind},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_agent_action_started(thread_id: str, tool: str) -> int:
    """Push an ``agent.action_started`` event — the model committed to a tool
    call and its NAME is known, before arguments finish streaming and before
    execution (MET-592 "typed from step zero"). The completed ``agent.step``
    for the same call follows once it executes."""
    event = StreamEvent(
        event=StreamEventType.AGENT_ACTION_STARTED,
        data={"tool": tool},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_agent_step(thread_id: str, step: dict[str, Any], agent_id: str = "agent") -> int:
    """Push an ``agent.step`` event — one ReAct step (tool call / result / thought).

    Makes the agent legible: the dashboard renders these as a tool-call timeline
    instead of only the final text (MET-552). ``step`` is a JSON-safe dict:
    ``{index, thought, tool, arguments, observation, error, final}``.
    """
    event = StreamEvent(
        event=StreamEventType.AGENT_STEP,
        data={"step": step, "agent_id": agent_id},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_agent_done(thread_id: str, agent_id: str = "agent") -> int:
    """Push an ``agent.done`` event."""
    event = StreamEvent(
        event=StreamEventType.AGENT_DONE,
        data={"agent_id": agent_id},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_context_stats(thread_id: str, stats: dict[str, Any]) -> int:
    """Push a ``context.stats`` event — the state of the model's context window.

    Emitted once at the start of a harness turn so a client can show what is
    going into this turn (system prompt, project brief, history, tool schemas,
    the message) versus what is available (the model's context window, and how
    many work products / history turns / tools were included vs. exist). ``stats``
    is the JSON-safe dict from ``harness_backend.compute_context_stats``.
    """
    event = StreamEvent(
        event=StreamEventType.CONTEXT_STATS,
        data=stats,
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_scope_changed(
    thread_id: str,
    *,
    scope_kind: str,
    scope_entity_id: str,
    project_name: str | None = None,
) -> int:
    """Push a ``scope.changed`` event — the thread's scope was rescoped in place.

    Emitted by ``chat/scope.py::apply_thread_scope`` (MET-580), the single path
    both the human ``/project``-equivalent route and the agent-callable
    ``chat.set_project_scope`` tool go through. A client renders this itself
    (e.g. a transcript notice) rather than relying on the model's prose to
    mention the change — the notice must appear whether or not the model says so.
    """
    event = StreamEvent(
        event=StreamEventType.SCOPE_CHANGED,
        data={
            "scope_kind": scope_kind,
            "scope_entity_id": scope_entity_id,
            "project_name": project_name,
        },
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


async def notify_error(thread_id: str, error: str) -> int:
    """Push an ``error`` event."""
    event = StreamEvent(
        event=StreamEventType.ERROR,
        data={"error": error},
        thread_id=thread_id,
    )
    return await stream_manager.broadcast(event)


# ---------------------------------------------------------------------------
# SSE async generator
# ---------------------------------------------------------------------------


async def stream_thread(
    thread_id: str,
    manager: ChatStreamManager | None = None,
    last_event_id: int | None = None,
) -> Any:
    """Async generator that yields SSE-formatted strings for *thread_id*.

    The generator subscribes to the stream manager, yields events as they
    arrive, and cleans up on exit (client disconnect or cancellation).

    Parameters
    ----------
    thread_id:
        The thread to stream events for.
    manager:
        Optional stream manager override (for testing).  Defaults to the
        module-level ``stream_manager`` singleton.
    """
    mgr = manager or stream_manager
    queue = mgr.subscribe(thread_id)

    logger.info("stream_started", thread_id=thread_id, resume_from=last_event_id)

    # MET-593 resume: subscribe FIRST (so nothing new is missed), then replay
    # the buffered gap; the live loop skips anything the replay already sent.
    replayed_to = last_event_id if last_event_id is not None else -1
    try:
        if last_event_id is not None:
            for buffered in mgr.replay_since(thread_id, last_event_id):
                if buffered.event_id is not None and buffered.event_id > replayed_to:
                    replayed_to = buffered.event_id
                yield buffered.to_sse()
        while True:
            event = await queue.get()
            if event is None:
                # Sentinel — server closed the stream
                break
            if event.event_id is not None and event.event_id <= replayed_to:
                continue  # already delivered via replay
            yield event.to_sse()
    except asyncio.CancelledError:
        logger.info("stream_cancelled", thread_id=thread_id)
    finally:
        mgr.unsubscribe(thread_id, queue)
        logger.info("stream_ended", thread_id=thread_id)
