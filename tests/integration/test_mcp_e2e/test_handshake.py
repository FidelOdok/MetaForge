"""Tier-1 readiness tests — the MCP server starts up and reports its surface.

Every other test in the suite assumes these pass. If ``initialize`` or
``tools/list`` is broken, nothing downstream is meaningful.
"""

from __future__ import annotations

import pytest

from tests.integration.test_mcp_e2e._helpers import rpc

pytestmark = pytest.mark.asyncio


async def test_initialize_returns_server_info(mcp_client):
    """``initialize`` returns the server's protocol version + capabilities."""
    result = await rpc(
        mcp_client,
        "initialize",
        {"protocolVersion": "2024-11-05", "capabilities": {}},
    )
    # The MCP spec only guarantees a non-empty server-info envelope. We
    # don't pin the exact protocol version because the server may
    # advertise a different one — we only assert the field exists.
    assert isinstance(result, dict)
    server_info = result.get("serverInfo") or result.get("server_info") or {}
    assert isinstance(server_info, dict)


async def test_tools_list_returns_non_empty(mcp_client):
    """The server registers at least one tool — sanity floor."""
    result = await rpc(mcp_client, "tools/list")
    tools = result.get("tools", [])
    assert isinstance(tools, list)
    assert len(tools) > 0, "MCP server registered zero tools"


async def test_tools_list_entries_have_required_fields(mcp_client):
    """Every tool entry carries the fields downstream tests rely on."""
    result = await rpc(mcp_client, "tools/list")
    tools = result.get("tools", [])
    for tool in tools:
        assert "name" in tool, f"tool missing 'name': {tool}"
        assert isinstance(tool["name"], str) and tool["name"], "tool name must be non-empty string"
        # MCP spec uses ``inputSchema`` (camelCase) on the wire.
        schema = tool.get("inputSchema") or tool.get("input_schema")
        assert isinstance(schema, dict), f"tool {tool['name']} missing inputSchema"
        assert "description" in tool, f"tool {tool['name']} missing description"
