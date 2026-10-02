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

``POST /mcp`` answered as an SSE stream (FORGE-464, FORGE-465)
    the call's own response stream. Claude Code 2.1.286 never opens
    ``GET /mcp``, so the stream above alone left every held write on the
    dashboard queue for the client most likely to answer inline. The spec
    lets a server answer a POST with ``text/event-stream`` and send requests
    *related to that call* before the result, which is exactly what an
    approval is. Used only when the session has no ``GET /mcp`` open: a
    client that opened one keeps receiving its questions there, so nothing
    FORGE-423 shipped changes under it. A call that carries a
    ``progressToken`` gets the stream too, whether or not its client can
    elicit (FORGE-465): a write held for the dashboard sends progress on it,
    naming the approval, so the client knows what it is waiting for.

**Per session, not per server.** One sidecar serves many clients from one
``UnifiedMcpServer``, so "can this connection be asked" cannot be a property
of the server. A stream is registered under the session id the transport
issued at ``initialize``, and a call with no open stream for its session
falls back to the dashboard queue rather than hanging.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from mcp_core.elicitation import (
    ELICITATION_PROTOCOL_VERSION,
    ElicitAction,
    ElicitResult,
    cancelled_notification,
    result_from_payload,
)

logger = structlog.get_logger(__name__)

__all__ = ["CallStream", "ElicitationHub", "HttpElicitor", "HttpNotifier", "SessionChannel"]

#: How long a client has to answer before the call is treated as unanswered.
#: Matches ``StdioElicitor``; a reviewer reading a diff is slower than a
#: request timeout but faster than this.
DEFAULT_ELICITATION_TIMEOUT = 180.0

#: Bound on concurrently open streams, so a client that reconnects without
#: closing cannot grow the registry without limit. Evicts oldest-first, like
#: the session-project registry it sits beside.
_MAX_STREAMS = 256

#: How many withdrawn questions are remembered, so a late answer to one is
#: recognised and ignored (FORGE-472) rather than dispatched as a request.
_MAX_ENDED = 256


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
    def declared(self) -> bool:
        """This session's own handshake says it can be asked.

        Two of the three conditions. The third, a channel someone is reading,
        is either this stream or the call's own (FORGE-464), so it is checked
        by the caller that knows which.
        """
        return self.declared_elicitation and self.protocol >= ELICITATION_PROTOCOL_VERSION

    @property
    def eligible(self) -> bool:
        """All three conditions on the standalone ``GET /mcp`` stream.

        The same three ``UnifiedMcpServer.can_elicit`` checks, except they
        are facts about *this* session: someone is reading this stream, the
        capability came from this session's ``initialize``, and so did the
        revision.
        """
        return self.stream_open and self.declared


@dataclass
class CallStream:
    """One ``tools/call`` POST answered as SSE, while it is still open.

    FORGE-464. Exists only while the transport is still holding that POST's
    response, which is the one window in which a question sent on it can be
    read. ``pending`` is kept so a client that hangs up mid-question is
    answered ``cancel`` at once, rather than leaving the call to wait out
    the whole elicitation timeout for a reply that cannot come.
    """

    session_id: str
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    pending: set[str] = field(default_factory=set)
    open: bool = True


#: The call stream for the request being handled, if its POST can carry one.
#: A context variable rather than a registry keyed by session, because one
#: session can have several calls in flight and each question belongs on the
#: stream of the call that raised it. Tasks copy it at creation, so the
#: handler task the transport starts sees the stream its POST opened.
_CALL_STREAM: ContextVar[CallStream | None] = ContextVar("mcp_call_stream", default=None)


