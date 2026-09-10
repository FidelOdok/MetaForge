"""OpenUSD conversion MCP adapter entrypoint — HTTP server with JSON-RPC `/mcp`.

Mirrors the calculix/gazebo adapter pattern: the unified ``UnifiedMcpServer``
and its remote-adapter shim (``_RemoteAdapterServer`` in
``tool_registry/registry.py``) post JSON-RPC to ``/mcp`` and expect
``tool/list`` / ``tool/call`` to dispatch to the adapter's
``McpToolServer.handle_request``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tool_registry.tools.omniverse_usd.adapter import OmniverseUsdServer

logger = structlog.get_logger(__name__)


def _handle_shutdown(signum: int, _frame: object) -> None:
    sig_name = signal.Signals(signum).name
    logger.info("Received shutdown signal", signal=sig_name)
    sys.exit(0)


async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    from tool_registry.tools.omniverse_usd.adapter import OmniverseUsdServer
    from tool_registry.tools.omniverse_usd.config import OmniverseUsdConfig

    work_dir = os.environ.get("OMNIVERSE_USD_WORK_DIR", "/workspace")
    config = OmniverseUsdConfig(work_dir=work_dir)
    server = OmniverseUsdServer(config=config)

    logger.info(
        "OpenUSD conversion MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    port = int(os.environ.get("MCP_PORT", "8202"))
    await _start_http(server, port, work_dir)


async def _start_http(server: OmniverseUsdServer, port: int, work_dir: str) -> None:
    """Start an HTTP server that forwards JSON-RPC to the MCP server."""
    import json
    from pathlib import Path

    from aiohttp import web

    from mcp_core.context import context_from_headers, with_context

    async def handle_mcp(request: web.Request) -> web.Response:
        body = await request.text()
        ctx = context_from_headers(dict(request.headers))
        with with_context(ctx):
            response = await server.handle_request(body)
        return web.Response(text=response, content_type="application/json")

    async def handle_health(request: web.Request) -> web.Response:
        try:
            import pxr  # noqa: F401

            usd_available = True
        except ImportError:
            usd_available = False
        work_dir_exists = Path(work_dir).exists()
        status = "healthy" if usd_available and work_dir_exists else "degraded"
        body = {
            "adapter_id": server.adapter_id,
            "status": status,
            "version": server.version,
            "tools_available": len(server.tool_ids),
            "usd_core_available": usd_available,
            "work_dir": work_dir,
        }
        return web.Response(text=json.dumps(body), content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    logger.info("OpenUSD conversion HTTP server starting", port=port)
    await site.start()

    # Keep running until shutdown signal
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
