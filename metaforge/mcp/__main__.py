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

import structlog

if TYPE_CHECKING:
    from twin_core.api import InMemoryTwinAPI
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from mcp_core.auth import AUTH_DENIED, redact, verify_api_key
from metaforge.mcp.oauth import OAuthError, OAuthProvider
from metaforge.mcp.server import UnifiedMcpServer, build_unified_server

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
                "code": -32002,
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
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            raw = line.decode("utf-8").strip()
            if not raw:
                continue
            response = await server.handle_request(raw)
            # JSON-RPC notifications return an empty body — writing a
            # blank line breaks the client's JSON line framing.
            if not response:
                continue
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
    except asyncio.CancelledError:
        pass
    finally:
        transport.close()
        logger.info("mcp_stdio_stopped")


# ---------------------------------------------------------------------------
# HTTP / SSE transport
# ---------------------------------------------------------------------------


def build_http_app(
    server: UnifiedMcpServer,
    *,
    enable_sse: bool,
    api_key: str | None = None,
    oauth: OAuthProvider | None = None,
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
    """
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

    def _check_auth(request: Request, authorization: str | None) -> None:
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
        if oauth is not None and oauth_on and provided and oauth.validate_token(provided):
            return

        # Static API-key path — authoritative only when a key is set.
        reason = "invalid_token"
        if api_key:
            result = verify_api_key(provided, api_key)
            if result.ok:
                return
            reason = result.reason
        elif not oauth_on:
            # Neither mechanism configured → open mode, everything passes.
            return

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

    @app.post("/mcp")
    async def mcp_post(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_auth(request, authorization)
        raw_body = await request.body()
        # MET-387: install per-request McpCallContext from headers so
        # downstream handlers see the project / actor / session via
        # ``current_context()``.
        from mcp_core.context import context_from_headers, with_context

        ctx = context_from_headers(dict(request.headers))
        with with_context(ctx):
            response = await server.handle_request(raw_body.decode("utf-8"))
        # JSON-RPC notifications produce no body — return 204 so the
        # client doesn't try to json-parse an empty string.
        if not response:
            return JSONResponse(content=None, status_code=204)
        return JSONResponse(json.loads(response))

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
  <label>Access secret<br>
    <input type="password" name="login_secret" autofocus required>
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
        code = oauth.issue_code(client, redirect_uri, params["code_challenge"], params.get("scope"))
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
    app = build_http_app(server, enable_sse=enable_sse, api_key=api_key, oauth=oauth)
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


async def _build_memory_client() -> tuple[Any, Any]:
    """Construct ``MemoryClient`` + experience store for the standalone MCP entrypoint.

    Mirrors ``api_gateway/server.py``'s memory wiring (MET-453). Returns
    ``(client, store)`` so the caller can close the pgvector pool on
    shutdown. Returns ``(None, None)`` when no embedding backend is
    available — the rest of the MCP surface stays usable.
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

        client = MemoryClient(store, embeddings)
        return client, store
    except Exception as exc:
        logger.warning("mcp_memory_client_init_failed", error=str(exc))
        return None, None


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
) -> tuple[UnifiedMcpServer, InMemoryTwinAPI, Any, Any, Any]:
    """Return the unified MCP server, the twin, knowledge service, memory store, insight store.

    Callers must close the twin (``await twin.aclose()``), the
    knowledge service (``await _close_knowledge_service(svc)``), the
    memory store (``await _close_memory_store(store)``), and the
    insight store (``await _close_insight_store(store)``) when the
    transport loop exits — otherwise the Neo4j driver, aiohttp
    sessions, the LightRAG pgvector pool, the memory pgvector pool,
    and the insight pgvector pool leak across subprocess restarts
    (MET-425, MET-453, MET-477).
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
    memory_client, memory_store = await _build_memory_client()
    # MET-477 / G1: build the consolidation insight store so
    # ``memory.list_insights`` doesn't error out with
    # "set_insight_store was never called". The gateway has the full
    # consolidation pipeline; the MCP server only needs the read side.
    insight_store = await _build_insight_store()
    # MET-496: the agent-session store the auto-capture middleware writes to.
    # Shares the DATABASE_URL-selected backend with the gateway so captured
    # sessions land in the same Postgres the /sessions routes read.
    agent_session_store = await _build_agent_session_store()
    # MET-495: the decision recorder composes twin + project backend + MinIO
    # blob store; built here (api_gateway is importable) and injected so the
    # twin adapter exposes twin.record_decision without layer violations.
    decision_recorder = None
    try:
        from api_gateway.twin.decision_recorder import make_decision_recorder

        decision_recorder = make_decision_recorder(twin, project_backend)
    except Exception as exc:  # noqa: BLE001 — degrade; record_decision just absent
        logger.warning("mcp_decision_recorder_init_failed", error=str(exc))
    server = await build_unified_server(
        adapter_ids=_adapter_ids_from_args(args.adapters),
        knowledge_service=knowledge_service,
        twin=twin,
        constraint_engine=twin.constraints,
        project_backend=project_backend,
        memory_client=memory_client,
        memory_insight_store=insight_store,
        twin_allow_mutations=getattr(args, "allow_twin_mutations", False),
        agent_session_store=agent_session_store,
        capture_sessions=getattr(args, "capture_sessions", False),
        decision_recorder=decision_recorder,
    )
    return server, twin, knowledge_service, memory_store, insight_store


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
            server, twin, knowledge_service, memory_store, insight_store = await _bootstrap(args)
            try:
                await run_stdio(server)
            finally:
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
            server, twin, kb_svc, mem_store, ins_store = await _bootstrap(args)
            try:
                await serve_http_async(
                    server,
                    args.host,
                    args.port,
                    enable_sse=args.transport == "sse",
                )
            finally:
                await twin.aclose()
                await _close_knowledge_service(kb_svc)
                await _close_memory_store(mem_store)
                await _close_insight_store(ins_store)

        asyncio.run(_http_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
