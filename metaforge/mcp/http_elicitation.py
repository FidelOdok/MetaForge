"""Ask the human through an HTTP client (FORGE-423).

FORGE-360 built elicitation and FORGE-416 fixed the protocol revision it
needs, and inline approvals were still unreachable for every plugin: the
elicitor was attached only on the stdio path, so ``can_elicit`` was false on
HTTP no matter what a client declared. Every plugin connects over HTTP -- the
Claude Code manifest sets ``"type": "http"`` -- so every held write went to
the dashboard queue, and the feature existed for a transport almost nobody
uses.

What was missing was only the channel. ``StdioElicitor`` already defines the
semantics, ``ElicitAction``/``ElicitResult`` are transport-agnostic, and
:func:`mcp_core.elicitation.result_from_payload` is shared by both. This adds
the Streamable HTTP server-to-client direction:

``GET /mcp``
    a long-lived SSE stream the server pushes requests down. The existing
    ``GET /mcp/sse`` is a different thing -- a request/response convenience
    where the caller queues work as ``?request=`` params and the server
    closes with ``event: done`` -- and is left alone.

``POST /mcp`` carrying a JSON-RPC *response*
    the client's answer, routed here by id rather than to ``handle_request``.

**Per session, not per server.** One sidecar serves many clients from one
``UnifiedMcpServer``, so "can this connection be asked" cannot be a property
of the server. A stream is registered under the session id the transport
issued at ``initialize``, and a call with no open stream for its session
falls back to the dashboard queue rather than hanging.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from mcp_core.elicitation import (
    ELICITATION_PROTOCOL_VERSION,
    ElicitAction,
    ElicitResult,
    result_from_payload,
)

logger = structlog.get_logger(__name__)

__all__ = ["ElicitationHub", "HttpElicitor", "SessionChannel"]

#: How long a client has to answer before the call is treated as unanswered.
#: Matches ``StdioElicitor``; a reviewer reading a diff is slower than a
#: request timeout but faster than this.
DEFAULT_ELICITATION_TIMEOUT = 180.0

#: Bound on concurrently open streams, so a client that reconnects without
#: closing cannot grow the registry without limit. Evicts oldest-first, like
#: the session-project registry it sits beside.
_MAX_STREAMS = 256


@dataclass
class SessionChannel:
    """One client's open server-to-client stream, and what it can be asked.

    ``declared_elicitation`` and ``protocol`` are recorded from that
    session's own ``initialize`` rather than read off the server, because the
    server's copy is whichever client connected last. Routing an approval on
    that would ask the wrong question of the wrong person.
    """

    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    declared_elicitation: bool = False
    protocol: str = ""
    #: Whether a client is currently reading this channel.
    #:
    #: Tracked separately from the channel's existence because the two are
    #: not the same: ``note_initialize`` creates a channel to record the
    #: handshake, which happens before any stream is opened and may be
    #: followed by none at all. Treating "a channel exists" as "someone is
    #: listening" made ``available`` true for a client that had never opened
    #: one -- so the gate would have chosen elicitation and pushed a question
    #: into a queue nobody reads, which is worse than the dashboard fallback
    #: it displaced. A test caught it.
    stream_open: bool = False

    @property
    def eligible(self) -> bool:
        """All three conditions, per connection.

        The same three ``UnifiedMcpServer.can_elicit`` checks, except they
        are facts about *this* session: someone is reading this stream, the
        capability came from this session's ``initialize``, and so did the
        revision.
        """
        return (
            self.stream_open
            and self.declared_elicitation
            and self.protocol >= ELICITATION_PROTOCOL_VERSION
        )


class ElicitationHub:
    """Routes ``elicitation/create`` to the client that asked for it.

    Owns three things, all keyed by session id: the open streams, what each
    session declared, and the answers still outstanding.
    """

    def __init__(self, *, timeout_seconds: float = DEFAULT_ELICITATION_TIMEOUT) -> None:
        self._timeout = timeout_seconds
        self._channels: dict[str, SessionChannel] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._next = 0

    # -- what a session declared ---------------------------------------

    def note_initialize(
        self, session_id: str | None, *, capabilities: Any, negotiated_protocol: str
    ) -> None:
        """Record one session's elicitation eligibility from its handshake.

        Called by the transport because only the transport knows which
        session a request belongs to. A session that initialises twice
        (a reconnect) overwrites, which is right: the latest handshake is
        what the client is actually speaking.
        """
        if not session_id:
            return
        channel = self._channels.setdefault(session_id, SessionChannel())
        channel.declared_elicitation = isinstance(capabilities, dict) and (
            "elicitation" in capabilities
        )
        channel.protocol = negotiated_protocol
        self._evict_oldest()

    def _evict_oldest(self) -> None:
        while len(self._channels) > _MAX_STREAMS:
            oldest = next(iter(self._channels))
            self._channels.pop(oldest, None)
            logger.warning("mcp_elicitation_channel_evicted", session_id=oldest)

    # -- the stream ----------------------------------------------------

    async def stream(self, session_id: str) -> AsyncIterator[bytes]:
        """SSE frames for one session, until the client disconnects.

        A heartbeat keeps intermediaries from closing an idle connection --
        an approval stream is idle almost all the time, and a proxy that
        reaps it would turn "ask the user" into "hang for three minutes and
        then fall back", which looks like the server being slow rather than
        the stream being gone.
        """
        channel = self._channels.setdefault(session_id, SessionChannel())
        channel.stream_open = True
        logger.info("mcp_elicitation_stream_opened", session_id=session_id)
        try:
            # Named so a client can tell an open stream from a stalled
            # connection before anything has been asked.
            yield b": metaforge elicitation stream open\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(channel.queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                yield f"event: message\ndata: {message}\n\n".encode()
        finally:
            channel.stream_open = False
            # Only drop the channel if it is still ours: a reconnect may
            # already have replaced it, and removing the new one would leave
            # the live client unreachable.
            if self._channels.get(session_id) is channel:
                self._channels.pop(session_id, None)
            logger.info("mcp_elicitation_stream_closed", session_id=session_id)

    def available(self, session_id: str | None) -> bool:
        """Whether this session can be asked right now."""
        if not session_id:
            return False
        channel = self._channels.get(session_id)
        return channel is not None and channel.eligible

    # -- asking and answering ------------------------------------------

    async def elicit(
        self, session_id: str, message: str, requested_schema: dict[str, Any]
    ) -> ElicitResult:
        """Push one ``elicitation/create`` and wait for that session's answer."""
        channel = self._channels.get(session_id)
        if channel is None:
            # The stream went away between the eligibility check and here.
            # Cancel rather than raise: the gate maps it to TIMED_OUT, which
            # is the truth -- nobody was asked, and nobody said no.
            logger.warning("mcp_elicitation_no_stream", session_id=session_id)
            return ElicitResult(ElicitAction.CANCEL)

        self._next += 1
        message_id = f"elicit-{session_id}-{self._next}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await channel.queue.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "method": "elicitation/create",
                    "params": {"message": message, "requestedSchema": requested_schema},
                }
            )
        )
        try:
            payload = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            self._pending.pop(message_id, None)
            logger.warning("mcp_elicitation_timeout", message_id=message_id)
            return ElicitResult(ElicitAction.CANCEL)
        finally:
            self._pending.pop(message_id, None)
        return result_from_payload(payload)

    def resolve(self, payload: dict[str, Any]) -> bool:
        """Hand an inbound JSON-RPC response to whoever is waiting for it.

        Returns False when nothing is waiting, so the transport can treat the
        body as an ordinary request rather than dropping it -- the same
        contract ``StdioElicitor.resolve`` keeps, for the same reason.
        """
        raw_id = payload.get("id")
        if not isinstance(raw_id, str):
            return False
        future = self._pending.pop(raw_id, None)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True


