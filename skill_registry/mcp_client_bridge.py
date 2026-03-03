"""Concrete McpBridge that delegates to an McpClient for real tool invocation."""

from __future__ import annotations

from typing import Any

from mcp_core.client import McpClient
from mcp_core.schemas import ToolCallRequest
from skill_registry.mcp_bridge import McpBridge, McpToolError


class McpClientBridge(McpBridge):
    """Bridge from McpBridge (skill/agent interface) to McpClient (protocol layer).

    This connects the high-level skill system to the real MCP protocol client,
    translating between the two APIs.

    Example::

        client = McpClient()
        await client.connect("calculix", transport)
        bridge = McpClientBridge(client)
        result = await bridge.invoke("calculix.run_fea", {"mesh_file": "..."})
    """

    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def invoke(
        self,
        tool_id: str,
        params: dict[str, Any],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Invoke a tool through the MCP client."""
        request = ToolCallRequest(
            tool_id=tool_id,
            arguments=params,
            timeout_seconds=timeout or 120,
        )
        try:
            result = await self._client.call_tool(request)
        except Exception as exc:
            raise McpToolError(tool_id, str(exc)) from exc

        if result.status != "success":
            raise McpToolError(tool_id, f"Tool returned status: {result.status}")

        return result.data

    async def is_available(self, tool_id: str) -> bool:
        """Check if a tool is registered in the MCP client."""
        tools = await self._client.list_tools()
        return any(t.tool_id == tool_id for t in tools)

    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        """List available tools from the MCP client."""
        manifests = await self._client.list_tools()
        result = [m.model_dump() for m in manifests]
        if capability is not None:
            result = [t for t in result if t.get("capability") == capability]
        return result
