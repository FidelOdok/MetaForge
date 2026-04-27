"""Unit tests for the hardened ``McpClientBridge`` (MET-306)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_core.protocol import ToolExecutionError, ToolUnavailableError
from mcp_core.schemas import ToolCallRequest, ToolCallResult
from skill_registry.mcp_bridge import McpToolError
from skill_registry.mcp_client_bridge import McpClientBridge


def _success(data: dict[str, Any] | None = None) -> ToolCallResult:
    return ToolCallResult(
        tool_id="x.y",
        status="success",
        data=data or {"ok": True},
        duration_ms=12.0,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestInvokeHappyPath:
    @pytest.mark.asyncio
    async def test_returns_data_on_success(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=_success({"answer": 42}))
        bridge = McpClientBridge(client)
        result = await bridge.invoke("x.y", {"q": 1})
        assert result == {"answer": 42}
        assert client.call_tool.await_count == 1

    @pytest.mark.asyncio
    async def test_passes_timeout_through_to_request(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=_success())
        bridge = McpClientBridge(client)
        await bridge.invoke("x.y", {}, timeout=5)
        sent: ToolCallRequest = client.call_tool.await_args.args[0]
        assert sent.timeout_seconds == 5


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_wraps_in_wait_for(self) -> None:
        client = MagicMock()

        async def _slow(_req: ToolCallRequest) -> ToolCallResult:
            await asyncio.sleep(2.0)
            return _success()

        client.call_tool = _slow
        bridge = McpClientBridge(client)
        with pytest.raises(McpToolError, match="timed out"):
            await bridge.invoke("x.y", {}, timeout=1)


# ---------------------------------------------------------------------------
# Server-side errors are NOT retried
# ---------------------------------------------------------------------------


class TestServerError:
    @pytest.mark.asyncio
    async def test_tool_execution_error_propagates(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(
            side_effect=ToolExecutionError(tool_id="x.y", details="bad input", duration_ms=5.0)
        )
        bridge = McpClientBridge(client)
        with pytest.raises(McpToolError, match="bad input"):
            await bridge.invoke("x.y", {})
        assert client.call_tool.await_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_non_success_status_raises(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(
            return_value=ToolCallResult(tool_id="x.y", status="failure", data={}, duration_ms=1.0)
        )
        bridge = McpClientBridge(client)
        with pytest.raises(McpToolError, match="status"):
            await bridge.invoke("x.y", {})


# ---------------------------------------------------------------------------
# Transport failure → retry with backoff
# ---------------------------------------------------------------------------


class TestRetry:
    @pytest.mark.asyncio
    async def test_recovers_after_transient_failure(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(
            side_effect=[
                ToolUnavailableError("x.y"),
                _success({"recovered": True}),
            ]
        )
        bridge = McpClientBridge(client, max_retries=2, backoff_initial=0.0)
        result = await bridge.invoke("x.y", {})
        assert result == {"recovered": True}
        assert client.call_tool.await_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(side_effect=ToolUnavailableError("x.y"))
        bridge = McpClientBridge(client, max_retries=2, backoff_initial=0.0)
        with pytest.raises(McpToolError, match="Transport unavailable"):
            await bridge.invoke("x.y", {})
        # Initial + 2 retries
        assert client.call_tool.await_count == 3

    @pytest.mark.asyncio
    async def test_broken_pipe_is_retried(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(side_effect=[BrokenPipeError("write failed"), _success()])
        bridge = McpClientBridge(client, max_retries=1, backoff_initial=0.0)
        result = await bridge.invoke("x.y", {})
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_is_available_handles_listing_failure(self) -> None:
        client = MagicMock()
        client.list_tools = AsyncMock(side_effect=RuntimeError("boom"))
        bridge = McpClientBridge(client)
        assert await bridge.is_available("anything") is False

    @pytest.mark.asyncio
    async def test_list_tools_capability_filter(self) -> None:
        from mcp_core.schemas import ToolManifest

        client = MagicMock()
        client.list_tools = AsyncMock(
            return_value=[
                ToolManifest(
                    tool_id="a.x",
                    adapter_id="a",
                    name="x",
                    description="",
                    capability="cad",
                ),
                ToolManifest(
                    tool_id="b.y",
                    adapter_id="b",
                    name="y",
                    description="",
                    capability="fea",
                ),
            ]
        )
        bridge = McpClientBridge(client)
        cad_only = await bridge.list_tools(capability="cad")
        assert {t["tool_id"] for t in cad_only} == {"a.x"}
