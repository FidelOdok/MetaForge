"""FreeCAD MCP adapter entrypoint — HTTP server (stdio fallback).

This script is the Docker container entrypoint. It initializes the FreeCAD MCP
server and, by default, serves JSON-RPC over HTTP (``POST /mcp`` + ``GET
/health``) so the gateway/sidecar can reach it as a remote adapter — the same
transport cadquery/calculix use (MET-532). Set ``FREECAD_TRANSPORT=stdio`` to
fall back to the legacy stdin/stdout MCP transport.
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import signal
import sys

import structlog

logger = structlog.get_logger(__name__)


def _handle_shutdown(signum: int, _frame: object) -> None:
    """Handle graceful shutdown signals."""
    sig_name = signal.Signals(signum).name
    logger.info("Received shutdown signal", signal=sig_name)
    sys.exit(0)


async def main() -> None:
    """Start the FreeCAD MCP adapter server (HTTP by default)."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    # Import here to ensure PYTHONPATH is set correctly.
    from tool_registry.tools.freecad import operations as _ops
    from tool_registry.tools.freecad.adapter import FreecadServer
    from tool_registry.tools.freecad.config import FreecadConfig
    from tool_registry.tools.freecad.worker_pool import FreecadWorkerPool

    work_dir = os.environ.get("FREECAD_WORK_DIR", "/workspace")
    freecad_binary = os.environ.get("FREECAD_BINARY", "freecadcmd")
    config_kwargs: dict[str, object] = {"freecad_binary": freecad_binary, "work_dir": work_dir}
    if (ttl := os.environ.get("FREECAD_SESSION_TTL_SECONDS")) is not None:
        config_kwargs["session_ttl_seconds"] = float(ttl)
    if (max_sessions := os.environ.get("FREECAD_MAX_SESSIONS")) is not None:
        config_kwargs["max_sessions"] = int(max_sessions)
    if (max_workers := os.environ.get("FREECAD_MAX_WORKERS")) is not None:
        config_kwargs["max_workers"] = int(max_workers)
    config = FreecadConfig(**config_kwargs)

    # FORGE-221: arm faulthandler before anything can crash. Best-effort — on
    # a real SIGSEGV this prints the Python-level stack at the moment of the
    # signal to stderr before the process dies (not a guarantee; some
    # corrupted states can't run even this), but it's strictly better than
    # today's bare "exit 0, no traceback." Harmless to enable unconditionally
    # in HTTP mode too.
    faulthandler.enable()

    stdio_mode = os.environ.get("FREECAD_TRANSPORT", "http").lower() == "stdio"
    # FORGE-221: HTTP mode is the gateway -- stateful tools route through a
    # FreecadWorkerPool, each worker being another copy of this same process
    # started in stdio mode (below) with FREECAD_MAX_SESSIONS=1. Stdio mode
    # (a worker, or the legacy direct-stdio deployment) never gets a pool of
    # its own -- worker_pool=None makes every method run its real body, which
    # is exactly what a worker needs to do.
    pool = None if stdio_mode else FreecadWorkerPool(config)
    server = FreecadServer(config=config, worker_pool=pool)

    # Startup self-check: surface the FreeCAD-availability state up front so a
    # misconfigured image (wrong interpreter / missing workbenches — see MET-527)
    # is obvious in the logs rather than only at first tool call.
    logger.info(
        "FreeCAD MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tool_count=len(server.tool_ids),
        has_freecad=_ops.HAS_FREECAD,
        has_partdesign=_ops.HAS_PARTDESIGN,
        work_dir=work_dir,
        session_ttl_seconds=config.session_ttl_seconds,
        max_sessions=config.max_sessions,
        max_workers=config.max_workers,
        pooled=pool is not None,
    )

    if stdio_mode:
        # FORGE-221: FreecadWorkerPool's StdioTransport waits for this exact
        # line on stderr before sending its first request -- avoids racing
        # FreeCAD's own non-trivial import time (already done by the time we
        # get here, via `from tool_registry.tools.freecad import operations`
        # above, but the pool doesn't know that without an explicit signal).
        print("freecad-worker-ready", file=sys.stderr, flush=True)
        await server.start_stdio()
    else:
        port = int(os.environ.get("FREECAD_HTTP_PORT", "8102"))
        await _start_http(server, port)


async def _start_http(server: object, port: int) -> None:
    """Serve JSON-RPC over HTTP, mirroring the cadquery/calculix adapters."""
    from aiohttp import web

    from mcp_core.context import context_from_headers, with_context

    async def handle_mcp(request: web.Request) -> web.Response:
        body = await request.text()
        # MET-387: scope every /mcp call to the harness's context so downstream
        # handlers see project / actor via ``current_context()``.
        ctx = context_from_headers(dict(request.headers))
        with with_context(ctx):
            response = await server.handle_request(body)  # type: ignore[attr-defined]
        return web.Response(text=response, content_type="application/json")

    async def handle_health(_request: web.Request) -> web.Response:
        return web.Response(text='{"status":"healthy"}', content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info("FreeCAD HTTP server starting", port=port)
    await site.start()

    await asyncio.Event().wait()  # run until a shutdown signal


if __name__ == "__main__":
    asyncio.run(main())
