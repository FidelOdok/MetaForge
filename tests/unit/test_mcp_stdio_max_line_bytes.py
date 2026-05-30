"""Unit tests for MET-450 — stdio readline byte limit.

The bug: ``asyncio.StreamReader``'s default ``limit`` is 64 KiB. A
single ``knowledge.ingest`` JSON-RPC line easily exceeds that, so the
stdio loop in ``metaforge.mcp.__main__.run_stdio()`` crashed with
``ValueError: Separator is found, but chunk is longer than limit`` and
the harness collapsed with no JSON-RPC response.

The fix: instantiate ``StreamReader(limit=<16 MiB by default>)`` and
let ops tune the cap with ``METAFORGE_MCP_MAX_LINE_BYTES``.

These tests pin the contract of the new ``_stdio_max_line_bytes()``
helper: env-driven, sane fallbacks on garbage / non-positive values,
and a 16 MiB default that comfortably swallows real ingest payloads.
"""

from __future__ import annotations

import asyncio

import pytest

from metaforge.mcp.__main__ import (
    _DEFAULT_STDIO_MAX_LINE_BYTES,
    _stdio_max_line_bytes,
)


class TestStdioMaxLineBytes:
    """Tests for ``_stdio_max_line_bytes()`` — MET-450."""

    def test_default_is_at_least_16_mib(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset env → 16 MiB default (well above any real ingest payload)."""
        monkeypatch.delenv("METAFORGE_MCP_MAX_LINE_BYTES", raising=False)
        assert _stdio_max_line_bytes() >= 16 * 1024 * 1024
        assert _stdio_max_line_bytes() == _DEFAULT_STDIO_MAX_LINE_BYTES

    def test_env_override_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid env value replaces the default."""
        monkeypatch.setenv("METAFORGE_MCP_MAX_LINE_BYTES", str(4 * 1024 * 1024))
        assert _stdio_max_line_bytes() == 4 * 1024 * 1024

    def test_empty_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty / whitespace env value falls back to default — not 0."""
        monkeypatch.setenv("METAFORGE_MCP_MAX_LINE_BYTES", "   ")
        assert _stdio_max_line_bytes() == _DEFAULT_STDIO_MAX_LINE_BYTES

    def test_garbage_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unparseable env value falls back to default (and logs a warning)."""
        monkeypatch.setenv("METAFORGE_MCP_MAX_LINE_BYTES", "not-an-int")
        assert _stdio_max_line_bytes() == _DEFAULT_STDIO_MAX_LINE_BYTES

    @pytest.mark.parametrize("value", ["0", "-1", "-999999"])
    def test_non_positive_env_falls_back(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        """0 or negative cap would deadlock the loop — fall back to default."""
        monkeypatch.setenv("METAFORGE_MCP_MAX_LINE_BYTES", value)
        assert _stdio_max_line_bytes() == _DEFAULT_STDIO_MAX_LINE_BYTES


class TestStreamReaderAcceptsLargeLines:
    """End-to-end check that the configured limit actually applies.

    Skips the stdin plumbing of ``run_stdio()`` — that path is exercised
    in the live MCP suite. Here we just prove that an
    ``asyncio.StreamReader`` constructed with the helper's value reads
    a 200 KB line cleanly, which is the bug's reproduction scenario.
    """

    async def test_stream_reader_with_default_cap_reads_200kb_line(self) -> None:
        reader = asyncio.StreamReader(limit=_DEFAULT_STDIO_MAX_LINE_BYTES)
        big_line = ("x" * 200_000) + "\n"
        reader.feed_data(big_line.encode("utf-8"))
        reader.feed_eof()

        # This is exactly the call site (`run_stdio`) was raising on
        # before MET-450 — a 200 KB payload now flows through.
        line = await reader.readline()
        assert len(line) == 200_001  # 200_000 'x' + '\n'

    async def test_stream_reader_with_default_limit_explodes_on_70kb(self) -> None:
        """Negative case — pin the bug. The asyncio default still does
        explode, which is why we have to set a custom ``limit``.
        """
        reader = asyncio.StreamReader()  # default 64 KiB
        too_big = ("x" * 70_000) + "\n"
        reader.feed_data(too_big.encode("utf-8"))
        reader.feed_eof()

        with pytest.raises(ValueError, match="chunk is longer than limit"):
            await reader.readline()
