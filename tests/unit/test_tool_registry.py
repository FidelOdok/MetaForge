"""Tests for the tool registry, execution engine, and tool metadata models."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from mcp_core.protocol import ToolExecutionError, ToolTimeoutError, ToolUnavailableError
from mcp_core.schemas import ToolCallRequest
from tool_registry.execution_engine import ExecutionEngine
from tool_registry.registry import ToolRegistry
from tool_registry.tool_metadata import AdapterInfo, AdapterStatus, ToolCapability
from tool_registry.tools.calculix.adapter import CalculixServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_calculix_server() -> CalculixServer:
    """Create a CalculiX server with mocked solver methods."""
    server = CalculixServer()
    server._execute_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "max_von_mises": {"bracket_body": 145.2, "bracket_mount": 89.7},
            "solver_time": 12.5,
            "mesh_elements": 45000,
        }
    )
    server._execute_thermal_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "max_temperature": 85.3,
            "min_temperature": 22.1,
            "temperature_distribution": {"zone_a": 85.3, "zone_b": 45.6},
            "solver_time": 8.2,
        }
    )
    server._validate_mesh_file = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "valid": True,
            "element_count": 45000,
            "node_count": 12000,
            "max_aspect_ratio": 3.2,
            "issues": [],
        }
    )
    return server


# ---------------------------------------------------------------------------
# TestToolMetadata
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_adapter_status_enum_values(self) -> None:
        assert AdapterStatus.REGISTERED == "registered"
        assert AdapterStatus.CONNECTED == "connected"
        assert AdapterStatus.DEGRADED == "degraded"
        assert AdapterStatus.DISCONNECTED == "disconnected"
        assert AdapterStatus.ERROR == "error"

    def test_adapter_info_defaults(self) -> None:
        info = AdapterInfo(adapter_id="test", version="1.0.0")
        assert info.adapter_id == "test"
        assert info.version == "1.0.0"
        assert info.status == AdapterStatus.REGISTERED
        assert info.tools == []
        assert info.last_health_check is None
        assert info.health_check_interval_seconds == 60
        assert info.error_message is None
        assert info.registered_at is not None

    def test_adapter_info_custom_values(self) -> None:
        now = datetime.now(UTC)
        info = AdapterInfo(
            adapter_id="calculix",
            version="0.1.0",
            status=AdapterStatus.CONNECTED,
            last_health_check=now,
            health_check_interval_seconds=30,
            error_message=None,
        )
        assert info.adapter_id == "calculix"
        assert info.version == "0.1.0"
        assert info.status == AdapterStatus.CONNECTED
        assert info.last_health_check == now
        assert info.health_check_interval_seconds == 30

    def test_tool_capability_model(self) -> None:
        cap = ToolCapability(
            capability="stress_analysis",
            tool_ids=["calculix.run_fea"],
            description="Finite element stress analysis",
        )
        assert cap.capability == "stress_analysis"
        assert cap.tool_ids == ["calculix.run_fea"]
        assert cap.description == "Finite element stress analysis"

    def test_tool_capability_defaults(self) -> None:
        cap = ToolCapability(capability="mesh_validation")
        assert cap.tool_ids == []
        assert cap.description == ""


# ---------------------------------------------------------------------------
# TestToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    async def test_register_adapter(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        adapter_info = await registry.register_adapter(server)

        assert adapter_info.adapter_id == "calculix"
        assert adapter_info.version == "0.1.0"
        assert adapter_info.status == AdapterStatus.CONNECTED
        assert len(adapter_info.tools) == 3

    async def test_register_adapter_populates_tools(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        expected_ids = {"calculix.run_fea", "calculix.run_thermal", "calculix.validate_mesh"}
        actual_ids = {t.tool_id for t in registry.list_tools()}
        assert actual_ids == expected_ids

    async def test_unregister_adapter(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        await registry.unregister_adapter("calculix")

        assert registry.get_adapter("calculix") is None
        assert registry.list_tools() == []
        assert registry.list_adapters() == []

    async def test_unregister_nonexistent_adapter(self) -> None:
        """Unregistering a non-existent adapter does not raise."""
        registry = ToolRegistry()
        await registry.unregister_adapter("nonexistent")
        # Should complete without error

    async def test_get_adapter_not_found_returns_none(self) -> None:
        registry = ToolRegistry()
        assert registry.get_adapter("nonexistent") is None

    async def test_list_adapters(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        adapters = registry.list_adapters()
        assert len(adapters) == 1
        assert adapters[0].adapter_id == "calculix"

    async def test_get_tool(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        tool = registry.get_tool("calculix.run_fea")
        assert tool is not None
        assert tool.tool_id == "calculix.run_fea"
        assert tool.capability == "stress_analysis"

    async def test_get_tool_not_found_returns_none(self) -> None:
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent.tool") is None

    async def test_list_tools_all(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        tools = registry.list_tools()
        assert len(tools) == 3

    async def test_list_tools_by_capability(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        tools = registry.list_tools(capability="stress_analysis")
        assert len(tools) == 1
        assert tools[0].tool_id == "calculix.run_fea"

    async def test_list_tools_by_capability_no_match(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        tools = registry.list_tools(capability="pcb_validation")
        assert tools == []

    async def test_list_tools_by_phase(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        # All CalculiX tools are phase 1
        tools = registry.list_tools(phase=1)
        assert len(tools) == 3

        tools = registry.list_tools(phase=2)
        assert tools == []

    async def test_find_tools_by_capability(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        tools = registry.find_tools_by_capability("thermal_analysis")
        assert len(tools) == 1
        assert tools[0].tool_id == "calculix.run_thermal"

    async def test_list_capabilities(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        capabilities = registry.list_capabilities()
        assert len(capabilities) == 3
        capability_names = {c.capability for c in capabilities}
        assert capability_names == {"stress_analysis", "thermal_analysis", "mesh_validation"}

        # Check that each capability references the correct tool
        for cap in capabilities:
            assert len(cap.tool_ids) == 1

    async def test_check_health(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        health = await registry.check_health("calculix")
        assert health.status == "healthy"
        assert health.adapter_id == "calculix"
        assert health.version == "0.1.0"
        assert health.tools_available == 3

        # Verify adapter status was updated
        adapter = registry.get_adapter("calculix")
        assert adapter is not None
        assert adapter.status == AdapterStatus.CONNECTED
        assert adapter.last_health_check is not None

    async def test_check_health_unknown_adapter(self) -> None:
        registry = ToolRegistry()
        health = await registry.check_health("nonexistent")
        assert health.status == "unhealthy"
        assert health.adapter_id == "nonexistent"
        assert health.tools_available == 0

    async def test_check_all_health(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        results = await registry.check_all_health()
        assert "calculix" in results
        assert results["calculix"].status == "healthy"

    async def test_register_duplicate_adapter_updates(self) -> None:
        """Re-registering the same adapter should update existing entry."""
        registry = ToolRegistry()
        server = _create_calculix_server()

        info1 = await registry.register_adapter(server)
        info2 = await registry.register_adapter(server)

        # Should still have exactly one adapter
        assert len(registry.list_adapters()) == 1
        # Tools should not be duplicated
        assert len(registry.list_tools()) == 3
        # Both infos should reference same adapter
        assert info1.adapter_id == info2.adapter_id

    async def test_get_adapter_for_tool(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        assert registry.get_adapter_for_tool("calculix.run_fea") == "calculix"
        assert registry.get_adapter_for_tool("nonexistent.tool") is None

    async def test_get_client(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        client = registry.get_client("calculix")
        assert client is not None
        assert registry.get_client("nonexistent") is None


# ---------------------------------------------------------------------------
# TestExecutionEngine
# ---------------------------------------------------------------------------


class TestExecutionEngine:
    async def test_execute_success(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry)
        result = await engine.execute(
            tool_id="calculix.run_fea",
            arguments={
                "mesh_file": "/models/bracket.inp",
                "load_case": "gravity_1g",
                "analysis_type": "static_stress",
            },
        )

        assert result.tool_id == "calculix.run_fea"
        assert result.status == "success"
        assert result.data["max_von_mises"]["bracket_body"] == 145.2
        assert result.duration_ms > 0

    async def test_execute_tool_not_found(self) -> None:
        registry = ToolRegistry()
        engine = ExecutionEngine(registry)

        with pytest.raises(ToolUnavailableError):
            await engine.execute(
                tool_id="nonexistent.tool",
                arguments={},
            )

    async def test_execute_timeout(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()

        # Make the solver hang forever
        async def _slow_solver(*args, **kwargs):  # type: ignore[no-untyped-def]
            import asyncio

            await asyncio.sleep(100)
            return {}

        server._execute_solver = _slow_solver  # type: ignore[method-assign]
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry, default_timeout=0.1)
        with pytest.raises(ToolTimeoutError):
            await engine.execute(
                tool_id="calculix.run_fea",
                arguments={
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "lc1",
                    "analysis_type": "static_stress",
                },
            )

    async def test_execute_with_retry_on_failure(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()

        call_count = 0

        async def _failing_then_succeeding(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Solver crashed")
            return {
                "max_von_mises": {"region_a": 50.0},
                "solver_time": 5.0,
                "mesh_elements": 1000,
            }

        server._execute_solver = _failing_then_succeeding  # type: ignore[method-assign]
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry, max_retries=1)
        result = await engine.execute(
            tool_id="calculix.run_fea",
            arguments={
                "mesh_file": "/models/bracket.inp",
                "load_case": "lc1",
                "analysis_type": "static_stress",
            },
        )

        # First call fails (wrapped as ToolExecutionError by McpClient),
        # but the ToolExecutionError from handler gets wrapped by the server
        # as a JSON-RPC error, which McpClient then raises as ToolExecutionError.
        # The second call should succeed.
        assert result.status == "success"
        assert call_count == 2

    async def test_execute_with_retry_all_fail(self) -> None:
        """When all retries fail, the last error is raised."""
        registry = ToolRegistry()
        server = _create_calculix_server()

        async def _always_failing(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("Solver always crashes")

        server._execute_solver = _always_failing  # type: ignore[method-assign]
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry, max_retries=1)
        with pytest.raises(ToolExecutionError):
            await engine.execute(
                tool_id="calculix.run_fea",
                arguments={
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "lc1",
                    "analysis_type": "static_stress",
                },
            )

    async def test_execute_batch(self) -> None:
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry)
        calls = [
            ToolCallRequest(
                tool_id="calculix.run_fea",
                arguments={
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "gravity_1g",
                    "analysis_type": "static_stress",
                },
            ),
            ToolCallRequest(
                tool_id="calculix.validate_mesh",
                arguments={"mesh_file": "/models/bracket.inp"},
            ),
        ]

        results = await engine.execute_batch(calls)
        assert len(results) == 2
        assert results[0].tool_id == "calculix.run_fea"
        assert results[0].status == "success"
        assert results[1].tool_id == "calculix.validate_mesh"
        assert results[1].status == "success"

    async def test_execute_custom_timeout(self) -> None:
        """Custom timeout overrides the default."""
        registry = ToolRegistry()
        server = _create_calculix_server()
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry, default_timeout=0.001)
        # With a generous custom timeout, the call should succeed
        result = await engine.execute(
            tool_id="calculix.validate_mesh",
            arguments={"mesh_file": "/models/bracket.inp"},
            timeout=30.0,
        )
        assert result.status == "success"

    async def test_execute_no_retries(self) -> None:
        """With max_retries=0, failures are raised immediately."""
        registry = ToolRegistry()
        server = _create_calculix_server()

        async def _failing(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("Solver crashed")

        server._execute_solver = _failing  # type: ignore[method-assign]
        await registry.register_adapter(server)

        engine = ExecutionEngine(registry, max_retries=0)
        with pytest.raises(ToolExecutionError):
            await engine.execute(
                tool_id="calculix.run_fea",
                arguments={
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "lc1",
                    "analysis_type": "static_stress",
                },
            )
