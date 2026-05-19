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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from twin_core.api import InMemoryTwinAPI
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mcp_core.auth import AUTH_DENIED, redact, verify_api_key
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
    reader = asyncio.StreamReader()
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

    def _check_auth(authorization: str | None) -> None:
        if not api_key:
            return
        provided: str | None = None
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization.split(None, 1)[1].strip()
        result = verify_api_key(provided, api_key)
        if not result.ok:
            logger.warning(
                "mcp_auth_denied",
                transport="http",
                reason=result.reason,
                redacted=result.redacted or redact(provided or ""),
            )
            raise HTTPException(
                status_code=401,
                detail={"error_type": AUTH_DENIED, "reason": result.reason},
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
        _check_auth(authorization)
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
            _check_auth(authorization)
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

    return app


def run_http(server: UnifiedMcpServer, host: str, port: int, *, enable_sse: bool) -> None:
    """Block on uvicorn until shutdown."""
    import uvicorn

    api_key = os.environ.get("METAFORGE_MCP_API_KEY") or None
    app = build_http_app(server, enable_sse=enable_sse, api_key=api_key)
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
        adapter_count=len(server.adapters),
        tool_count=len(server.tool_ids),
    )
    server_runner.run()


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
        reranker_enabled = os.environ.get(
            "KNOWLEDGE_RERANKER_ENABLED", "false"
        ).lower() in ("1", "true", "yes")
        service = create_knowledge_service(
            "lightrag",
            working_dir=os.environ.get(
                "METAFORGE_LIGHTRAG_WORKDIR", "./.lightrag-storage"
            ),
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


async def _bootstrap(
    args: argparse.Namespace,
) -> tuple[UnifiedMcpServer, InMemoryTwinAPI, Any]:
    """Return the unified MCP server, the twin, and the knowledge service.

    Callers must close the twin (``await twin.aclose()``) and the
    knowledge service (``await _close_knowledge_service(svc)``) when
    the transport loop exits — otherwise the Neo4j driver, aiohttp
    sessions, and the LightRAG pgvector pool leak across subprocess
    restarts (MET-425).
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
    server = await build_unified_server(
        adapter_ids=_adapter_ids_from_args(args.adapters),
        knowledge_service=knowledge_service,
        twin=twin,
        constraint_engine=twin.constraints,
        project_backend=project_backend,
    )
    return server, twin, knowledge_service


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
            server, twin, knowledge_service = await _bootstrap(args)
            try:
                await run_stdio(server)
            finally:
                # MET-425: release the Neo4j driver / backing-store
                # resources so subprocess respawns from the UAT harness
                # don't see "address in use" or ResourceWarning leaks.
                await twin.aclose()
                # MET-433: same hygiene for the LightRAG pgvector pool.
                await _close_knowledge_service(knowledge_service)

        asyncio.run(_stdio())
    else:
        # HTTP path keeps the two-step flow: ``run_http`` runs uvicorn
        # which manages its own loop and only consumes the bootstrapped
        # server's introspection surface.
        server, twin, knowledge_service = asyncio.run(_bootstrap(args))
        try:
            run_http(server, args.host, args.port, enable_sse=args.transport == "sse")
        finally:
            # MET-425: best-effort teardown after uvicorn returns. The
            # neo4j async driver tolerates cross-loop close in practice.
            asyncio.run(twin.aclose())
            asyncio.run(_close_knowledge_service(knowledge_service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
