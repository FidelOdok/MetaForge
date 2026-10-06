"""``python -m metaforge.mcp`` — standalone MCP server entrypoint (MET-337).

Boots the unified MCP server (every enabled adapter under one process)
on the chosen transport. Three modes today:

* ``--transport stdio`` (default) — line-delimited JSON-RPC on
  stdin/stdout. The Claude Code default. Writes a ``ready`` log line
  to stderr on launch so subprocess harnesses (MET-340) have a
  deterministic readiness signal.
* ``--transport http`` — minimal FastAPI on ``127.0.0.1`` (configurable
  ``--host``). ``POST /mcp`` accepts a JSON-RPC request body and
  returns the response as JSON.
* ``--transport sse`` — same FastAPI app plus a streaming
  ``GET /mcp/sse`` endpoint that emits each tool-call response as a
  server-sent event. Suitable for Codex / generic harnesses that
  expect SSE.

API-key auth is wired in MET-338 (next ticket); not in scope here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog

if TYPE_CHECKING:
    from twin_core.api import InMemoryTwinAPI
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from mcp_core.auth import AUTH_DENIED, AuthPosture, redact, verify_api_key
from mcp_core.context import HEADER_SESSION
from mcp_core.elicitation import (
    ElicitAction,
    ElicitResult,
    cancelled_notification,
    result_from_payload,
)
from mcp_core.guardrails import Caller
from mcp_core.protocol import AUTH_DENIED as AUTH_DENIED_CODE
from mcp_core.service_auth import HEADER_SERVICE_KEY, ServiceRunVerifier
from metaforge.mcp.http_elicitation import (
    ElicitationHub,
    HttpElicitor,
    HttpNotifier,
    is_jsonrpc_response,
    session_uuid,
)
from metaforge.mcp.oauth import OAuthError, OAuthProvider
from metaforge.mcp.server import UnifiedMcpServer, build_unified_server
from observability.metrics import collector_for

logger = structlog.get_logger("metaforge.mcp")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m metaforge.mcp",
        description="MetaForge unified MCP server — stdio + HTTP/SSE transports.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "sse"),
        default="stdio",
        help="Transport to bind to (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind host for http/sse transports (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Bind port for http/sse transports (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--adapters",
        default=None,
        help=(
            "Comma-separated adapter id allow-list "
            "(e.g. ``cadquery,calculix``). Default: every enabled adapter."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=(
            "Serve only one tool profile (core, mechanical, simulation, "
            "electronics, robotics). Every client caps how many tools it "
            "carries, and most drop the overflow without saying so, which "
            "leaves the model behaving as though the missing capability does "
            "not exist. A profile keeps the set inside that cap on purpose. "
            "Default: every registered tool (FORGE-339)."
        ),
    )
    parser.add_argument(
        "--allow-twin-mutations",
        action="store_true",
        default=False,
        help=(
            "Permit mutating Cypher (CREATE / MERGE / SET / DELETE) through "
            "``twin.query_cypher`` so work-products can be created and the "
            "digital thread built over MCP (MET-488). Off by default; every "
            "mutating call is audit-logged. Do NOT enable on a publicly "
            "reachable endpoint without API-key auth."
        ),
    )
    parser.add_argument(
        "--capture-sessions",
        action="store_true",
        default=False,
        help=(
            "Record every tool call as an action event in an agent session "
            "so MCP/CLI work shows up in /sessions with no client cooperation "
            "(MET-496). Requires a DATABASE_URL-backed session store; degrades "
            "to a no-op without one."
        ),
    )
    return parser.parse_args(argv)


def _adapter_ids_from_args(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [a.strip() for a in raw.split(",") if a.strip()]


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------


def _auth_error_response(request_id: str, reason: str) -> str:
    """JSON-RPC error envelope for an auth failure."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                # FORGE-388: the shared table, not a literal. -32002 is
                # the spec's "Resource not found"; a rejected credential
                # is AUTH_DENIED.
                "code": AUTH_DENIED_CODE,
                "message": "Authentication failed",
                "data": {"error_type": AUTH_DENIED, "reason": reason},
            },
        }
    )


def _stdio_auth_check() -> tuple[bool, str]:
    """Enforce API-key auth at stdio launch (MET-338).

    Returns ``(ok, reason)``. ``ok=False`` means the caller should
    write a single ``auth_error`` JSON-RPC message and exit.
    """
    expected = os.environ.get("METAFORGE_MCP_API_KEY") or ""
    if not expected:
        return True, "open_mode"
    provided = os.environ.get("METAFORGE_MCP_CLIENT_KEY") or ""
    result = verify_api_key(provided, expected)
    if not result.ok:
        logger.warning(
            "mcp_auth_denied",
            transport="stdio",
            reason=result.reason,
            redacted=result.redacted or redact(provided),
        )
        return False, result.reason
    logger.info("mcp_auth_ok", transport="stdio", redacted=result.redacted)
    return True, "match"


# MET-450: default stdio readline cap. 16 MiB is generous for text
# ingest (real payloads run 10-500 KB; PDFs ride in via filesystem
# paths, not inline bytes). Bumped from the asyncio default of 64 KiB
# which crashed the loop mid-readline on any real datasheet ingest.
_DEFAULT_STDIO_MAX_LINE_BYTES = 16 * 1024 * 1024


def _stdio_max_line_bytes() -> int:
    """Return the asyncio StreamReader ``limit`` for stdio reads (MET-450).

    Reads ``METAFORGE_MCP_MAX_LINE_BYTES`` from env to let ops cap or
    raise the ceiling without code changes; falls back to 16 MiB. A
    non-positive / unparseable value falls back to the default so a
    misconfigured env can't deadlock the stdio loop with a 0-byte cap.
    """
    import os

    raw = os.environ.get("METAFORGE_MCP_MAX_LINE_BYTES", "").strip()
    if not raw:
        return _DEFAULT_STDIO_MAX_LINE_BYTES
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "mcp_stdio_max_line_bytes_invalid",
            value=raw,
            fallback=_DEFAULT_STDIO_MAX_LINE_BYTES,
        )
        return _DEFAULT_STDIO_MAX_LINE_BYTES
    if value <= 0:
        logger.warning(
            "mcp_stdio_max_line_bytes_non_positive",
            value=value,
            fallback=_DEFAULT_STDIO_MAX_LINE_BYTES,
        )
        return _DEFAULT_STDIO_MAX_LINE_BYTES
    return value