class HttpElicitor:
    """The :class:`mcp_core.elicitation.Elicitor` the HTTP app attaches.

    One instance for the whole app. It resolves the session from the active
    call context at the moment it is called, so a single
    ``UnifiedMcpServer`` shared by many clients still asks the right one.
    """

    def __init__(self, hub: ElicitationHub) -> None:
        self._hub = hub

    @staticmethod
    def _session() -> str | None:
        from mcp_core.context import current_context

        try:
            ctx = current_context()
        except Exception:  # noqa: BLE001 — no context is not an error here
            return None
        return str(ctx.session_id) if ctx.session_is_stable else None

    def available(self) -> bool:
        """Consulted by ``can_elicit``. False falls back to the queue.

        Without this, ``can_elicit`` would be true for every HTTP client the
        moment an elicitor exists -- including ones with no stream open --
        and the gate would choose elicitation over the dashboard and then
        have nowhere to ask. A fallback that is chosen and then fails is
        worse than one that is never chosen.
        """
        return self._hub.available(self._session())

    async def __call__(self, message: str, requested_schema: dict[str, Any]) -> ElicitResult:
        session_id = self._session()
        if session_id is None:
            logger.warning("mcp_elicitation_no_session")
            return ElicitResult(ElicitAction.CANCEL)
        return await self._hub.elicit(session_id, message, requested_schema)


def is_jsonrpc_response(payload: Any) -> bool:
    """Is this body an answer to something the *server* asked?

    A response carries an id and a result or error and no method. Checked
    before dispatch, because routing our own answer into ``handle_request``
    would come back as "Unknown method: None" -- the same misroute the stdio
    loop had to learn to avoid.
    """
    return (
        isinstance(payload, dict)
        and "method" not in payload
        and payload.get("id") is not None
        and ("result" in payload or "error" in payload)
    )


def session_uuid(raw: str | None) -> UUID | None:
    """Parse a session header, or None. Never raises."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None
