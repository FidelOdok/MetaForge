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


@pytest.mark.asyncio
async def test_read_stderr_recovers_crash_diagnostic() -> None:
    """FORGE-221: a crashed child's stderr (e.g. a faulthandler dump) must
    still be recoverable after send() reports the process died."""
    transport = StdioTransport(
        command=[
            sys.executable,
            "-u",
            "-c",
            "import sys; sys.stderr.write('boom: simulated crash\\n'); sys.stderr.flush();"
            " sys.exit(0)",
        ],
    )
    await transport.connect()
    try:
        with pytest.raises(RuntimeError, match="closed stdout"):
            await transport.send("anything")
        stderr = await transport.read_stderr()
        assert b"boom: simulated crash" in stderr
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_read_stderr_empty_when_nothing_buffered() -> None:
    transport = StdioTransport(
        command=[sys.executable, "-u", "-c", _ECHO_LINES],
    )
    await transport.connect()
    try:
        assert await transport.read_stderr() == b""
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_default_limit_overruns_on_a_large_reply() -> None:
    """FORGE-238: reproduces the reported bug -- asyncio's own readline()
    default (64 KiB) trips ``LimitOverrunError`` on one big response line,
    exactly what a multi-part STEP export inlined as base64 hits. Locks in
    the *failure* so the next test's fix is provably doing something."""
    transport = StdioTransport(
        command=[sys.executable, "-u", "-c", _ECHO_LINES],
    )
    await transport.connect()
    try:
        oversized = "x" * (128 * 1024)  # well past the 64 KiB default
        with pytest.raises(ValueError, match="exceed(s|ed)? the limit|longer than limit"):
            await transport.send(oversized)
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_custom_limit_accepts_a_reply_the_default_would_reject() -> None:
    """FORGE-238: the fix -- a caller with large replies (FreecadWorkerPool)
    passes a bigger ``limit`` and the same oversized line round-trips fine."""
    transport = StdioTransport(
        command=[sys.executable, "-u", "-c", _ECHO_LINES],
        limit=1024 * 1024,
    )
    await transport.connect()
    try:
        oversized = "x" * (128 * 1024)
        echoed = await transport.send(oversized)
        assert echoed == oversized
    finally:
        await transport.disconnect()
