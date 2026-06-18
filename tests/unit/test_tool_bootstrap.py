"""Unit tests for tool adapter bootstrap."""

from __future__ import annotations

import os
from unittest.mock import patch

from tool_registry.bootstrap import (
    _ADAPTER_REGISTRY,
    _is_adapter_enabled,
    bootstrap_tool_registry,
)
from tool_registry.registry import ToolRegistry


class TestAdapterEnabled:
    """Tests for _is_adapter_enabled() environment variable logic."""

    def test_default_enabled(self):
        """All adapters are enabled by default."""
        with patch.dict(os.environ, {}, clear=True):
            assert _is_adapter_enabled("cadquery") is True
            assert _is_adapter_enabled("freecad") is True
            assert _is_adapter_enabled("calculix") is True

    def test_per_adapter_disable(self):
        """Per-adapter toggle disables a specific adapter."""
        with patch.dict(os.environ, {"METAFORGE_ADAPTER_CADQUERY_ENABLED": "false"}):
            assert _is_adapter_enabled("cadquery") is False
            assert _is_adapter_enabled("freecad") is True

    def test_per_adapter_enable(self):
        """Per-adapter toggle explicitly enables."""
        with patch.dict(os.environ, {"METAFORGE_ADAPTER_FREECAD_ENABLED": "true"}):
            assert _is_adapter_enabled("freecad") is True

    def test_global_allowlist(self):
        """METAFORGE_ADAPTERS restricts which adapters are enabled."""
        with patch.dict(os.environ, {"METAFORGE_ADAPTERS": "cadquery,calculix"}):
            assert _is_adapter_enabled("cadquery") is True
            assert _is_adapter_enabled("calculix") is True
            assert _is_adapter_enabled("freecad") is False

    def test_per_adapter_overrides_global(self):
        """Per-adapter toggle takes precedence over global list."""
        env = {
            "METAFORGE_ADAPTERS": "cadquery",
            "METAFORGE_ADAPTER_CADQUERY_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            # Per-adapter disable wins over global enable
            assert _is_adapter_enabled("cadquery") is False


class TestAdapterRegistry:
    """Tests for the known adapter registry."""

    def test_known_adapters(self):
        """All expected adapters are in the registry."""
        assert "cadquery" in _ADAPTER_REGISTRY
        assert "freecad" in _ADAPTER_REGISTRY
        assert "calculix" in _ADAPTER_REGISTRY

    def test_adapter_spec_fields(self):
        """Each adapter spec has required fields."""
        for adapter_id, spec in _ADAPTER_REGISTRY.items():
            assert "module" in spec, f"{adapter_id} missing module"
            assert "class" in spec, f"{adapter_id} missing class"
            assert "config_module" in spec, f"{adapter_id} missing config_module"
            assert "config_class" in spec, f"{adapter_id} missing config_class"


class TestBootstrapToolRegistry:
    """Tests for bootstrap_tool_registry()."""

    async def test_bootstrap_all_adapters(self):
        """Bootstrap registers all available adapters.

        Post-MET-478: KiCad joined cadquery/freecad/calculix in the
        bootstrap registry (cadquery=7, freecad=5, calculix=4,
        kicad=6 = 22 tools across 4 adapters).
        """
        registry = await bootstrap_tool_registry()

        assert isinstance(registry, ToolRegistry)
        adapters = registry.list_adapters()
        assert len(adapters) == 4
        adapter_ids = {a.adapter_id for a in adapters}
        assert adapter_ids == {"cadquery", "freecad", "calculix", "kicad"}

    async def test_bootstrap_with_existing_registry(self):
        """Bootstrap populates an existing registry instance."""
        registry = ToolRegistry()
        result = await bootstrap_tool_registry(registry=registry)
        assert result is registry
        assert len(registry.list_adapters()) == 4

    async def test_bootstrap_specific_adapters(self):
        """Bootstrap only registers specified adapter IDs."""
        registry = await bootstrap_tool_registry(adapter_ids=["cadquery"])

        adapters = registry.list_adapters()
        assert len(adapters) == 1
        assert adapters[0].adapter_id == "cadquery"

    async def test_bootstrap_disabled_adapter_skipped(self):
        """Disabled adapters are skipped."""
        env = {"METAFORGE_ADAPTER_FREECAD_ENABLED": "false"}
        with patch.dict(os.environ, env):
            registry = await bootstrap_tool_registry()

        adapter_ids = {a.adapter_id for a in registry.list_adapters()}
        assert "freecad" not in adapter_ids
        assert "cadquery" in adapter_ids
        assert "calculix" in adapter_ids
        assert "kicad" in adapter_ids

    async def test_bootstrap_unknown_adapter_id(self):
        """Unknown adapter IDs are reported as failed, not crash."""
        registry = await bootstrap_tool_registry(adapter_ids=["nonexistent"])
        assert len(registry.list_adapters()) == 0

    async def test_remote_url_unreachable_falls_back_to_in_process(self):
        """MET-477 G2: when ``METAFORGE_ADAPTER_<ID>_URL`` points at a
        host that can't be reached, bootstrap MUST register the
        in-process adapter rather than marking it failed.

        The MET-477 smoke caught this on fidel-dev: a forward-compatible
        ``METAFORGE_ADAPTER_CADQUERY_URL=http://cadquery-adapter:8101``
        in the gateway env caused the in-container MCP server to skip
        the cadquery adapter entirely because the remote container
        wasn't deployed. The fix dropped through to the local
        ``_create_adapter`` path on remote-fetch failure.
        """
        env = {
            "METAFORGE_ADAPTER_CADQUERY_URL": (
                "http://nonexistent-host-that-does-not-resolve.invalid:9999"
            ),
        }
        with patch.dict(os.environ, env):
            registry = await bootstrap_tool_registry(adapter_ids=["cadquery"])

        adapter_ids = {a.adapter_id for a in registry.list_adapters()}
        assert "cadquery" in adapter_ids, (
            "remote-adapter fetch failed but the in-process fallback didn't fire — G2 regression"
        )
        # And the cadquery tools should be live, not just the adapter shell.
        tool_ids = {t.tool_id for t in registry.list_tools()}
        assert any(tid.startswith("cadquery.") for tid in tool_ids), (
            "in-process cadquery adapter registered but exposed no tools"
        )

    async def test_bootstrap_tool_count(self):
        """Verify total tool count across all adapters.

        Post-MET-528: cadquery=7 + freecad=13 + calculix=4 + kicad=6 = 30.
        freecad gained the 8 stateful PartDesign authoring tools (open_session,
        close_session, describe_session, create_primitive, create_body,
        create_sketch, pad_sketch, export_model) on top of the 5 stateless ones.
        """
        registry = await bootstrap_tool_registry()

        tools = registry.list_tools()
        assert len(tools) == 43

    async def test_bootstrap_capability_discovery(self):
        """Bootstrapped tools can be discovered by capability."""
        registry = await bootstrap_tool_registry()

        cad_gen = registry.find_tools_by_capability("cad_generation")
        assert len(cad_gen) == 2  # cadquery + freecad

    async def test_bootstrap_health_check(self):
        """Health check works on bootstrapped adapters."""
        registry = await bootstrap_tool_registry(adapter_ids=["cadquery"])

        health = await registry.check_health("cadquery")
        assert health.status == "healthy"
        assert health.tools_available == 7
