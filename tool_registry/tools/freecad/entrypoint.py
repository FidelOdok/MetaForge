"""FreeCAD MCP adapter entrypoint — HTTP server (stdio fallback).

This script is the Docker container entrypoint. It initializes the FreeCAD MCP
server and, by default, serves JSON-RPC over HTTP (``POST /mcp`` + ``GET
/health``) so the gateway/sidecar can reach it as a remote adapter — the same
transport cadquery/calculix use (MET-532). Set ``FREECAD_TRANSPORT=stdio`` to
fall back to the legacy stdin/stdout MCP transport.
"""

from __future__ import annotations

import asyncio
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

    work_dir = os.environ.get("FREECAD_WORK_DIR", "/workspace")
    freecad_binary = os.environ.get("FREECAD_BINARY", "freecadcmd")
    config = FreecadConfig(freecad_binary=freecad_binary, work_dir=work_dir)
    server = FreecadServer(config=config)

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
    )

    if os.environ.get("FREECAD_TRANSPORT", "http").lower() == "stdio":
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
