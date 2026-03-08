"""KiCad MCP adapter entrypoint — starts the KiCad tool server in stdio mode.

This script is the Docker container entrypoint. It initializes the KiCad
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
    """Start the KiCad MCP adapter server."""
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    # Import here to ensure PYTHONPATH is set correctly
    from tool_registry.tools.kicad.adapter import KicadServer
    from tool_registry.tools.kicad.config import KicadConfig

    # Build config from environment
    work_dir = os.environ.get("KICAD_WORK_DIR", "/workspace")
    kicad_cli = os.environ.get("KICAD_CLI_PATH", "kicad-cli")

    config = KicadConfig(
        kicad_cli=kicad_cli,
        work_dir=work_dir,
    )

    server = KicadServer(config=config)

    logger.info(
        "KiCad MCP adapter starting",
        adapter_id=server.adapter_id,
        version=server.version,
        tools=server.tool_ids,
        work_dir=work_dir,
    )

    await server.start_stdio()


if __name__ == "__main__":
    asyncio.run(main())
