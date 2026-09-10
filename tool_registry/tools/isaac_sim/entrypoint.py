"""Isaac Sim MCP adapter entrypoint — HTTP server with JSON-RPC `/mcp`.

This adapter is a thin coordinator: it does NOT bundle Isaac Sim itself
(no GPU needed for the adapter container). It dispatches jobs to a
*separate* ``nvcr.io/nvidia/isaac-sim`` container via
``tool_registry.compute_providers.resolve_runtime()`` -- the same shape
as ``compute_providers.py``'s own RunPod/Vast.ai runtimes, one level up.

Mirrors the calculix/gazebo/omniverse_usd adapter pattern otherwise: the
unified ``UnifiedMcpServer`` and its remote-adapter shim
(``_RemoteAdapterServer`` in ``tool_registry/registry.py``) post JSON-RPC
to ``/mcp`` and expect ``tool/list`` / ``tool/call`` to dispatch to the
adapter's ``McpToolServer.handle_request``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tool_registry.tools.isaac_sim.adapter import IsaacSimServer

logger = structlog.get_logger(__name__)


def _handle_shutdown(signum: int, _frame: object) -> None:
    sig_name = signal.Signals(signum).name
    logger.info("Received shutdown signal", signal=sig_name)
    sys.exit(0)


async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    from tool_registry.tools.isaac_sim.adapter import IsaacSimServer
    from tool_registry.tools.isaac_sim.config import IsaacSimConfig

    work_dir = os.environ.get("ISAAC_SIM_WORK_DIR", "/workspace")
    config = IsaacSimConfig(work_dir=work_dir)
    server = IsaacSimServer(config=config)

    logger.info(
        "Isaac Sim MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    port = int(os.environ.get("MCP_PORT", "8203"))
    await _start_http(server, port, work_dir)


async def _start_http(server: IsaacSimServer, port: int, work_dir: str) -> None:
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
        work_dir_exists = Path(work_dir).exists()
        status = "healthy" if work_dir_exists else "degraded"
        body = {
            "adapter_id": server.adapter_id,
            "status": status,
            "version": server.version,
            "tools_available": len(server.tool_ids),
            "work_dir": work_dir,
        }
        return web.Response(text=json.dumps(body), content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    logger.info("Isaac Sim HTTP server starting", port=port)
    await site.start()

    # Keep running until shutdown signal
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
