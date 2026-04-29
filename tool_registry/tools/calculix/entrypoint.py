"""CalculiX MCP adapter entrypoint — HTTP server with JSON-RPC `/mcp`.

Mirrors the cadquery adapter pattern (``tool_registry/tools/cadquery/entrypoint.py``):
the unified ``UnifiedMcpServer`` and its remote-adapter shim
(``_RemoteAdapterServer`` in ``tool_registry/registry.py``) post JSON-RPC
to ``/mcp`` and expect ``tool/list`` / ``tool/call`` to dispatch to the
adapter's ``McpToolServer.handle_request``.

Replaces the previous REST-only entrypoint (per-tool ``/tools/<id>``)
which broke unified routing — see MET-379 + MET-380.
"""

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

    from tool_registry.tools.calculix.adapter import CalculixServer
    from tool_registry.tools.calculix.config import CalculixConfig

    work_dir = os.environ.get("CALCULIX_WORK_DIR", "/workspace")
    ccx_binary = os.environ.get("CCX_BINARY", "ccx")
    config = CalculixConfig(work_dir=work_dir, ccx_binary=ccx_binary)
    server = CalculixServer(config=config)

    logger.info(
        "CalculiX MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    port = int(os.environ.get("MCP_PORT", "8200"))
    await _start_http(server, port, work_dir, ccx_binary)


async def _start_http(server, port: int, work_dir: str, ccx_binary: str) -> None:
    """Start an HTTP server that forwards JSON-RPC to the MCP server."""
    import json
    import shutil
    from pathlib import Path

    from aiohttp import web

    async def handle_mcp(request: web.Request) -> web.Response:
        body = await request.text()
        response = await server.handle_request(body)
        return web.Response(text=response, content_type="application/json")

    async def handle_health(request: web.Request) -> web.Response:
        ccx_available = shutil.which(ccx_binary) is not None
        work_dir_exists = Path(work_dir).exists()
        # Note: ``status`` is "healthy" only when both ccx and the work
        # dir are reachable — the manifest is stable regardless (all 4
        # tools register), so degraded mode still routes tool/list and
        # validate_mesh (no ccx required) correctly. See MET-380.
        status = "healthy" if ccx_available and work_dir_exists else "degraded"
        body = {
            "adapter_id": server.adapter_id,
            "status": status,
            "version": server.version,
            "tools_available": len(server.tool_ids),
            "ccx_available": ccx_available,
            "work_dir": work_dir,
        }
        return web.Response(text=json.dumps(body), content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    logger.info("CalculiX HTTP server starting", port=port)
    await site.start()

    # Keep running until shutdown signal
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
