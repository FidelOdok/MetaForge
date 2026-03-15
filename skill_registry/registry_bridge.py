"""McpBridge backed by a ToolRegistry — routes tool calls to the correct adapter.

This bridge connects the skill layer to real tool adapters by looking up
which adapter owns each tool and delegating to the appropriate McpClient.
"""

from __future__ import annotations

from typing import Any

import structlog

from mcp_core.schemas import ToolCallRequest
from skill_registry.mcp_bridge import McpBridge, McpToolError
from tool_registry.registry import ToolRegistry

logger = structlog.get_logger(__name__)


class RegistryMcpBridge(McpBridge):
    """McpBridge that routes tool calls through a ToolRegistry.

    Unlike McpClientBridge (which wraps a single McpClient), this bridge
    supports multiple adapters by looking up the adapter for each tool_id
    and dispatching to the correct McpClient.

    Example::

        registry = await bootstrap_tool_registry()
        bridge = RegistryMcpBridge(registry)
        result = await bridge.invoke("cadquery.create_parametric", {...})
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def invoke(
        self,
        tool_id: str,
        params: dict[str, Any],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Invoke a tool by routing to the correct adapter's McpClient."""
        adapter_id = self._registry.get_adapter_for_tool(tool_id)
        if adapter_id is None:
            raise McpToolError(tool_id, f"No adapter found for tool '{tool_id}'")

        client = self._registry.get_client(adapter_id)
        if client is None:
            raise McpToolError(tool_id, f"No MCP client for adapter '{adapter_id}'")

        request = ToolCallRequest(
            tool_id=tool_id,
            arguments=params,
            timeout_seconds=timeout or 120,
        )

        try:
            result = await client.call_tool(request)
        except Exception as exc:
            raise McpToolError(tool_id, str(exc)) from exc

        if result.status != "success":
            raise McpToolError(tool_id, f"Tool returned status: {result.status}")

        return result.data

    async def is_available(self, tool_id: str) -> bool:
        """Check if a tool is registered in the registry."""
        return self._registry.get_tool(tool_id) is not None

    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        """List available tools from the registry."""
        manifests = self._registry.list_tools(capability=capability)
        return [m.model_dump() for m in manifests]
