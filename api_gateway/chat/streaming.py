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
from collections import defaultdict
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
    AGENT_DONE = "agent.done"
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
        return f"event: {self.event.value}\ndata: {json.dumps(payload)}\n\n"


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

    def __init__(self) -> None:
        self._connections: dict[str, list[asyncio.Queue[StreamEvent | None]]] = defaultdict(list)

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

    async def close_all(self, thread_id: str) -> None:
        """Send a sentinel (``None``) to all listeners on *thread_id* and clean up."""
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


async def notify_agent_done(thread_id: str, agent_id: str = "agent") -> int:
    """Push an ``agent.done`` event."""
    event = StreamEvent(
        event=StreamEventType.AGENT_DONE,
        data={"agent_id": agent_id},
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

    logger.info("stream_started", thread_id=thread_id)

    try:
        while True:
            event = await queue.get()
            if event is None:
                # Sentinel — server closed the stream
                break
            yield event.to_sse()
    except asyncio.CancelledError:
        logger.info("stream_cancelled", thread_id=thread_id)
    finally:
        mgr.unsubscribe(thread_id, queue)
        logger.info("stream_ended", thread_id=thread_id)
