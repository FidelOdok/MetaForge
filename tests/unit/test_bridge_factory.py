"""Unit tests for ``skill_registry.bridge_factory.create_mcp_bridge`` (MET-306).

Stubs ``McpClient`` + ``Transport`` so the factory's dispatch logic can
be exercised without spawning subprocesses or opening sockets. The
real-subprocess path is covered in the MET-306 integration test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from skill_registry.bridge_factory import create_mcp_bridge
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.mcp_client_bridge import McpClientBridge


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "METAFORGE_MCP_BRIDGE",
        "METAFORGE_MCP_SERVER_URL",
        "METAFORGE_MCP_SERVER_CMD",
        "METAFORGE_MCP_READY_SIGNAL",
        "METAFORGE_REQUIRE_MCP",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Default behaviour
# ---------------------------------------------------------------------------


class TestDefaults:
    @pytest.mark.asyncio
    async def test_default_returns_fallback(self) -> None:
        fb = InMemoryMcpBridge()
        bridge = await create_mcp_bridge(fallback=fb)
        assert bridge is fb

    @pytest.mark.asyncio
    async def test_explicit_registry_mode_returns_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "registry")
        fb = InMemoryMcpBridge()
        bridge = await create_mcp_bridge(fallback=fb)
        assert bridge is fb

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "ouija")
        fb = InMemoryMcpBridge()
        bridge = await create_mcp_bridge(fallback=fb)
        assert bridge is fb


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


class TestConfigErrors:
    @pytest.mark.asyncio
    async def test_http_without_url_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "http")
        fb = InMemoryMcpBridge()
        bridge = await create_mcp_bridge(fallback=fb)
        assert bridge is fb

    @pytest.mark.asyncio
    async def test_stdio_without_cmd_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "stdio")
        fb = InMemoryMcpBridge()
        bridge = await create_mcp_bridge(fallback=fb)
        assert bridge is fb

    @pytest.mark.asyncio
    async def test_require_flag_raises_on_missing_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "http")
        monkeypatch.setenv("METAFORGE_REQUIRE_MCP", "true")
        with pytest.raises(RuntimeError, match="METAFORGE_MCP_SERVER_URL"):
            await create_mcp_bridge(fallback=InMemoryMcpBridge())


# ---------------------------------------------------------------------------
# HTTP success / connection failure
# ---------------------------------------------------------------------------


def _stub_transport(send_payload: str) -> Any:
    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.disconnect = AsyncMock()
    transport.send = AsyncMock(return_value=send_payload)
    transport.is_connected = MagicMock(return_value=True)
    return transport


class TestHttpMode:
    @pytest.mark.asyncio
    async def test_http_success_returns_client_bridge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "http")
        monkeypatch.setenv("METAFORGE_MCP_SERVER_URL", "http://localhost:9999")
        list_payload = (
            '{"jsonrpc":"2.0","id":"discover","result":{"tools":['
            '{"tool_id":"alpha.add","adapter_id":"alpha","name":"add",'
            '"description":"","capability":"math"}]}}'
        )
        transport = _stub_transport(list_payload)
        monkeypatch.setattr(
            "skill_registry.bridge_factory.HttpTransport", lambda url, **_kw: transport
        )
        bridge = await create_mcp_bridge(fallback=InMemoryMcpBridge())
        assert isinstance(bridge, McpClientBridge)
        assert await bridge.is_available("alpha.add")

    @pytest.mark.asyncio
    async def test_http_connection_failure_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "http")
        monkeypatch.setenv("METAFORGE_MCP_SERVER_URL", "http://localhost:9999")
        transport = MagicMock()
        transport.connect = AsyncMock(side_effect=ConnectionRefusedError())
        transport.disconnect = AsyncMock()
        monkeypatch.setattr(
            "skill_registry.bridge_factory.HttpTransport", lambda url, **_kw: transport
        )
        fb = InMemoryMcpBridge()
        bridge = await create_mcp_bridge(fallback=fb)
        assert bridge is fb

    @pytest.mark.asyncio
    async def test_require_flag_raises_on_connection_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "http")
        monkeypatch.setenv("METAFORGE_MCP_SERVER_URL", "http://localhost:9999")
        monkeypatch.setenv("METAFORGE_REQUIRE_MCP", "true")
        transport = MagicMock()
        transport.connect = AsyncMock(side_effect=ConnectionRefusedError())
        transport.disconnect = AsyncMock()
        monkeypatch.setattr(
            "skill_registry.bridge_factory.HttpTransport", lambda url, **_kw: transport
        )
        with pytest.raises(RuntimeError, match="failed to connect"):
            await create_mcp_bridge(fallback=InMemoryMcpBridge())
