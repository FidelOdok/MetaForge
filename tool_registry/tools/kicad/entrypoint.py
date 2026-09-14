"""KiCad MCP adapter entrypoint — HTTP server (stdio fallback).

This script is the Docker container entrypoint. It initializes the KiCad MCP
server and, by default, serves JSON-RPC over HTTP (``POST /mcp`` + ``GET
/health``) so the gateway/sidecar can reach it as a remote adapter — the same
transport cadquery/freecad/calculix use (MET-532). Without this, the unified
MCP bootstrap (``tool_registry/bootstrap.py``) has no reachable URL for kicad
and falls back to running the adapter in-process, where the gateway/sidecar
image has no ``kicad-cli`` binary installed and every kicad.* tool call fails
with -32001 regardless of whether this container is running (MET-478
follow-up). Set ``KICAD_TRANSPORT=stdio`` to fall back to the legacy
stdin/stdout MCP transport.
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
    """Start the KiCad MCP adapter server (HTTP by default)."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    # Import here to ensure PYTHONPATH is set correctly.
    from tool_registry.tools.kicad.adapter import KicadServer
    from tool_registry.tools.kicad.config import KicadConfig

    work_dir = os.environ.get("KICAD_WORK_DIR", "/workspace")
    kicad_cli = os.environ.get("KICAD_CLI_PATH", "kicad-cli")
    config = KicadConfig(kicad_cli=kicad_cli, work_dir=work_dir)
    server = KicadServer(config=config)

    logger.info(
        "KiCad MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    if os.environ.get("KICAD_TRANSPORT", "http").lower() == "stdio":
        await server.start_stdio()
    else:
        port = int(os.environ.get("KICAD_HTTP_PORT", "8103"))
        await _start_http(server, port)


async def _start_http(server: object, port: int) -> None:
    """Serve JSON-RPC over HTTP, mirroring the cadquery/freecad/calculix adapters."""
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
    logger.info("KiCad HTTP server starting", port=port)
    await site.start()

    await asyncio.Event().wait()  # run until a shutdown signal


if __name__ == "__main__":
    asyncio.run(main())
