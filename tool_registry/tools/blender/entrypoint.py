"""Blender MCP adapter entrypoint (FORGE-628) — HTTP server with JSON-RPC `/mcp`.

Mirrors the calculix adapter pattern (``tool_registry/tools/calculix/entrypoint.py``):
the unified ``UnifiedMcpServer`` and its remote-adapter shim
(``_RemoteAdapterServer`` in ``tool_registry/registry.py``) post JSON-RPC
to ``/mcp`` and expect ``tool/list`` / ``tool/call`` to dispatch to the
adapter's ``McpToolServer.handle_request``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tool_registry.tools.blender.adapter import BlenderServer

logger = structlog.get_logger(__name__)

#: The largest request body the adapter reads, in MiB. aiohttp's own default
#: is 1 MiB, and ``twin.render_work_product`` sends the model inline: a
#: converted airframe was refused with a plain-text 413 the caller could not
#: parse. Same rule as the CadQuery adapter (``CADQUERY_MAX_REQUEST_MB``).
MAX_REQUEST_MB_ENV = "BLENDER_MAX_REQUEST_MB"
DEFAULT_MAX_REQUEST_MB = 256


def max_request_bytes(env: dict[str, str] | None = None) -> int:
    """The request body limit from ``BLENDER_MAX_REQUEST_MB`` (default 256 MiB)."""
    raw = (env if env is not None else os.environ).get(MAX_REQUEST_MB_ENV, "")
    try:
        mb = float(raw) if raw.strip() else DEFAULT_MAX_REQUEST_MB
    except ValueError:
        mb = DEFAULT_MAX_REQUEST_MB
    return int(max(mb, 1.0) * 1024 * 1024)


def _handle_shutdown(signum: int, _frame: object) -> None:
    sig_name = signal.Signals(signum).name
    logger.info("Received shutdown signal", signal=sig_name)
    sys.exit(0)


async def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    from tool_registry.tools.blender.adapter import BlenderServer
    from tool_registry.tools.blender.config import BlenderConfig

    work_dir = os.environ.get("BLENDER_WORK_DIR", "/workspace")
    blender_binary = os.environ.get("BLENDER_BINARY", "blender")
    config = BlenderConfig(work_dir=work_dir, blender_binary=blender_binary)
    server = BlenderServer(config=config)

    logger.info(
        "Blender MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    port = int(os.environ.get("MCP_PORT", "8202"))
    await _start_http(server, port, work_dir, blender_binary)


async def _start_http(server: BlenderServer, port: int, work_dir: str, blender_binary: str) -> None:
    """Start an HTTP server that forwards JSON-RPC to the MCP server."""
    from aiohttp import web

    app = build_app(server, work_dir=work_dir, blender_binary=blender_binary)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    logger.info("Blender HTTP server starting", port=port, max_request_bytes=max_request_bytes())
    await site.start()

    # Keep running until shutdown signal
    await asyncio.Event().wait()


def build_app(
    server: Any,
    *,
    work_dir: str = "/workspace",
    blender_binary: str = "blender",
    max_bytes: int | None = None,
) -> Any:
    """The aiohttp app that forwards JSON-RPC on ``/mcp`` to ``server``.

    A body over ``max_bytes`` is answered with a JSON-RPC error that says so,
    never a plain-text page the caller cannot parse.
    """
    import json
    import shutil
    from pathlib import Path

    from aiohttp import web

    from mcp_core.context import context_from_headers, with_context
    from mcp_core.protocol import INVALID_REQUEST
    from tool_registry.mcp_server.handlers import make_error

    limit = max_request_bytes() if max_bytes is None else max_bytes

    async def handle_mcp(request: web.Request) -> web.Response:
        try:
            body = await request.text()
        except web.HTTPRequestEntityTooLarge:
            size = request.content_length
            message = (
                f"the request is {size if size is not None else 'over'} bytes; this adapter "
                f"reads at most {limit} bytes ({MAX_REQUEST_MB_ENV})"
            )
            logger.warning("blender_request_too_large", size_bytes=size, limit_bytes=limit)
            error = make_error("null", INVALID_REQUEST, message, {"details": message})
            return web.Response(text=json.dumps(error), status=413, content_type="application/json")
        ctx = context_from_headers(dict(request.headers))
        with with_context(ctx):
            response = await server.handle_request(body)
        return web.Response(text=response, content_type="application/json")

    async def handle_health(request: web.Request) -> web.Response:
        blender_available = shutil.which(blender_binary) is not None
        work_dir_exists = Path(work_dir).exists()
        status = "healthy" if blender_available and work_dir_exists else "degraded"
        body = {
            "adapter_id": getattr(server, "adapter_id", "blender"),
            "status": status,
            "version": getattr(server, "version", ""),
            "tools_available": len(getattr(server, "tool_ids", []) or []),
            "blender_available": blender_available,
            "work_dir": work_dir,
        }
        return web.Response(text=json.dumps(body), content_type="application/json")

    app = web.Application(client_max_size=limit)
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    asyncio.run(main())
