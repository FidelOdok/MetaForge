"""Unit tests for ``mcp_core.transports.StdioTransport`` (MET-306).

Spawns small Python one-liner subprocesses that echo-back stdin to
stdout. Avoids depending on the full MetaForge MCP server (covered in
the MET-340 integration test).
"""

from __future__ import annotations

import sys

import pytest

from mcp_core.transports import StdioTransport

_ECHO_LINES = "import sys\nfor line in sys.stdin: sys.stdout.write(line); sys.stdout.flush()\n"
_READY_THEN_ECHO = (
    "import sys\n"
    "sys.stderr.write('test-ready\\n'); sys.stderr.flush()\n"
    "for line in sys.stdin: sys.stdout.write(line); sys.stdout.flush()\n"
)


@pytest.mark.asyncio
async def test_send_round_trip() -> None:
    transport = StdioTransport(
        command=[sys.executable, "-u", "-c", _ECHO_LINES],
    )
    await transport.connect()
    try:
        assert transport.is_connected()
        echoed = await transport.send('{"hello":"world"}')
        assert echoed == '{"hello":"world"}'
    finally:
        await transport.disconnect()
    assert not transport.is_connected()


@pytest.mark.asyncio
async def test_ready_signal_unblocks_first_send() -> None:
    transport = StdioTransport(
        command=[sys.executable, "-u", "-c", _READY_THEN_ECHO],
        ready_signal="test-ready",
        ready_timeout=10.0,
    )
    await transport.connect()
    try:
        echoed = await transport.send("ping")
        assert echoed == "ping"
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_ready_timeout_raises() -> None:
    # Subprocess never writes the expected signal — connect must time out.
    transport = StdioTransport(
        command=[
            sys.executable,
            "-u",
            "-c",
            "import sys; sys.stderr.write('different-line\\n'); sys.stderr.flush();"
            " import time; time.sleep(5)",
        ],
        ready_signal="missing-signal",
        ready_timeout=0.5,
    )
    with pytest.raises(TimeoutError):
        await transport.connect()
    await transport.disconnect()


@pytest.mark.asyncio
async def test_subprocess_exit_breaks_send() -> None:
    transport = StdioTransport(
        command=[sys.executable, "-u", "-c", "import sys; sys.exit(0)"],
    )
    await transport.connect()
    try:
        with pytest.raises(RuntimeError, match="closed stdout"):
            await transport.send("anything")
    finally:
        await transport.disconnect()
