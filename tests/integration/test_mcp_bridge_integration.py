"""End-to-end gateway-side bridge integration test (MET-306).

Spawns the real ``python -m metaforge.mcp --transport stdio`` server
(the entrypoint shipped in MET-337), wires the gateway's
``create_mcp_bridge`` factory at it, and exercises the full external
MCP path: tool discovery via stdio, ``McpClientBridge.list_tools``
round-trip, ``is_available`` query.

Opt in with ``pytest --integration``. Requires the local Python env
to import the ``metaforge`` package (i.e. ``pip install -e .`` was
run after MET-337 landed).
"""

from __future__ import annotations

import sys

import pytest

from skill_registry.bridge_factory import create_mcp_bridge
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.mcp_client_bridge import McpClientBridge

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_factory_spawns_metaforge_mcp_and_lists_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge factory spawns the standalone MCP server and discovers tools."""
    monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "stdio")
    monkeypatch.setenv(
        "METAFORGE_MCP_SERVER_CMD",
        f"{sys.executable} -m metaforge.mcp --transport stdio",
    )
    monkeypatch.setenv("METAFORGE_ADAPTERS", "cadquery,calculix")

    bridge = await create_mcp_bridge(fallback=InMemoryMcpBridge())
    try:
        assert isinstance(bridge, McpClientBridge), (
            f"expected McpClientBridge, got {type(bridge).__name__}"
        )
        tools = await bridge.list_tools()
        # cadquery (7 tools) + calculix (4 tools) = 11; threshold is ≥7.
        assert len(tools) >= 7, f"expected ≥7 tools, got {len(tools)}"
        # A canonical tool exposed by both adapters round-trips through
        # the unified server.
        assert await bridge.is_available("cadquery.create_parametric")
        assert await bridge.is_available("calculix.run_fea")
    finally:
        if isinstance(bridge, McpClientBridge):
            await bridge._client.disconnect("metaforge")  # noqa: SLF001


@pytest.mark.asyncio
async def test_factory_falls_back_when_subprocess_command_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable command must fall back, not crash the gateway."""
    monkeypatch.setenv("METAFORGE_MCP_BRIDGE", "stdio")
    monkeypatch.setenv(
        "METAFORGE_MCP_SERVER_CMD",
        f"{sys.executable} -c 'import sys; sys.exit(1)'",
    )
    fallback = InMemoryMcpBridge()
    bridge = await create_mcp_bridge(fallback=fallback)
    assert bridge is fallback
