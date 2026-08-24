"""McpBridge backed by a ToolRegistry — routes tool calls to the correct adapter.

This bridge connects the skill layer to real tool adapters by looking up
which adapter owns each tool and delegating to the appropriate McpClient.
"""

from __future__ import annotations

from typing import Any

import structlog

from mcp_core.schemas import ToolCallRequest
from skill_registry.geometry_stash import GeometryStash
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
        # Commit-by-reference: remember freecad.export_model STEP so
        # twin.commit_geometry can be called with just (session_id, obj_id).
        # This is the gateway chat's dispatch seam (mirrors the sidecar's stash).
        self._geom_stash = GeometryStash()

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

        # Fill a commit-by-reference geometry commit from the last export.
        if tool_id == "twin.commit_geometry":
            had_explicit_blob = bool(params.get("step_base64"))
            filled = self._geom_stash.fill(params)
            # MET-642 S4 finding: this is the chat harness's actual dispatch
            # seam (unlike metaforge/mcp/server.py's, which is the standalone
            # sidecar used by external MCP clients) -- a miss here previously
            # surfaced only as commit_geometry's generic "no geometry" error,
            # with no way to tell whether the (session_id, obj_id) pair ever
            # matched a real export_model call.
            if not had_explicit_blob:
                event = (
                    "geometry_commit_by_reference"
                    if filled
                    else "geometry_commit_by_reference_miss"
                )
                logger.info(
                    event,
                    session_id=params.get("session_id"),
                    obj_id=params.get("obj_id"),
                )

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

        # Remember an export's STEP so a later commit can reference it.
        if tool_id == "freecad.export_model" and isinstance(result.data, dict):
            remembered = self._geom_stash.remember(params, result.data)
            logger.info(
                "geometry_export_remembered" if remembered else "geometry_export_not_remembered",
                session_id=params.get("session_id"),
                obj_id=params.get("obj_id"),
            )

        return result.data

    async def is_available(self, tool_id: str) -> bool:
        """Check if a tool is registered in the registry."""
        return self._registry.get_tool(tool_id) is not None

    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        """List available tools from the registry."""
        manifests = self._registry.list_tools(capability=capability)
        return [m.model_dump() for m in manifests]
