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
from typing import Any

import structlog
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
        response = await server.handle_request(raw_body.decode("utf-8"))
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

            async def _events() -> AsyncIterator[bytes]:
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


async def _bootstrap(args: argparse.Namespace) -> UnifiedMcpServer:
    return await build_unified_server(adapter_ids=_adapter_ids_from_args(args.adapters))


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
    server = asyncio.run(_bootstrap(args))
    if args.transport == "stdio":
        asyncio.run(run_stdio(server))
    else:
        run_http(server, args.host, args.port, enable_sse=args.transport == "sse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
