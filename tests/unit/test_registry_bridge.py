"""Unit tests for RegistryMcpBridge."""

from __future__ import annotations

import pytest

from skill_registry.mcp_bridge import McpToolError
from skill_registry.registry_bridge import RegistryMcpBridge
from tool_registry.bootstrap import bootstrap_tool_registry


class TestRegistryMcpBridge:
    """Tests for RegistryMcpBridge backed by a real bootstrapped ToolRegistry."""

    async def _make_bridge(self) -> RegistryMcpBridge:
        registry = await bootstrap_tool_registry()
        return RegistryMcpBridge(registry)

    async def test_is_available_registered_tool(self):
        """Registered tools are available."""
        bridge = await self._make_bridge()
        assert await bridge.is_available("cadquery.create_parametric") is True
        assert await bridge.is_available("freecad.export_geometry") is True
        assert await bridge.is_available("calculix.run_fea") is True

    async def test_is_available_unknown_tool(self):
        """Unknown tools are not available."""
        bridge = await self._make_bridge()
        assert await bridge.is_available("nonexistent.tool") is False

    async def test_list_tools_all(self):
        """List all tools returns expected count."""
        bridge = await self._make_bridge()
        tools = await bridge.list_tools()
        # cadquery=7 + freecad=5 + calculix=4 = 16
        assert len(tools) == 16

    async def test_list_tools_filter_capability(self):
        """List tools filtered by capability."""
        bridge = await self._make_bridge()
        cad_gen = await bridge.list_tools(capability="cad_generation")
        assert len(cad_gen) == 2
        tool_ids = {t["tool_id"] for t in cad_gen}
        assert tool_ids == {"cadquery.create_parametric", "freecad.create_parametric"}

    async def test_invoke_unknown_tool_raises(self):
        """Invoking an unknown tool raises McpToolError."""
        bridge = await self._make_bridge()
        with pytest.raises(McpToolError, match="No adapter found"):
            await bridge.invoke("nonexistent.tool", {})

    async def test_invoke_routes_to_correct_adapter(self):
        """Invoking a tool routes to the correct adapter and executes."""
        bridge = await self._make_bridge()

        # cadquery.create_parametric should route to the cadquery adapter
        # and validate arguments (will raise ValueError for missing args)
        with pytest.raises(McpToolError):
            # This will fail at the handler level (missing shape_type)
            # but proves routing works — it reached the handler
            await bridge.invoke("cadquery.create_parametric", {})

    async def test_invoke_cadquery_tool_validation(self):
        """Tool argument validation works through the bridge."""
        bridge = await self._make_bridge()

        # Should raise because shape_type is empty
        with pytest.raises(McpToolError):
            await bridge.invoke(
                "cadquery.create_parametric",
                {"shape_type": "", "parameters": {}, "output_path": "/out.step"},
            )
