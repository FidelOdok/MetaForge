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
    server = await build_unified_server(
        adapter_ids=_adapter_ids_from_args(args.adapters),
        knowledge_service=knowledge_service,
        twin=twin,
        constraint_engine=twin.constraints,
        project_backend=project_backend,
        memory_client=memory_client,
        memory_insight_store=insight_store,
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
