"""CadQuery MCP adapter entrypoint -- HTTP + stdio server."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog

logger = structlog.get_logger(__name__)


def _handle_shutdown(signum: int, _frame: object) -> None:
    sig_name = signal.Signals(signum).name
    logger.info("Received shutdown signal", signal=sig_name)
    sys.exit(0)


async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    from tool_registry.tools.cadquery.adapter import CadqueryServer
    from tool_registry.tools.cadquery.config import CadqueryConfig

    work_dir = os.environ.get("CADQUERY_WORK_DIR", "/workspace")
    config = CadqueryConfig(work_dir=work_dir)
    server = CadqueryServer(config=config)

    logger.info(
        "CadQuery MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    port = int(os.environ.get("CADQUERY_HTTP_PORT", "8100"))
    await _start_http(server, port)


async def _start_http(server, port: int) -> None:
    """Start an HTTP server that forwards JSON-RPC to the MCP server."""
    from aiohttp import web

    async def handle_mcp(request: web.Request) -> web.Response:
        body = await request.text()
        response = await server.handle_request(body)
        return web.Response(text=response, content_type="application/json")

    async def handle_health(request: web.Request) -> web.Response:
        return web.Response(text='{"status":"healthy"}', content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info("CadQuery HTTP server starting", port=port)
    await site.start()

    # Keep running until shutdown signal
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