class ElicitationHub:
    """Routes ``elicitation/create`` to the client that asked for it.

    Owns three things, all keyed by session id: the open streams, what each
    session declared, and the answers still outstanding.
    """

    def __init__(self, *, timeout_seconds: float = DEFAULT_ELICITATION_TIMEOUT) -> None:
        self._timeout = timeout_seconds
        self._channels: dict[str, SessionChannel] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        #: Questions the server stopped waiting on, oldest first, with why.
        self._ended: OrderedDict[str, str] = OrderedDict()
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
        logger.info(
            "mcp_elicitation_session_noted",
            session_id=session_id,
            declared_elicitation=channel.declared_elicitation,
            protocol=negotiated_protocol,
        )
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
            # The channel itself stays: it carries what this session declared
            # at initialize, and FORGE-464's call streams still need that
            # after a standalone stream closes. Dropping it here made a
            # session that once opened GET /mcp and then closed it look like
            # one that never declared elicitation at all. Eviction bounds the
            # registry instead.
            channel.stream_open = False
            logger.info("mcp_elicitation_stream_closed", session_id=session_id)

    # -- the call's own stream (FORGE-464) -----------------------------

    def call_stream_allowed(self, session_id: str | None) -> bool:
        """Whether a POST from this session may be answered as SSE.

        Only for a session whose handshake declared elicitation on a revision
        that has it, and that has no ``GET /mcp`` open (its questions go
        there). Anything else keeps its plain JSON response: switching a
        client's content type for a question it never said it could answer
        changes its transport and gains nothing.
        """
        if not session_id:
            return False
        channel = self._channels.get(session_id)
        return channel is not None and channel.declared and not channel.stream_open

    @contextmanager
    def call_stream(self, session_id: str) -> Iterator[CallStream]:
        """Bind a call stream to the current context for the block's length.

        The transport starts the handler task inside this block, so the task
        carries the stream; leaving the block resets the variable for the
        transport itself and nothing else.
        """
        stream = CallStream(session_id=session_id)
        token = _CALL_STREAM.set(stream)
        try:
            yield stream
        finally:
            _CALL_STREAM.reset(token)

    def close_call_stream(self, stream: CallStream) -> None:
        """The POST's response is finished, or its client has gone.

        Any question still waiting on it is answered ``cancel`` now: nobody
        can read it any more, and the gate maps cancel to TIMED_OUT, which
        is the truth.
        """
        stream.open = False
        for message_id in list(stream.pending):
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "error": {"code": -32000, "message": "call stream closed"},
                    }
                )
                logger.warning(
                    "mcp_elicitation_call_stream_closed_unanswered",
                    session_id=stream.session_id,
                    message_id=message_id,
                )
                self._withdraw(stream.session_id, message_id, None, "call stream closed")
        stream.pending.clear()

    @staticmethod
    def _current_call_stream(session_id: str) -> CallStream | None:
        stream = _CALL_STREAM.get()
        if stream is None or not stream.open or stream.session_id != session_id:
            return None
        return stream

    # -- notifications for the current call (FORGE-465) -----------------

    def _notify_queue(self, session_id: str | None) -> asyncio.Queue[str] | None:
        """Where a notification about the current call would be read.

        The call's own stream first: a notification about a request belongs
        on that request's response, and it is what reaches a client that
        cannot elicit. An open ``GET /mcp`` otherwise.
        """
        if not session_id:
            return None
        call = self._current_call_stream(session_id)
        if call is not None:
            return call.queue
        channel = self._channels.get(session_id)
        if channel is not None and channel.stream_open:
            return channel.queue
        return None

    def can_notify(self, session_id: str | None) -> bool:
        """Whether a notification for the current call would be read now."""
        return self._notify_queue(session_id) is not None

    def notify(self, session_id: str | None, message: dict[str, Any]) -> bool:
        """Put one notification on the current call's stream. False if none."""
        queue = self._notify_queue(session_id)
        if queue is None:
            return False
        queue.put_nowait(json.dumps(message))
        return True

    def available(self, session_id: str | None) -> bool:
        """Whether this session can be asked right now.

        Declared at initialize, and something to ask on: this call's own
        stream (FORGE-464) or an open ``GET /mcp``.
        """
        if not session_id:
            return False
        channel = self._channels.get(session_id)
        if channel is None or not channel.declared:
            return False
        return channel.stream_open or self._current_call_stream(session_id) is not None

    # -- asking and answering ------------------------------------------

    async def elicit(
        self, session_id: str, message: str, requested_schema: dict[str, Any]
    ) -> ElicitResult:
        """Push one ``elicitation/create`` and wait for that session's answer.

        On the session's ``GET /mcp`` stream when one is open, else on the
        call's own stream (FORGE-464).
        """
        channel = self._channels.get(session_id)
        call: CallStream | None = None
        queue: asyncio.Queue[str]
        if channel is not None and channel.stream_open:
            # A client that opened GET /mcp keeps getting its questions
            # there, as FORGE-423 shipped: it is already listening, and
            # moving the question would change what it reads mid-call.
            queue, via = channel.queue, "session_stream"
        elif (call := self._current_call_stream(session_id)) is not None:
            queue, via = call.queue, "call_stream"
        else:
            # The stream went away between the eligibility check and here.
            # Cancel rather than raise: the gate maps it to TIMED_OUT, which
            # is the truth -- nobody was asked, and nobody said no.
            logger.warning("mcp_elicitation_no_stream", session_id=session_id)
            return ElicitResult(ElicitAction.CANCEL)

        self._next += 1
        message_id = f"elicit-{session_id}-{self._next}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        if call is not None:
            call.pending.add(message_id)
        logger.info("mcp_elicitation_sent", session_id=session_id, message_id=message_id, via=via)
        await queue.put(
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
            self._withdraw(session_id, message_id, call, "approval request timed out")
            return ElicitResult(ElicitAction.CANCEL)
        except asyncio.CancelledError:
            # The gate's window ended (FORGE-472), or the call itself was
            # cancelled. Either way the server is no longer waiting, so the
            # form the client is showing has to come down.
            self._pending.pop(message_id, None)
            self._withdraw(session_id, message_id, call, "approval window ended")
            raise
        finally:
            self._pending.pop(message_id, None)
            if call is not None:
                call.pending.discard(message_id)
        return result_from_payload(payload)

    def _withdraw(
        self, session_id: str, message_id: str, call: CallStream | None, reason: str
    ) -> None:
        """Tell the client a question it was asked is no longer wanted.

        FORGE-472. Without this a form stayed on the client long after the
        server had stopped waiting: Claude Code still showed Accept/Decline
        ten minutes after the hold timed out. Sent on the call's own stream
        while it is open, else on an open ``GET /mcp``. The id is remembered
        either way, so an answer that arrives later is recognised as late.
        """
        self._ended[message_id] = reason
        while len(self._ended) > _MAX_ENDED:
            self._ended.popitem(last=False)
        notice = json.dumps(cancelled_notification(message_id, reason))
        channel = self._channels.get(session_id)
        if call is not None and call.open:
            call.queue.put_nowait(notice)
            via = "call_stream"
        elif channel is not None and channel.stream_open:
            channel.queue.put_nowait(notice)
            via = "session_stream"
        else:
            via = None
        logger.info(
            "mcp_elicitation_withdrawn",
            session_id=session_id,
            message_id=message_id,
            reason=reason,
            delivered=via is not None,
            via=via,
        )

    def resolve(self, payload: dict[str, Any]) -> bool:
        """Hand an inbound JSON-RPC response to whoever is waiting for it.

        Returns False when nothing is waiting and the id is not one this hub
        asked, so the transport can treat the body as an ordinary request
        rather than dropping it -- the same contract ``StdioElicitor.resolve``
        keeps, for the same reason.

        An answer to a question already withdrawn (FORGE-472) returns True
        but is never applied: the call it would have approved has already
        ended, and running a write on an answer nobody was waiting for is
        exactly the unreviewed write the gate exists to prevent.
        """
        raw_id = payload.get("id")
        if not isinstance(raw_id, str):
            return False
        future = self._pending.pop(raw_id, None)
        if future is None or future.done():
            reason = self._ended.get(raw_id)
            if reason is None:
                return False
            logger.warning(
                "mcp_elicitation_late_response_ignored",
                message_id=raw_id,
                ended_because=reason,
                action=(payload.get("result") or {}).get("action")
                if isinstance(payload.get("result"), dict)
                else None,
            )
            return True
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


class HttpNotifier:
    """The :class:`mcp_core.approval_hold.HoldNotifier` the HTTP app attaches.

    FORGE-465. Like :class:`HttpElicitor`, one instance for the whole app,
    resolving the session from the active call context when it is used.
    """

    def __init__(self, hub: ElicitationHub) -> None:
        self._hub = hub

    def available(self) -> bool:
        return self._hub.can_notify(HttpElicitor._session())

    def send(self, message: dict[str, Any]) -> bool:
        return self._hub.notify(HttpElicitor._session(), message)


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