class StdioElicitor:
    """Ask the connected client a question over stdio (FORGE-360).

    stdio is a single duplex pipe, so a server-to-client request is just a
    JSON-RPC request written to stdout with an id, and the answer is
    whichever inbound line carries that id. The read loop routes it here;
    this class only owns the correlation and the wait.

    A client that never answers is not a refusal. The timeout resolves to
    ``cancel``, which :func:`mcp_core.elicitation.elicitation_gate` maps to
    ``TIMED_OUT`` -- so the agent is told nobody looked, rather than that a
    reviewer said no.
    """

    def __init__(
        self,
        write: Callable[[str], None],
        *,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._write = write
        self._timeout = timeout_seconds
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        #: Questions withdrawn (FORGE-472), so a late answer is ignored.
        self._ended: set[str] = set()
        self._next = 0

    def resolve(self, message_id: str, payload: dict[str, Any]) -> bool:
        """Hand an inbound response to whoever is waiting for it.

        Returns False when nothing is waiting, so the caller can treat the
        line as an ordinary request rather than dropping it silently. A late
        answer to a withdrawn question (FORGE-472) is consumed and ignored.
        """
        future = self._pending.pop(message_id, None)
        if future is None or future.done():
            if message_id in self._ended:
                logger.warning("mcp_elicitation_late_response_ignored", message_id=message_id)
                return True
            return False
        future.set_result(payload)
        return True

    async def __call__(self, message: str, requested_schema: dict[str, Any]) -> ElicitResult:
        self._next += 1
        message_id = f"elicit-{self._next}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        self._write(
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
            self._withdraw(message_id, "approval request timed out")
            return ElicitResult(ElicitAction.CANCEL)
        except asyncio.CancelledError:
            self._pending.pop(message_id, None)
            self._withdraw(message_id, "approval window ended")
            raise

        # FORGE-423: shared with the HTTP elicitor. The three defaults here
        # are the easy part to get wrong, and getting them wrong on one
        # transport and not the other is worse than getting them wrong on
        # both.
        return result_from_payload(payload)

    def _withdraw(self, message_id: str, reason: str) -> None:
        """Take the question down on the client (FORGE-472)."""
        if len(self._ended) > 256:
            self._ended.clear()
        self._ended.add(message_id)
        self._write(json.dumps(cancelled_notification(message_id, reason)))
        logger.info("mcp_elicitation_withdrawn", message_id=message_id, reason=reason)


def _is_response(raw: str) -> tuple[bool, str, dict[str, Any]]:
    """Is this inbound line an answer to something *we* asked?

    A JSON-RPC response carries an id and a result/error and no method. The
    stdio loop treated every line as a request, which was correct while the
    traffic was one-directional and becomes a silent misroute the moment it
    is not: our own answer would have been dispatched as a method call and
    come back as "Unknown method: None".

    Anything unparseable is reported as "not a response" so it still reaches
    ``handle_request``, which already turns it into a proper parse error.
    """
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return False, "", {}
    if not isinstance(msg, dict) or "method" in msg:
        return False, "", {}
    if "result" not in msg and "error" not in msg:
        return False, "", {}
    message_id = msg.get("id")
    if not isinstance(message_id, str):
        return False, "", {}
    return True, message_id, msg


async def run_stdio(server: UnifiedMcpServer) -> None:
    """Read line-delimited JSON-RPC requests from stdin; reply on stdout.

    Mirrors the per-adapter pattern in
    ``tool_registry.mcp_server.server.McpToolServer.start_stdio`` so
    transport semantics stay consistent across the codebase.

    MET-338: API-key auth happens once at launch — stdio is a single
    persistent channel from one client, so checking the env-supplied
    key at startup matches the spec ("require key in env at spawn
    time"). Mismatch emits a single auth_error response on stdout
    and the process exits, mirroring the contract MCP harnesses
    expect on rejection.
    """
    ok, reason = _stdio_auth_check()
    if not ok:
        sys.stdout.write(_auth_error_response("auth", reason) + "\n")
        sys.stdout.flush()
        return
    # FORGE-332: the check above already knows whether a key was in force
    # (`reason == "open_mode"` means there was none). Tell the server, so
    # health/check can say so too -- stdio launched from a plugin manifest
    # is the commonest way to end up in open mode without deciding to.
    server.declare_auth_posture(
        AuthPosture(api_key=reason != "open_mode", oauth=False, transport="stdio")
    )
    # FORGE-387: stdio is a subprocess this user launched on their own
    # machine, so it really is them. Declared rather than defaulted, now
    # that the default is the conservative one.
    server.declare_caller(Caller.LOCAL)

    # MET-387: stdio installs the call context from env vars at boot —
    # one stdio process = one harness session, so a single context
    # applies to every subsequent request on this stream.
    from mcp_core.context import context_from_env, set_context

    set_context(context_from_env())

    logger.info(
        "mcp_stdio_ready",
        adapter_count=len(server.adapters),
        tool_count=len(server.tool_ids),
    )
    # MET-340 looks for this exact line on stderr to know the
    # subprocess is alive before it pushes the first request.
    print("metaforge-mcp ready", file=sys.stderr, flush=True)

    loop = asyncio.get_event_loop()
    # MET-450: ``asyncio.StreamReader``'s default ``limit`` is 64 KiB
    # (``2**16``). A single ``knowledge.ingest`` JSON-RPC request line
    # easily exceeds that — the ESP32-WROOM-32 fixture is ~61 KB, real
    # datasheet payloads run 10-500 KB. Default behaviour was a hard
    # ``ValueError`` mid-``readline()`` that killed the stdio loop with
    # no JSON-RPC response, collapsing the SSH-piped harness. Bump to
    # 16 MiB by default; ``METAFORGE_MCP_MAX_LINE_BYTES`` lets ops
    # tighten or loosen the cap without code changes.
    max_line_bytes = _stdio_max_line_bytes()
    reader = asyncio.StreamReader(limit=max_line_bytes)
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    # FORGE-360: stdout now has two writers -- replies to the client's
    # requests, and our own elicitation requests -- so framing needs a lock.
    # Interleaved partial lines would corrupt the stream for both.
    write_lock = asyncio.Lock()

    async def write_line(text: str) -> None:
        async with write_lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    pending_writes: set[asyncio.Task[None]] = set()

    def write_soon(text: str) -> None:
        task = asyncio.ensure_future(write_line(text))
        pending_writes.add(task)
        task.add_done_callback(pending_writes.discard)

    elicitor = StdioElicitor(write_soon)
    server.attach_elicitor(elicitor)
    server.attach_notifier(_StdioNotifier(write_soon))

    in_flight: set[asyncio.Task[None]] = set()

    async def dispatch(raw: str) -> None:
        response = await server.handle_request(raw)
        # JSON-RPC notifications return an empty body — writing a
        # blank line breaks the client's JSON line framing.
        if response:
            await write_line(response)

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            raw = line.decode("utf-8").strip()
            if not raw:
                continue
            # Our own answer, not a new request. Before elicitation the
            # stream was one-directional and this could not happen.
            is_response, message_id, payload = _is_response(raw)
            if is_response and elicitor.resolve(message_id, payload):
                continue
            # Dispatched as a task rather than awaited. A call held for
            # approval waits on an elicitation response that arrives on
            # *this* stream: awaiting it here means never reading the line
            # that would release it, which is a deadlock rather than a
            # slow approval.
            task = asyncio.ensure_future(dispatch(raw))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
    except asyncio.CancelledError:
        pass
    finally:
        # Let anything mid-flight finish writing before the pipe closes,
        # so a client does not see a truncated reply on shutdown.
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        if pending_writes:
            await asyncio.gather(*pending_writes, return_exceptions=True)
        transport.close()
        logger.info("mcp_stdio_stopped")


# ---------------------------------------------------------------------------
# HTTP / SSE transport
# ---------------------------------------------------------------------------


#: The session header the Streamable HTTP transport defines. Distinct from
#: MetaForge's own ``X-MetaForge-Session``: that one is ours and a client has
#: to be told about it, this one every spec-compliant client already sends
#: back once the server has issued it.
MCP_SESSION_HEADER = "Mcp-Session-Id"


def _is_initialize(raw_body: bytes) -> bool:
    """Whether this POST body is the ``initialize`` request.

    Unparseable bodies answer False: ``handle_request`` turns them into a
    proper JSON-RPC parse error, and minting a session for a request that
    was never valid would leave a binding nothing will ever claim.
    """
    try:
        message = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(message, dict) and message.get("method") == "initialize"


def _session_for_request(headers: dict[str, str], raw_body: bytes) -> str | None:
    """The session id this request belongs to, minting one at initialize.

    Returns None when there is nothing to do -- a non-initialize request
    from a client that is not carrying a session. That client keeps the old
    behaviour (a fresh context per call, and ``session.start`` telling it so)
    rather than being handed a session it never asked for and will not echo.
    """
    folded = {k.lower(): v for k, v in headers.items()}
    existing = folded.get(MCP_SESSION_HEADER.lower())
    if existing:
        # Already in a session. Only accept a well-formed one: our context
        # keys bindings by UUID, and a value we cannot parse would silently
        # become "no session" one layer down.
        try:
            UUID(existing)
        except ValueError:
            logger.warning("mcp_session_header_unparseable", value=existing[:64])
            return None
        return existing
    if _is_initialize(raw_body):
        return str(uuid4())
    return None


def _has_progress_token(payload: dict[str, Any]) -> bool:
    """Whether a request asked for progress (``params._meta.progressToken``)."""
    params = payload.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return isinstance(meta, dict) and meta.get("progressToken") is not None


class _StdioNotifier:
    """Notifications for the stdio client (FORGE-465). stdout always reaches it."""

    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write

    def available(self) -> bool:
        return True

    def send(self, message: dict[str, Any]) -> bool:
        self._write(json.dumps(message))
        return True


def _accepts_event_stream(accept: str | None) -> bool:
    """Whether the client said it can read an SSE response (FORGE-464).

    Explicit only. ``*/*`` is what a client sends when it has not thought
    about it, and answering that with a stream would hand a plain JSON
    client something it cannot parse.
    """
    if not accept:
        return False
    return any(
        part.split(";", 1)[0].strip().lower() == "text/event-stream" for part in accept.split(",")
    )


def build_http_app(
    server: UnifiedMcpServer,
    *,
    enable_sse: bool,
    api_key: str | None = None,
    oauth: OAuthProvider | None = None,
    service_key: str | None = None,
    service_verifier: ServiceRunVerifier | None = None,
) -> Any:
    """Construct a FastAPI app exposing the unified server.

    Defined as a function (not module-level) so callers can build a
    fresh app per test without binding a port. Lazy imports keep the
    stdio path free of FastAPI cost when running as a Claude Code
    subprocess.

    MET-338: when ``api_key`` is non-empty, every request to
    ``/mcp`` and ``/mcp/sse`` must carry ``Authorization: Bearer <key>``.
    ``/health`` is exempt — readiness checks must work without
    credentials so orchestrators can probe the server.

    MET-480: when ``oauth`` is provided, the app also serves an OAuth 2.1
    + PKCE authorization server (``/.well-known/*``, ``/register``,
    ``/authorize``, ``/token``) and ``/mcp`` accepts a valid OAuth bearer
    token **or** the static key. This is what the claude.ai web connector
    requires — it cannot send a static bearer header.

    FORGE-332: whichever of those two is live, the server is told, so
    ``health/check`` reports the auth mode instead of leaving
    /metaforge:doctor to guess. Open mode is what an unset
    ``METAFORGE_MCP_API_KEY`` gives you, so it is the state most likely to
    be in force without anyone having chosen it.
    """
    identifies = bool(oauth and oauth.config.verified_identity)
    server.declare_auth_posture(
        AuthPosture(
            api_key=bool(api_key),
            oauth=oauth is not None and oauth.config.enabled,
            transport="http",
            identifies_caller=identifies,
        )
    )
    # FORGE-387: an HTTP caller is not the engineer at the keyboard, whether
    # they are next door or through a tunnel. REMOTE only when the login
    # establishes who they are; a shared credential, or none, is UNTRUSTED.
    # Both hold writes -- the difference is what a reviewer is told.
    server.declare_caller(Caller.REMOTE if identifies else Caller.UNTRUSTED)
    # FORGE-487: the design-flow worker is the one HTTP caller that can be
    # something other than untrusted/remote, and only per request: it must
    # present ``service_key`` AND name a run the gateway confirms. Off unless
    # both are supplied, in any auth mode.
    server.attach_service_auth(service_key, service_verifier)
    app = FastAPI(
        title="MetaForge MCP",
        version="0.1.0",
        description=(
            "Unified MCP server aggregating every MetaForge tool adapter. "
            "POST /mcp with a JSON-RPC body. /mcp/sse streams responses "
            "as server-sent events when ``--transport sse`` is enabled."
        ),
    )

    def _issuer_for(request: Request) -> str:
        """Public base URL of this server.

        Behind the Cloudflare tunnel (MET-482) TLS is terminated at the
        edge, so the request the app sees is plain HTTP — we trust the
        ``X-Forwarded-*`` headers Cloudflare sets to reconstruct the
        public ``https://host`` the client actually used. An explicit
        ``METAFORGE_OAUTH_ISSUER`` always wins.
        """
        if oauth and oauth.config.issuer:
            return oauth.config.issuer.rstrip("/")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            return f"{proto}://{host}".rstrip("/")
        return str(request.base_url).rstrip("/")

    def _bearer(authorization: str | None) -> str | None:
        if authorization and authorization.lower().startswith("bearer "):
            return authorization.split(None, 1)[1].strip()
        return None

    def _check_auth(request: Request, authorization: str | None) -> str | None:
        """Accept the static API key (MET-338) OR an OAuth token (MET-480).

        Open mode (no key, no OAuth) passes everything. When either
        mechanism is configured, an unauthenticated request gets a 401
        carrying ``WWW-Authenticate: Bearer`` with a pointer to the
        protected-resource metadata so the claude.ai connector can begin
        the OAuth dance.
        """
        provided = _bearer(authorization)
        oauth_on = oauth is not None and oauth.config.enabled

        # OAuth token path (only when configured).
        #
        # FORGE-330: the actor this token is bound to used to be thrown away
        # here — `validate_token` returns it and the call site tested it for
        # truthiness. Everything downstream then took `actor_id` from the
        # client-supplied X-MetaForge-Actor header instead, so over HTTP a
        # caller could claim to be anyone and that is what landed in the
        # session record. Self-asserted attribution is not an audit trail.
        if oauth is not None and oauth_on and provided:
            actor = oauth.validate_token(provided)
            if actor:
                return actor

        # Static API-key path — authoritative only when a key is set.
        reason = "invalid_token"
        if api_key:
            result = verify_api_key(provided, api_key)
            if result.ok:
                # A shared API key authorises the call but identifies nobody.
                # Returning None keeps that distinction: authorised is not
                # the same as attributable.
                return None
            reason = result.reason
        elif not oauth_on:
            # Neither mechanism configured → open mode, everything passes.
            return None

        # At least one mechanism is on and the request failed it.
        logger.warning(
            "mcp_auth_denied",
            transport="http",
            reason=reason,
            oauth_enabled=oauth_on,
            redacted=redact(provided or ""),
        )
        headers: dict[str, str] = {}
        if oauth and oauth.config.enabled:
            meta_url = f"{_issuer_for(request)}/.well-known/oauth-protected-resource"
            headers["WWW-Authenticate"] = f'Bearer resource_metadata="{meta_url}"'
        raise HTTPException(
            status_code=401,
            detail={"error_type": AUTH_DENIED, "reason": reason},
            headers=headers or None,
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        raw = await server.handle_request(
            '{"jsonrpc":"2.0","id":"health","method":"health/check","params":{}}'
        )
        body = json.loads(raw)
        return JSONResponse(body.get("result", body))

    # FORGE-423: the server-to-client direction. Without it `can_elicit` is
    # false for every HTTP client -- which is every plugin -- so FORGE-360's
    # inline approvals existed only on stdio and every held write went to the
    # dashboard queue.
    hub = ElicitationHub()
    server.attach_elicitor(HttpElicitor(hub))
    server.attach_notifier(HttpNotifier(hub))

    @app.get("/mcp")
    async def mcp_stream(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        """The long-lived SSE stream the server pushes requests down.

        Distinct from ``GET /mcp/sse``, which is a request/response
        convenience (queue work as ``?request=`` params, server closes with
        ``event: done``). This one carries server-initiated messages and
        stays open, which is what the Streamable HTTP spec means by the
        server-to-client direction.

        A stream with no session id is refused rather than opened on an
        invented one: an approval pushed down a stream nobody can be
        correlated with is a question asked into the void.
        """
        _check_auth(request, authorization)
        session_id = request.headers.get(MCP_SESSION_HEADER)
        if session_uuid(session_id) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{MCP_SESSION_HEADER} is required to open the event stream, and "
                    "must be the id this server issued at initialize. Without it an "
                    "approval request cannot be routed back to the connection that "
                    "asked for it."
                ),
            )
        assert session_id is not None  # narrowed by session_uuid above
        return StreamingResponse(
            hub.stream(session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/mcp")
    async def mcp_post(
        request: Request,
        authorization: str | None = Header(default=None),
        # FORGE-422: `Response`, not `JSONResponse` -- a 204 is returned as a
        # bare Response (no body), and JSONResponse is a subclass, so this
        # widens to cover both rather than the 204 being forced into a shape
        # that must carry one.
    ) -> Response:
        verified_actor = _check_auth(request, authorization)
        raw_body = await request.body()
        # MET-387: install per-request McpCallContext from headers so
        # downstream handlers see the project / actor / session via
        # ``current_context()``.
        from mcp_core.context import context_from_headers, with_context

        headers = dict(request.headers)
        # FORGE-334: adopt the transport's own session. The Streamable HTTP
        # spec says a server MAY assign a session id on the InitializeResult
        # via ``Mcp-Session-Id``, and that a client which receives one MUST
        # echo it on every subsequent request. We were issuing none, so every
        # HTTP client without our proprietary X-MetaForge-Session header got
        # a freshly invented session per call -- which is why a project
        # picked with ``/metaforge:use`` did not stick for them.
        issued_session = _session_for_request(headers, raw_body)
        if issued_session is not None:
            headers[HEADER_SESSION] = issued_session
        ctx = context_from_headers(headers)
        # FORGE-410: `?profile=core` on the MCP URL. A plugin manifest can set
        # it where it cannot pass a command-line flag, and one sidecar then
        # serves a capped set to the plugin and everything to the dashboard --
        # rather than a single `--profile` shrinking the tool list for every
        # consumer at once.
        requested_profile = request.query_params.get("profile")
        if requested_profile:
            ctx = ctx.model_copy(update={"profile": requested_profile.strip()})
        # FORGE-330: a verified token outranks whatever the client put in
        # X-MetaForge-Actor. The header is a convenience for unauthenticated
        # local use; it must never be able to overwrite an identity the
        # server established cryptographically, or the audit trail records
        # whoever the caller said they were.
        if verified_actor:
            # FORGE-330: the actor comes from a token this server issued,
            # so it outranks the header -- but "issued by us" is not the
            # same as "we know who this is". With the shared-secret login
            # anyone holding the secret can type any name, so
            # actor_verified stays False and the name is a label on the
            # timeline. An upstream identity provider is what makes it an
            # identity; `OAuthConfig.verified_identity` is where that
            # switches on.
            proven = bool(oauth and oauth.config.verified_identity)
            ctx = ctx.model_copy(update={"actor_id": verified_actor, "actor_verified": proven})
        # FORGE-423: the client answering something *we* asked. Routed by id
        # rather than dispatched -- sending our own answer to handle_request
        # comes back as "Unknown method: None", the same misroute the stdio
        # loop had to learn to avoid.
        try:
            inbound = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            inbound = None
        if is_jsonrpc_response(inbound) and hub.resolve(inbound):
            # Accepted, and there is nothing to say back.
            return Response(status_code=202)

        # FORGE-487: the design-flow worker's calls. After the answer-routing
        # above, which must not be delayed by a gateway lookup, and before the
        # context is used anywhere (including the SSE path below).
        ctx = await server.authenticate_service_caller(
            ctx, request.headers.get(HEADER_SERVICE_KEY), inbound
        )

        # FORGE-464: a tools/call that may be held is answered on its own SSE
        # stream when the client can read one, so the approval question can
        # travel on the call it is about. Claude Code never opens GET /mcp,
        # so without this every held write it made went to the dashboard.
        #
        # FORGE-465: a call carrying a progressToken gets the stream too,
        # whatever the client can elicit, so a write held for the dashboard
        # can say so on it. Nothing switches to SSE unless something is sent.
        if (
            issued_session is not None
            and isinstance(inbound, dict)
            and inbound.get("method") == "tools/call"
            and _accepts_event_stream(request.headers.get("accept"))
            and (hub.call_stream_allowed(issued_session) or _has_progress_token(inbound))
        ):
            return await _call_with_stream(raw_body.decode("utf-8"), ctx, issued_session)

        with with_context(ctx):
            response = await server.handle_request(raw_body.decode("utf-8"))
        # FORGE-423: record what this session declared, from its own
        # handshake. The server's copy is whichever client initialised last,
        # and routing an approval on that would ask the wrong person.
        if issued_session is not None and _is_initialize(raw_body):
            negotiated = ""
            try:
                negotiated = str(json.loads(response)["result"]["protocolVersion"])
            except (ValueError, KeyError, TypeError):
                pass
            hub.note_initialize(
                issued_session,
                capabilities=(inbound or {}).get("params", {}).get("capabilities"),
                negotiated_protocol=negotiated,
            )
        # JSON-RPC notifications produce no body — return 204 so the
        # client doesn't try to json-parse an empty string.
        #
        # FORGE-422: a bare Response, not JSONResponse(content=None). The
        # latter serialises None to b"null" -- four bytes on a status that
        # MUST carry none -- so uvicorn sets Content-Length 0 and then raises
        # "Response content longer than Content-Length". The client still got
        # its 204, so it looked fine from outside while the server logged an
        # ASGI traceback on every connection: `notifications/initialized` is
        # the first thing a spec-compliant client sends.
        return _json_reply(response, issued_session)

    def _json_reply(response: str, session_id: str | None) -> Response:
        if not response:
            return Response(status_code=204)
        out = JSONResponse(json.loads(response))
        if session_id is not None:
            out.headers[MCP_SESSION_HEADER] = session_id
        return out

    #: Handler tasks whose POST stream closed before they finished. Held so
    #: the loop does not garbage-collect a running call; the done callback
    #: lets each one go.
    detached: set[asyncio.Task[str]] = set()

    async def _call_with_stream(body: str, ctx: Any, session_id: str) -> Response:
        """Run one tools/call, switching to SSE only if it asks a question.

        FORGE-464. Most calls are never held, and those keep the plain JSON
        response they always had: the handler runs as a task and the
        transport waits for whichever comes first, the result or a message
        pushed onto this call's stream. A result first means nothing was
        asked, so it is returned as JSON. A message first means the call is
        waiting on the client, and the response becomes the stream: the
        question, then (once the client POSTs its answer, routed by id) the
        result, then the stream closes.
        """
        from mcp_core.context import with_context

        with with_context(ctx), hub.call_stream(session_id) as call:
            task: asyncio.Task[str] = asyncio.create_task(server.handle_request(body))
        first: asyncio.Task[str] = asyncio.create_task(call.queue.get())
        await asyncio.wait({task, first}, return_when=asyncio.FIRST_COMPLETED)
        if not first.done():
            first.cancel()
            hub.close_call_stream(call)
            return _json_reply(task.result(), session_id)

        logger.info("mcp_call_stream_opened", session_id=session_id)

        def frame(message: str) -> bytes:
            return f"event: message\ndata: {message}\n\n".encode()

        async def events() -> AsyncIterator[bytes]:
            try:
                yield frame(first.result())
                while True:
                    getter: asyncio.Task[str] = asyncio.create_task(call.queue.get())
                    await asyncio.wait(
                        {task, getter}, timeout=15.0, return_when=asyncio.FIRST_COMPLETED
                    )
                    if getter.done():
                        yield frame(getter.result())
                        continue
                    getter.cancel()
                    if not task.done():
                        # A reviewer is still reading. Keep intermediaries
                        # from reaping a connection that looks idle.
                        yield b": keep-alive\n\n"
                        continue
                    while not call.queue.empty():
                        yield frame(call.queue.get_nowait())
                    break
                hub.close_call_stream(call)
                result = task.result()
                if result:
                    # Re-serialised so the event is one line whatever the
                    # handler's formatting.
                    yield frame(json.dumps(json.loads(result)))
                logger.info("mcp_call_stream_completed", session_id=session_id)
            finally:
                # Normal end, or the client hung up mid-question. Either way
                # nothing more can be read here, so an unanswered question is
                # cancelled now rather than at the elicitation timeout.
                hub.close_call_stream(call)
                if not task.done():
                    logger.warning("mcp_call_stream_client_gone", session_id=session_id)
                    detached.add(task)
                    task.add_done_callback(detached.discard)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                MCP_SESSION_HEADER: session_id,
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.delete("/mcp")
    async def mcp_delete(request: Request) -> Response:  # FORGE-422: 204 has no body
        """Explicit session termination, per the Streamable HTTP spec.

        A client that is done SHOULD send DELETE with its ``Mcp-Session-Id``.
        Releasing the project binding here is what keeps a long-lived sidecar
        from holding one for a client that has gone away -- the registry is
        capped and evicts oldest-first, so without this a departed client's
        binding can outlive an active client's.
        """
        from uuid import UUID

        from mcp_core.context import clear_session_project

        raw = request.headers.get(MCP_SESSION_HEADER)
        if not raw:
            return JSONResponse({"error": "Mcp-Session-Id required"}, status_code=400)
        try:
            clear_session_project(UUID(raw))
        except ValueError:
            return JSONResponse({"error": "Mcp-Session-Id is not a known session"}, status_code=404)
        logger.info("mcp_session_terminated", session_id=raw)
        # FORGE-422: same reason as the notification path above -- a 204
        # must not carry a body.
        return Response(status_code=204)

    if enable_sse:

        @app.get("/mcp/sse")
        async def mcp_sse(
            request: Request,
            authorization: str | None = Header(default=None),
        ) -> StreamingResponse:
            """Stream tool-call results as server-sent events.

            The client sends one or more JSON-RPC requests as query
            params (``request=<urlencoded JSON>``) — repeat the param to
            queue multiple. Each response is emitted as a separate
            ``data:`` event so generic SSE clients can consume them.
            """
            _check_auth(request, authorization)
            queries = request.query_params.getlist("request")
            # MET-387: install per-stream context from headers; every
            # queued request runs under the same ctx (one SSE connection
            # = one harness session).
            from mcp_core.context import context_from_headers, with_context

            ctx = context_from_headers(dict(request.headers))

            async def _events() -> AsyncIterator[bytes]:
                with with_context(ctx):
                    for raw in queries:
                        response = await server.handle_request(raw)
                        yield f"event: response\ndata: {response}\n\n".encode()
                    yield b"event: done\ndata: \n\n"

            return StreamingResponse(
                _events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

    # -- OAuth 2.1 + PKCE authorization server (MET-480) -------------------
    # Only mounted when configured. These endpoints are intentionally
    # unauthenticated — they ARE the auth handshake the claude.ai connector
    # runs before it can present a bearer token to /mcp.
    if oauth and oauth.config.enabled:
        _mount_oauth_routes(app, oauth, _issuer_for)

    return app


def _form_params(raw: bytes) -> dict[str, str]:
    """Parse an ``application/x-www-form-urlencoded`` body into a flat map.

    Parsed by hand so the OAuth endpoints don't pull in ``python-multipart``
    just to read a handful of fields.
    """
    from urllib.parse import parse_qsl

    return dict(parse_qsl(raw.decode("utf-8")))


def _login_page(action: str, fields: dict[str, str], *, error: str = "") -> str:
    """Minimal shared-secret login form for ``/authorize``.

    Carries the original OAuth request parameters as hidden inputs so the
    POST can re-validate and mint the code without server-side session
    state.
    """
    from html import escape

    hidden = "\n".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(v)}">'
        for k, v in fields.items()
        if v
    )
    banner = f'<p class="err">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MetaForge MCP — Sign in</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0b0f14; color:#e6edf3;
         display:grid; place-items:center; height:100vh; margin:0; }}
  form {{ background:#161b22; padding:2rem; border-radius:12px; width:min(360px,90vw);
          box-shadow:0 8px 32px rgba(0,0,0,.4); }}
  h1 {{ font-size:1.1rem; margin:0 0 1rem; }}
  input[type=password] {{ width:100%; padding:.6rem; border-radius:8px;
          border:1px solid #30363d; background:#0d1117; color:#e6edf3; box-sizing:border-box; }}
  button {{ margin-top:1rem; width:100%; padding:.6rem; border:0; border-radius:8px;
          background:#2f81f7; color:#fff; font-weight:600; cursor:pointer; }}
  .err {{ color:#f85149; font-size:.85rem; }}
</style></head>
<body>
<form method="post" action="{action}">
  <h1>Authorize MetaForge MCP</h1>
  {banner}
  <label>Your name<br>
    <input type="text" name="operator" autofocus placeholder="e.g. ana"
           autocomplete="username">
  </label>
  <p style="font-size:.8rem;opacity:.7;margin:.2rem 0 .8rem">
    Recorded against your work so a reviewer can see who did what. This
    login is a shared secret, so the name is not verified.
  </p>
  <label>Access secret<br>
    <input type="password" name="login_secret" required>
  </label>
  {hidden}
  <button type="submit">Authorize</button>
</form>
</body></html>"""


def _mount_oauth_routes(
    app: Any,
    oauth: OAuthProvider,
    issuer_for: Callable[[Request], str],
) -> None:
    from urllib.parse import urlencode

    @app.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource(request: Request) -> JSONResponse:
        return JSONResponse(oauth.protected_resource_metadata(issuer_for(request)))

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_authorization_server(request: Request) -> JSONResponse:
        return JSONResponse(oauth.authorization_server_metadata(issuer_for(request)))

    @app.post("/register")
    async def oauth_register(request: Request) -> JSONResponse:
        try:
            metadata = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail={"error": "invalid_request"}) from exc
        try:
            registration = oauth.register_client(metadata)
        except OAuthError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        logger.info("oauth_client_registered", client_id=registration["client_id"])
        return JSONResponse(registration, status_code=201)

    def _authorize_fields(params: dict[str, str]) -> dict[str, str]:
        keys = (
            "client_id",
            "redirect_uri",
            "response_type",
            "code_challenge",
            "code_challenge_method",
            "scope",
            "state",
        )
        return {k: params.get(k, "") for k in keys}

    @app.get("/authorize")
    async def oauth_authorize_get(request: Request) -> Any:
        params = dict(request.query_params)
        try:
            oauth.validate_authorize(
                client_id=params.get("client_id"),
                redirect_uri=params.get("redirect_uri"),
                response_type=params.get("response_type"),
                code_challenge=params.get("code_challenge"),
                code_challenge_method=params.get("code_challenge_method"),
                scope=params.get("scope"),
            )
        except OAuthError as exc:
            return _authorize_error(exc, params)
        return HTMLResponse(_login_page("/authorize", _authorize_fields(params)))

    @app.post("/authorize")
    async def oauth_authorize_post(request: Request) -> Any:
        params = _form_params(await request.body())
        try:
            client = oauth.validate_authorize(
                client_id=params.get("client_id"),
                redirect_uri=params.get("redirect_uri"),
                response_type=params.get("response_type"),
                code_challenge=params.get("code_challenge"),
                code_challenge_method=params.get("code_challenge_method"),
                scope=params.get("scope"),
            )
        except OAuthError as exc:
            return _authorize_error(exc, params)
        if not oauth.verify_login(params.get("login_secret")):
            logger.warning("oauth_login_denied", client_id=params.get("client_id"))
            return HTMLResponse(
                _login_page("/authorize", _authorize_fields(params), error="Invalid secret"),
                status_code=401,
            )
        redirect_uri = params["redirect_uri"]
        code = oauth.issue_code(
            client,
            redirect_uri,
            params["code_challenge"],
            params.get("scope"),
            operator=params.get("operator"),
        )
        query = {"code": code}
        if params.get("state"):
            query["state"] = params["state"]
        logger.info("oauth_code_issued", client_id=client.client_id)
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(query)}", status_code=302)

    def _authorize_error(exc: OAuthError, params: dict[str, str]) -> Any:
        # Redirectable errors bounce back to the (already-validated)
        # redirect_uri per RFC 6749 §4.1.2.1; the rest render inline.
        if exc.redirectable and params.get("redirect_uri"):
            query = {"error": exc.error}
            if exc.description:
                query["error_description"] = exc.description
            if params.get("state"):
                query["state"] = params["state"]
            redirect_uri = params["redirect_uri"]
            sep = "&" if "?" in redirect_uri else "?"
            return RedirectResponse(f"{redirect_uri}{sep}{urlencode(query)}", status_code=302)
        return JSONResponse(exc.as_dict(), status_code=exc.status)

    @app.post("/token")
    async def oauth_token(request: Request) -> JSONResponse:
        params = _form_params(await request.body())
        try:
            tokens = oauth.exchange(params)
        except OAuthError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        logger.info("oauth_token_issued", grant_type=params.get("grant_type"))
        return JSONResponse(
            tokens,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )


def run_http(server: UnifiedMcpServer, host: str, port: int, *, enable_sse: bool) -> None:
    """Block on uvicorn until shutdown.

    Kept as a synchronous entry-point for back-compat with callers that
    pre-built the server. Production HTTP launch goes through
    :func:`serve_http_async` so the bootstrap + uvicorn loop share the
    same event loop (MET-477 / G3 — fixes the asyncpg pool-binding bug
    where pools created during ``_bootstrap`` were attached to a dead
    loop by the time uvicorn served requests on a new one).
    """
    import asyncio

    asyncio.run(serve_http_async(server, host, port, enable_sse=enable_sse))


async def serve_http_async(
    server: UnifiedMcpServer,
    host: str,
    port: int,
    *,
    enable_sse: bool,
) -> None:
    """Run uvicorn in the **current** event loop (MET-477 G3).

    Critical for the bootstrap path: ``_bootstrap`` creates asyncpg
    pools (memory experience store, consolidation insight store) bound
    to whichever event loop is running. If uvicorn then spins up its
    own loop via ``uvicorn.Server.run()``, every query against those
    pools fails with ``"another operation is in progress"`` because
    the pool's connection is tied to the dead bootstrap loop. Serving
    via ``uvicorn.Server.serve()`` (the async variant) keeps the
    pools and the request handlers in the same loop.
    """
    import uvicorn

    from metaforge.mcp.oauth import OAuthConfig

    api_key = os.environ.get("METAFORGE_MCP_API_KEY") or None
    oauth_config = OAuthConfig.from_env()
    oauth = OAuthProvider(oauth_config) if oauth_config else None
    service_key, service_verifier = _service_auth_from_env()
    app = build_http_app(
        server,
        enable_sse=enable_sse,
        api_key=api_key,
        oauth=oauth,
        service_key=service_key,
        service_verifier=service_verifier,
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
    )
    server_runner = uvicorn.Server(config)
    logger.info(
        "mcp_http_ready",
        host=host,
        port=port,
        sse_enabled=enable_sse,
        auth_enforced=bool(api_key),
        oauth_enabled=bool(oauth),
        adapter_count=len(server.adapters),
        tool_count=len(server.tool_ids),
    )
    await server_runner.serve()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _build_knowledge_service() -> Any:
    """Mirror ``api_gateway/server.py``'s ``create_knowledge_service`` wiring.

    Returns ``None`` when ``DATABASE_URL`` is unset — the L1 knowledge
    layer requires Postgres + pgvector, and the rest of the MCP surface
    (cadquery / freecad / calculix / twin / project) must stay usable
    in that mode. Errors during init are logged and swallowed for the
    same reason.

    When the service is returned, ``initialize()`` has already been
    called — callers register it directly into ``build_unified_server``.
    Teardown is the caller's responsibility (see ``main`` below).
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    try:
        from digital_twin.knowledge import create_knowledge_service

        # LightRAG's pgvector client wants ``postgresql://``; the gateway
        # publishes the asyncpg URL because SQLAlchemy needs that prefix.
        dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
        reranker_enabled = os.environ.get("KNOWLEDGE_RERANKER_ENABLED", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        service = create_knowledge_service(
            "lightrag",
            working_dir=os.environ.get("METAFORGE_LIGHTRAG_WORKDIR", "./.lightrag-storage"),
            postgres_dsn=dsn,
            reranker_enabled=reranker_enabled,
        )
        await service.initialize()  # type: ignore[attr-defined]
        logger.info(
            "mcp_knowledge_service_initialised",
            reranker_enabled=reranker_enabled,
        )
        return service
    except Exception as exc:
        logger.warning("mcp_knowledge_service_init_failed", error=str(exc))
        return None


async def _close_knowledge_service(service: Any) -> None:
    """Best-effort teardown — mirrors twin.aclose() semantics."""
    if service is None:
        return
    close = getattr(service, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.warning("mcp_knowledge_service_close_failed", error=str(exc))


async def _build_component_catalog_store() -> Any:
    """Construct + initialize the parametric component catalog (MET-436).

    Mirrors ``_build_knowledge_service``: returns ``None`` when
    ``DATABASE_URL`` is unset or init fails, so the rest of the MCP
    surface stays usable without it — ``component.*`` just stays
    unregistered (``component_mcp_adapter_skipped``) in that case.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    try:
        from digital_twin.catalog import ComponentCatalogStore

        dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
        store = ComponentCatalogStore(dsn=dsn)
        await store.initialize()
        logger.info("mcp_component_catalog_store_initialised")
        return store
    except Exception as exc:
        logger.warning("mcp_component_catalog_store_init_failed", error=str(exc))
        return None


async def _close_component_catalog_store(store: Any) -> None:
    """Best-effort teardown — mirrors ``_close_knowledge_service``."""
    if store is None:
        return
    close = getattr(store, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.warning("mcp_component_catalog_store_close_failed", error=str(exc))


def _build_component_intent_llm() -> Any:
    """Reuse the OpenRouter ``PropertyLLM`` client for intent translation (MET-436).

    ``OpenRouterPropertyLLM`` already satisfies ``IntentLLM`` structurally
    (both are a single ``async def complete(prompt: str) -> str``) — no
    adapter class needed. Returns ``None`` (no ``OPEN_ROUTER_API_KEY``)
    when unconfigured; ``component.search_intent`` just stays
    unregistered in that case, same degrade-gracefully contract as
    every other runtime-injected adapter.
    """
    if not os.environ.get("OPEN_ROUTER_API_KEY"):
        return None
    try:
        from digital_twin.knowledge.openrouter_property_llm import (
            OpenRouterPropertyConfig,
            OpenRouterPropertyLLM,
        )

        cfg = OpenRouterPropertyConfig.from_env()
        logger.info("mcp_component_intent_llm_initialised", model=cfg.primary_model)
        return OpenRouterPropertyLLM(cfg)
    except Exception as exc:
        logger.warning("mcp_component_intent_llm_init_failed", error=str(exc))
        return None


async def _build_memory_client(knowledge_service: Any = None) -> tuple[Any, Any, Any]:
    """Construct ``MemoryClient`` + experience store + embedder for the MCP entrypoint.

    Mirrors ``api_gateway/server.py``'s memory wiring (MET-453). Returns
    ``(client, store, embeddings)`` so the caller can close the pgvector pool
    on shutdown and reuse the embedder for the session→experience bridge
    (MET-567). Returns ``(None, None, None)`` when no embedding backend is
    available — the rest of the MCP surface stays usable.

    ``knowledge_service`` is passed through to the client so
    ``memory.search_design_rationale`` / ``get_component_context`` work here
    too, instead of raising the way they did with the L1 service omitted.
    """
    try:
        from digital_twin.knowledge.embedding_service import create_embedding_service
        from digital_twin.memory.client import MemoryClient
        from digital_twin.memory.pgvector_store import PgVectorExperienceStore
        from digital_twin.memory.store import InMemoryExperienceStore

        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            embeddings = create_embedding_service("openai", api_key=openai_key)
        else:
            embeddings = create_embedding_service("local")

        db_url = os.environ.get("DATABASE_URL")
        store: PgVectorExperienceStore | InMemoryExperienceStore | None = None
        if db_url:
            try:
                dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
                pg_store = PgVectorExperienceStore(dsn=dsn)
                await pg_store.initialize()
                store = pg_store
                logger.info("mcp_memory_store_pgvector_initialised")
            except Exception as exc:
                logger.warning("mcp_memory_store_pgvector_failed", error=str(exc))
        if store is None:
            store = InMemoryExperienceStore()
            logger.info("mcp_memory_store_in_memory_initialised")

        client = MemoryClient(store, embeddings, knowledge_service=knowledge_service)
        return client, store, embeddings
    except Exception as exc:
        logger.warning("mcp_memory_client_init_failed", error=str(exc))
        return None, None, None


async def _close_memory_store(store: Any) -> None:
    """Best-effort teardown of the pgvector pool."""
    if store is None:
        return
    close = getattr(store, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.warning("mcp_memory_store_close_failed", error=str(exc))


async def _build_agent_session_store() -> Any:
    """Build the agent-session store for MET-496 auto-capture.

    Returns a ``DATABASE_URL``-selected store (Pg in the sidecar, where it
    shares Postgres with the gateway so captured sessions surface in
    ``/sessions``; in-memory otherwise). Errors degrade to ``None`` — capture
    must never block server boot.
    """
    try:
        from api_gateway.sessions.backend import create_agent_session_store

        return await create_agent_session_store()
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning("mcp_agent_session_store_init_failed", error=str(exc))
        return None


async def _build_insight_store() -> Any:
    """Construct the consolidation insight store (MET-477 / G1).

    Mirrors ``api_gateway/server.py``'s lighter wiring: prefer
    pgvector when ``DATABASE_URL`` is set, otherwise fall back to
    in-memory. Without this, ``memory.list_insights`` raises
    "insight_store was called before set_insight_store()". Returns
    ``None`` only on import / init failure — the caller passes that
    straight to ``build_unified_server`` and MemoryServer falls back
    to its old "no store bound" error envelope (no regression).

    Note: this builder only stands up the *read* side of the
    consolidation flow. The full pipeline (orchestrator + Neo4j
    dual-write) lives in the gateway. The standalone MCP server is
    a read consumer, so pgvector alone is enough.
    """
    try:
        from digital_twin.memory.consolidation import (
            InMemoryInsightStore,
            PgVectorInsightStore,
        )

        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            try:
                dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
                pg_store = PgVectorInsightStore(dsn=dsn)
                await pg_store.initialize()
                logger.info("mcp_insight_store_pgvector_initialised")
                return pg_store
            except Exception as exc:  # noqa: BLE001 — degrade to in-memory
                logger.warning("mcp_insight_store_pgvector_failed", error=str(exc))
        logger.info("mcp_insight_store_in_memory_initialised")
        return InMemoryInsightStore()
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning("mcp_insight_store_init_failed", error=str(exc))
        return None


async def _close_insight_store(store: Any) -> None:
    """Best-effort teardown of the insight-store pool (MET-477)."""
    if store is None:
        return
    close = getattr(store, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_insight_store_close_failed", error=str(exc))


async def _bootstrap(
    args: argparse.Namespace,
) -> tuple[UnifiedMcpServer, InMemoryTwinAPI, Any, Any, Any, Any]:
    """Return the unified MCP server, the twin, knowledge service, memory store,
    insight store, and component catalog store.

    Callers must close the twin (``await twin.aclose()``), the
    knowledge service (``await _close_knowledge_service(svc)``), the
    memory store (``await _close_memory_store(store)``), the
    insight store (``await _close_insight_store(store)``), and the
    component catalog store (``await _close_component_catalog_store(store)``)
    when the transport loop exits — otherwise the Neo4j driver, aiohttp
    sessions, the LightRAG pgvector pool, the memory pgvector pool,
    the insight pgvector pool, and the component catalog pgvector pool
    leak across subprocess restarts (MET-425, MET-453, MET-477, MET-436).
    """
    from api_gateway.projects.backend import create_project_backend
    from twin_core.api import InMemoryTwinAPI

    twin = await InMemoryTwinAPI.create_from_env()
    # MET-427: bring up the same project backend the gateway uses so
    # `project.*` MCP tools see / write the same store. Falls back to
    # in-memory when DATABASE_URL is not set, matching the gateway.
    project_backend = await create_project_backend()
    # MET-433: close the bootstrap gap so ``python -m metaforge.mcp``
    # exposes ``knowledge.*`` tools when ``DATABASE_URL`` is set.
    # ``build_unified_server`` already accepts the kwarg — until now
    # only the gateway wired it.
    knowledge_service = await _build_knowledge_service()
    # MET-433: bind the twin so ``knowledge.extract`` can resolve MPN
    # → current Datasheet. ``set_twin`` is duck-typed (only the
    # production LightRAG impl needs it); the unit-test fakes used
    # in ``test_mcp_entrypoint`` mock it out.
    if knowledge_service is not None:
        set_twin = getattr(knowledge_service, "set_twin", None)
        if set_twin is not None:
            set_twin(twin)
    # MET-453: build the memory client so `memory.retrieve_similar_experience`
    # is exposed alongside knowledge.* when the standalone stdio MCP
    # server is the entrypoint (Claude Code / Cursor talking direct).
    memory_client, memory_store, memory_embeddings = await _build_memory_client(knowledge_service)
    # MET-477 / G1: build the consolidation insight store so
    # ``memory.list_insights`` doesn't error out with
    # "set_insight_store was never called". The gateway has the full
    # consolidation pipeline; the MCP server only needs the read side.
    insight_store = await _build_insight_store()
    # MET-496: the agent-session store the auto-capture middleware writes to.
    # Shares the DATABASE_URL-selected backend with the gateway so captured
    # sessions land in the same Postgres the /sessions routes read.
    agent_session_store = await _build_agent_session_store()
    # MET-567: the sidecar is where Layer-A auto-capture runs, so it closes
    # more sessions than the gateway does — wrap the store so each completion
    # (including an idle rollover) deposits an experience.
    try:
        from api_gateway.sessions.experience_bridge import wrap_with_experience_bridge

        agent_session_store = wrap_with_experience_bridge(
            agent_session_store, memory_store, memory_embeddings
        )
    except Exception as exc:  # noqa: BLE001 — capture must never block boot
        logger.warning("mcp_session_experience_bridge_failed", error=str(exc))
    # MET-567: publish WORK_PRODUCT_CREATED from the twin recorders onto an
    # in-process bus carrying the KnowledgeConsumer, so a decision recorded
    # over MCP becomes searchable knowledge here too (not only in the gateway).
    try:
        from api_gateway.twin.work_product_events import init_work_product_events
        from orchestrator.event_bus.subscribers import create_default_bus

        init_work_product_events(
            create_default_bus(knowledge_service=knowledge_service)
            if knowledge_service is not None
            else None
        )
    except Exception as exc:  # noqa: BLE001 — indexing is best-effort
        logger.warning("mcp_work_product_events_wiring_failed", error=str(exc))
    # MET-495: the decision recorder composes twin + project backend + MinIO
    # blob store; built here (api_gateway is importable) and injected so the
    # twin adapter exposes twin.record_decision without layer violations.
    decision_recorder = None
    try:
        from api_gateway.twin.decision_recorder import make_decision_recorder

        decision_recorder = make_decision_recorder(twin, project_backend)
    except Exception as exc:  # noqa: BLE001 — degrade; record_decision just absent
        logger.warning("mcp_decision_recorder_init_failed", error=str(exc))
    # MET-529: mirror the decision recorder so twin.commit_geometry works over
    # the sidecar too — without it, agents/chat that author CAD over MCP get
    # "Tool execution failed" (the recorder is None). MinIO is configured in the
    # sidecar env, so authored STEP persists to the shared bucket.
    geometry_recorder = None
    try:
        from api_gateway.twin.geometry_recorder import make_geometry_recorder

        geometry_recorder = make_geometry_recorder(twin, project_backend)
    except Exception as exc:  # noqa: BLE001 — degrade; commit_geometry just absent
        logger.warning("mcp_geometry_recorder_init_failed", error=str(exc))
    # MET-618: mirror the recorders above so twin.stage_work_product_file works
    # over the sidecar — without it, an agent whose freecad session has expired
    # has no way back to a committed work product's actual STEP/mesh content.
    blob_stager = None
    try:
        from api_gateway.twin.blob_stager import make_blob_stager

        blob_stager = make_blob_stager(twin)
    except Exception as exc:  # noqa: BLE001 — degrade; stage_work_product_file just absent
        logger.warning("mcp_blob_stager_init_failed", error=str(exc))
    # MET-436: the parametric component catalog + the intent-translation
    # LLM. Both are None-able independently — component.* just stays
    # unregistered (component_mcp_adapter_skipped) unless the catalog
    # store, the LLM, AND knowledge_service (reused for the intent-search
    # fuzzy fallback) are all available. Built before component_recorder
    # below so the recorder can auto-fill image/footprint/CAD/cost from an
    # already-indexed catalog row.
    component_catalog_store = await _build_component_catalog_store()
    component_intent_llm = _build_component_intent_llm()
    # MET-436 follow-up: mirror the decision recorder so
    # twin.record_component_selection works over the sidecar too — without
    # it, a component.search_* result stays pure chat output here even
    # though the gateway-hosted API already persists it as a BOMItem.
    component_recorder = None
    try:
        from api_gateway.twin.component_recorder import make_component_recorder

        component_recorder = make_component_recorder(
            twin, project_backend, catalog_store=component_catalog_store
        )
    except Exception as exc:  # noqa: BLE001 — degrade; record_component_selection just absent
        logger.warning("mcp_component_recorder_init_failed", error=str(exc))
    # FORGE-337: without this the sidecar registers no project resources at
    # all -- resources/list came back empty while /metaforge:use, :status and
    # :new all tell the agent to read metaforge://twin/brief/<project_id>.
    # Registration is conditional on the provider, so the absence was silent:
    # the resources did not fail, they were never there.
    brief_provider = None
    try:
        from api_gateway.projects.brief_provider import make_brief_provider
        from api_gateway.projects.routes import get_project_backend

        brief_provider = make_brief_provider(twin, get_project_backend())
    except Exception as exc:  # noqa: BLE001 — degrade to no resources, loudly
        logger.warning("mcp_brief_provider_unavailable", error=str(exc))

    approval_gate = _build_approval_gate()
    approval_ledger = _build_approval_ledger()

    # FORGE-413: without this the MCP server holds `metrics=None`, every
    # recorder returns at its `if counter is not None` guard, and all four
    # FORGE-379 metrics plus FORGE-411's two emit nothing -- so six alert
    # rules cannot fire, and Prometheus showed zero series for every one of
    # them. Exactly the shape of the MET-433 note a few lines above
    # (`build_unified_server` already accepted the kwarg; only the gateway
    # wired it) and of FORGE-406's approval gate.
    metrics = collector_for("metaforge-mcp")

    # FORGE-415: the Engineering Intent & Requirements Harness and the
    # document/constraint recorders registered in the gateway and never here,
    # so `twin.record_engineering_entity`, `approve_engineering_entity`,
    # `record_document` and `record_constraint_set` were absent from every
    # external MCP client. They need only the twin and the project backend,
    # both of which this function already has.
    #
    # `proposal_recorder` is deliberately not in this list: it takes the
    # gateway's `ApprovalWorkflow`, which the sidecar has no equivalent of --
    # its approvals go out through the remote gate instead. Wiring it would
    # mean inventing a second approval path, which is the opposite of what
    # FORGE-406 was about.
    from api_gateway.twin.constraint_recorder import make_constraint_recorder
    from api_gateway.twin.document_recorder import make_document_recorder
    from api_gateway.twin.engineering_entity_approval import (
        make_engineering_entity_approver,
    )
    from api_gateway.twin.engineering_entity_recorder import (
        make_engineering_entity_recorder,
    )
    from api_gateway.twin.item_revisions import make_item_history_reader

    entity_recorder = make_engineering_entity_recorder(twin, project_backend)

    # FORGE-462: flow.* and run.* registered in the gateway and never here, so
    # no harness plugin could list, propose or start a design flow.
    flow_bindings = _build_flow_bindings()

    server = await build_unified_server(
        **flow_bindings,
        engineering_entity_recorder=entity_recorder,
        engineering_entity_approver=make_engineering_entity_approver(twin),
        document_recorder=make_document_recorder(twin, project_backend),
        constraint_recorder=make_constraint_recorder(twin, project_backend),
        adapter_ids=_adapter_ids_from_args(args.adapters),
        approval_gate=approval_gate,
        approval_ledger=approval_ledger,
        metrics=metrics,
        brief_provider=brief_provider,
        # FORGE-371: unset means no links, reported by health/check. The
        # server cannot know where the dashboard is served from.
        dashboard_url=os.environ.get("METAFORGE_DASHBOARD_URL"),
        knowledge_service=knowledge_service,
        twin=twin,
        constraint_engine=twin.constraints,
        project_backend=project_backend,
        memory_client=memory_client,
        memory_insight_store=insight_store,
        twin_allow_mutations=getattr(args, "allow_twin_mutations", False),
        profile=getattr(args, "profile", None),
        agent_session_store=agent_session_store,
        capture_sessions=getattr(args, "capture_sessions", False),
        decision_recorder=decision_recorder,
        geometry_recorder=geometry_recorder,
        blob_stager=blob_stager,
        component_catalog_store=component_catalog_store,
        component_intent_llm=component_intent_llm,
        component_recorder=component_recorder,
        # FORGE-523: twin.item_history, so a plugin can read revisions too.
        item_history_reader=make_item_history_reader(twin),
    )
    return server, twin, knowledge_service, memory_store, insight_store, component_catalog_store


def _build_flow_bindings() -> dict[str, Any]:
    """Bindings for the ``design_flow`` and ``run`` adapters (FORGE-462).

    Both adapters register only when a binding is supplied, and until this
    the sidecar supplied none: ``flow.list``, ``flow.propose``,
    ``flow.start_run``, ``flow.status``, ``run.start_design_flow`` and
    ``run.get_status`` were absent from every external client's tools/list.

    Chosen the same way as the approval gate, for the same reason. The flow
    versions, the approval ledger and the run store are process-level, so:

    ``METAFORGE_GATEWAY_URL`` set
        Call the gateway's own routes. A flow proposed from a plugin is the
        one the dashboard shows, and a run started from one is in
        ``/v1/runs``.

    unset
        Bind to this process's stores. Correct only inside the gateway; said
        at start-up so a misconfigured sidecar is visible rather than quiet.
    """
    gateway_url = (os.environ.get("METAFORGE_GATEWAY_URL") or "").strip()
    if gateway_url:
        from metaforge.mcp.remote_flows import build_remote_flow_bindings

        remote = build_remote_flow_bindings(gateway_url)
        logger.info("mcp_flow_bindings_remote", gateway=gateway_url)
        return {
            "design_flow_catalogue_reader": remote.catalogue_reader,
            "design_flow_proposer": remote.proposer,
            "design_flow_status_reader": remote.status_reader,
            "design_flow_run_starter": remote.run_starter,
            "design_flow_intent_compiler": remote.intent_compiler,
            "design_flow_capability_reader": remote.capability_reader,
            "design_flow_lifecycle_reader": remote.lifecycle_reader,
            "run_launcher": remote.run_launcher,
        }

    try:
        from api_gateway.design_flows.mcp_bindings import (
            make_capability_reader,
            make_catalogue_reader,
            make_intent_compiler,
            make_lifecycle_reader,
            make_proposer,
            make_run_starter,
            make_run_status_reader,
        )
        from api_gateway.runs.launcher import make_run_launcher
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.error("mcp_flow_bindings_missing", error=str(exc))
        return {}

    logger.warning(
        "mcp_flow_bindings_in_process",
        detail=(
            "flow.* and run.* are bound to this process's own flow, approval and run "
            "stores. Correct only if this MCP server runs inside the gateway; a separate "
            "sidecar should set METAFORGE_GATEWAY_URL, or proposals and runs it creates "
            "will never appear on the dashboard."
        ),
    )
    return {
        "design_flow_catalogue_reader": make_catalogue_reader(),
        "design_flow_proposer": make_proposer(),
        "design_flow_status_reader": make_run_status_reader(),
        "design_flow_run_starter": make_run_starter(),
        "design_flow_intent_compiler": make_intent_compiler(),
        "design_flow_capability_reader": make_capability_reader(),
        "design_flow_lifecycle_reader": make_lifecycle_reader(),
        "run_launcher": make_run_launcher(),
    }


def _service_auth_from_env() -> tuple[str | None, ServiceRunVerifier | None]:
    """The design-flow service credential and what verifies runs (FORGE-487).

    ``METAFORGE_MCP_SERVICE_KEY`` is shared between this sidecar and the
    ``design-flow-worker`` only. There is no default: unset, the feature is
    off and the worker is an ordinary untrusted caller whose writes are held.
    ``METAFORGE_GATEWAY_URL`` is where runs are verified; without it the key
    alone enables nothing (``attach_service_auth`` says so).
    """
    key = (os.environ.get("METAFORGE_MCP_SERVICE_KEY") or "").strip() or None
    gateway_url = (os.environ.get("METAFORGE_GATEWAY_URL") or "").strip()
    if key is None:
        logger.info("mcp_service_caller_off", reason="METAFORGE_MCP_SERVICE_KEY is not set")
        return None, None
    if not gateway_url:
        return key, None
    from metaforge.mcp.service_runs import GatewayRunVerifier

    return key, GatewayRunVerifier(gateway_url)


def _build_approval_gate() -> Any:
    """Somewhere for a held write to wait (FORGE-406).

    Until this existed, nothing outside the test suite ever built a gate, so
    ``approval_gate`` was ``None`` and every write from a plugin came back
    "no approval gate is configured". FORGE-359's guardrail was present,
    correct and unreachable — which is the failure this codebase keeps
    producing, and the reason the absence is now logged at start-up rather
    than discovered on the first write.

    Two shapes, chosen explicitly:

    ``METAFORGE_GATEWAY_URL`` set
        Park held calls in the gateway's ledger over HTTP. This is the right
        answer for the sidecar: the approval store is process-level, so a
        call held in this process would sit in a queue the dashboard cannot
        see.

    unset, but ``api_gateway`` importable
        Use the in-process gate. Correct only when the MCP server is running
        inside the gateway; the log line says which was chosen so a
        misconfigured sidecar is visible rather than merely quiet.
    """
    gateway_url = (os.environ.get("METAFORGE_GATEWAY_URL") or "").strip()
    if gateway_url:
        from metaforge.mcp.remote_approvals import build_remote_approval_gate

        logger.info("mcp_approval_gate_remote", gateway=gateway_url)
        return build_remote_approval_gate(gateway_url)

    try:
        from api_gateway.mcp_approvals import build_mcp_approval_gate
    except Exception as exc:  # noqa: BLE001 — reported, never fatal
        logger.error(
            "mcp_approval_gate_missing",
            error=str(exc),
            detail=(
                "No approval gate could be built, so every held write will be REFUSED "
                "rather than queued. Set METAFORGE_GATEWAY_URL to the gateway so held "
                "calls reach the dashboard's approvals queue."
            ),
        )
        return None

    logger.warning(
        "mcp_approval_gate_in_process",
        detail=(
            "Holding writes in this process's own queue. Correct only if this MCP "
            "server runs inside the gateway; a separate sidecar should set "
            "METAFORGE_GATEWAY_URL, or held calls will never appear on the dashboard."
        ),
    )
    return build_mcp_approval_gate()


def _build_approval_ledger() -> Any:
    """The ledger inline (elicitation) holds are written to (FORGE-473).

    Mirrors :func:`_build_approval_gate`: a sidecar with ``METAFORGE_GATEWAY_URL``
    writes to the gateway over HTTP; inside the gateway it uses the process's own
    store. Neither available means ``None``, and the log says so, because an
    inline answer then leaves no ledger entry.
    """
    gateway_url = (os.environ.get("METAFORGE_GATEWAY_URL") or "").strip()
    if gateway_url:
        from metaforge.mcp.remote_approvals import RemoteApprovalLedger

        return RemoteApprovalLedger(gateway_url)
    try:
        from api_gateway.mcp_approvals import InProcessApprovalLedger
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.warning(
            "mcp_approval_ledger_missing",
            error=str(exc),
            detail="Inline approvals will have no approval id and will not reach the ledger.",
        )
        return None
    return InProcessApprovalLedger()


def _configure_logging_for_transport(transport: str) -> None:
    """Pin every log to stderr when stdio is the data channel.

    The default structlog factory writes to stdout — that would corrupt
    the JSON-RPC framing on stdio. ``PrintLoggerFactory(file=sys.stderr)``
    is the single hammer that catches logs emitted during adapter
    bootstrap (before the entrypoint owns the event loop).
    """
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    if transport == "stdio":
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.UnicodeDecoder(),
                structlog.dev.ConsoleRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging_for_transport(args.transport)

    # Bootstrap and stdio loop must share one asyncio event loop because
    # remote adapters open aiohttp ClientSessions during bootstrap that
    # are bound to that loop's selector — closing the loop between
    # bootstrap and run_stdio leaves the sessions orphaned and any
    # subsequent ``send`` returns an empty/error response (MET-373).
    if args.transport == "stdio":

        async def _stdio() -> None:
            (
                server,
                twin,
                knowledge_service,
                memory_store,
                insight_store,
                cc_store,
            ) = await _bootstrap(args)
            try:
                await run_stdio(server)
            finally:
                # Release remote adapters' aiohttp ClientSessions -- otherwise
                # only reclaimed by GC at interpreter exit, logging an
                # "Unclosed client session" warning on every restart.
                if server.tool_registry is not None:
                    await server.tool_registry.close_all()
                # MET-425: release the Neo4j driver / backing-store
                # resources so subprocess respawns from the UAT harness
                # don't see "address in use" or ResourceWarning leaks.
                await twin.aclose()
                # MET-433: same hygiene for the LightRAG pgvector pool.
                await _close_knowledge_service(knowledge_service)
                # MET-453: same hygiene for the memory pgvector pool.
                await _close_memory_store(memory_store)
                # MET-477 / G1: same hygiene for the insight pgvector pool.
                await _close_insight_store(insight_store)
                # MET-436: same hygiene for the component catalog pgvector pool.
                await _close_component_catalog_store(cc_store)

        asyncio.run(_stdio())
    else:
        # MET-477 / G3: bootstrap + uvicorn share one event loop so
        # asyncpg pools created during ``_bootstrap`` stay bound to
        # the loop that uvicorn serves requests on. The previous
        # ``asyncio.run(_bootstrap) ... run_http() ... asyncio.run(_close_*)``
        # pattern destroyed the bootstrap loop before uvicorn's loop
        # started — every subsequent memory.* / list_insights query
        # failed with "another operation is in progress" because the
        # asyncpg pool was bound to a dead loop.
        async def _http_main() -> None:
            server, twin, kb_svc, mem_store, ins_store, cc_store = await _bootstrap(args)
            try:
                await serve_http_async(
                    server,
                    args.host,
                    args.port,
                    enable_sse=args.transport == "sse",
                )
            finally:
                if server.tool_registry is not None:
                    await server.tool_registry.close_all()
                await twin.aclose()
                await _close_knowledge_service(kb_svc)
                await _close_memory_store(mem_store)
                await _close_insight_store(ins_store)
                await _close_component_catalog_store(cc_store)

        asyncio.run(_http_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
