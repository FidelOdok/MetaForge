"""OpenFOAM MCP adapter entrypoint: JSON-RPC over HTTP (FORGE-557).

Serves ``POST /mcp`` and ``GET /health`` like the spice, calculix and cadquery
adapters, so the gateway and sidecar reach it through
``METAFORGE_ADAPTER_OPENFOAM_URL``. In-process, the gateway image has no OpenFOAM
and every call would fail.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog

logger = structlog.get_logger(__name__)


def _handle_shutdown(signum: int, _frame: object) -> None:
    logger.info("Received shutdown signal", signal=signal.Signals(signum).name)
    sys.exit(0)


async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    from aiohttp import web

    from mcp_core.context import context_from_headers, with_context
    from tool_registry.tools.openfoam.adapter import OpenFoamServer
    from tool_registry.tools.openfoam.config import OpenFoamConfig

    server = OpenFoamServer(
        OpenFoamConfig(
            bashrc=os.environ.get("OPENFOAM_BASHRC", "/opt/openfoam11/etc/bashrc"),
            work_dir=os.environ.get("OPENFOAM_WORK_DIR", "/workspace"),
            max_operation_time=int(os.environ.get("OPENFOAM_MAX_OPERATION_TIME", "3600")),
            max_cells=int(os.environ.get("OPENFOAM_MAX_CELLS", "2000000")),
            max_procs=int(os.environ.get("OPENFOAM_MAX_PROCS", "8")),
        )
    )

    async def handle_mcp(request: web.Request) -> web.Response:
        body = await request.text()
        with with_context(context_from_headers(dict(request.headers))):
            response = await server.handle_request(body)
        return web.Response(text=response, content_type="application/json")

    async def handle_health(_request: web.Request) -> web.Response:
        return web.Response(text='{"status":"healthy"}', content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("OPENFOAM_HTTP_PORT", "8300"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("OpenFOAM HTTP server starting", port=port, tools=server.tool_ids)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
