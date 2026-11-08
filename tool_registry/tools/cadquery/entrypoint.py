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

    _init_telemetry()

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


def _init_telemetry() -> None:
    """Export traces, logs and metrics (the script runner's among them) over OTLP.

    Best-effort: telemetry never stops the adapter from starting.
    """
    try:
        from observability.bootstrap import init_observability
        from observability.config import ObservabilityConfig, OtlpExporterConfig

        state = init_observability(
            ObservabilityConfig(
                service_name="metaforge-cadquery-adapter",
                environment=os.environ.get("METAFORGE_ENV", "development"),
                otlp=OtlpExporterConfig(
                    endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
                ),
            )
        )
        logger.info("cadquery_observability_initialised", active=state.is_active)
    except Exception as exc:  # noqa: BLE001 -- telemetry must not block the adapter
        logger.warning("cadquery_observability_init_failed", error=str(exc))


async def _start_http(server, port: int) -> None:
    """Start an HTTP server that forwards JSON-RPC to the MCP server."""
    from aiohttp import web

    from mcp_core.context import context_from_headers, with_context

    async def handle_mcp(request: web.Request) -> web.Response:
        body = await request.text()
        # MET-387: scope every /mcp call to the harness's context so
        # downstream handlers see project / actor via ``current_context()``.
        ctx = context_from_headers(dict(request.headers))
        with with_context(ctx):
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
