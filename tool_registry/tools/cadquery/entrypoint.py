"""CadQuery MCP adapter entrypoint -- starts the CadQuery tool server in stdio mode.

This script is the Docker container entrypoint. It initializes the CadQuery
MCP server and listens for JSON-RPC requests on stdin, writing responses
to stdout (MCP stdio transport).
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
    """Start the CadQuery MCP adapter server."""
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

    await server.start_stdio()


if __name__ == "__main__":
    asyncio.run(main())
