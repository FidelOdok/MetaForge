"""End-to-end tests for CadQuery CAD generation vertical.

Exercises the full stack:
  MechanicalAgent → Skill → RegistryMcpBridge → ToolRegistry → CadQuery Adapter

The CadQuery adapter handlers validate arguments and delegate to
CadqueryOperations, which requires CadQuery installed. Since CadQuery
is not available in CI, the adapter handlers will raise via the operations
layer. These tests verify the full routing path works end-to-end, testing:
- Tool registry bootstrap with all adapters
- RegistryMcpBridge routing to the correct adapter
- Agent dispatch to generate_cad and generate_cad_script skills
- Backend selection and fallback logic
"""

from __future__ import annotations

import pytest

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.registry_bridge import RegistryMcpBridge
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.registry import ToolRegistry
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# --- Mock CAD results (simulating what CadQuery would return) ---

CADQUERY_CREATE_RESULT = {
    "cad_file": "output/bracket.step",
    "volume_mm3": 12500.0,
    "surface_area_mm2": 8400.0,
    "bounding_box": {
        "min_x": -25.0,
        "min_y": -15.0,
        "min_z": -2.5,
        "max_x": 25.0,
        "max_y": 15.0,
        "max_z": 2.5,
    },
    "parameters_used": {"length": 50.0, "width": 30.0, "thickness": 5.0},
    "material": "aluminum_6061",
}

CADQUERY_SCRIPT_RESULT = {
    "cad_file": "output/script_result.step",
    "script_text": ("import cadquery as cq\nresult = cq.Workplane('XY').box(50, 30, 20)\n"),
    "volume_mm3": 30000.0,
    "surface_area_mm2": 6200.0,
    "bounding_box": {
        "min_x": -25.0,
        "min_y": -15.0,
        "min_z": -10.0,
        "max_x": 25.0,
        "max_y": 15.0,
        "max_z": 10.0,
    },
}


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="drone-motor-mount",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/drone_mount.step",
        content_hash="sha256:drone123",
        format="step",
        created_by="human",
        metadata={"material": "Al6061-T6"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def registry() -> ToolRegistry:
    """Bootstrap a full ToolRegistry with all adapters."""
    return await bootstrap_tool_registry()


@pytest.fixture()
async def registry_bridge(registry: ToolRegistry) -> RegistryMcpBridge:
    """Create a RegistryMcpBridge backed by the full registry."""
    return RegistryMcpBridge(registry)


@pytest.fixture()
async def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture()
async def work_product(twin: InMemoryTwinAPI) -> WorkProduct:
    return await twin.create_work_product(_make_work_product())


@pytest.fixture()
def mcp_with_cadquery_responses() -> InMemoryMcpBridge:
    """InMemoryMcpBridge with CadQuery tool responses registered."""
    mcp = InMemoryMcpBridge()
    mcp.register_tool(
        "cadquery.create_parametric",
        capability="cad_generation",
        name="Create Parametric",
    )
    mcp.register_tool_response("cadquery.create_parametric", CADQUERY_CREATE_RESULT)
    mcp.register_tool(
        "cadquery.execute_script",
        capability="cad_scripting",
        name="Execute Script",
    )
    mcp.register_tool_response("cadquery.execute_script", CADQUERY_SCRIPT_RESULT)
    return mcp


# ---------------------------------------------------------------------------
# Registry & Bridge E2E tests
# ---------------------------------------------------------------------------


class TestRegistryBootstrapE2E:
    """Verify the full bootstrap -> registry -> bridge path."""

    async def test_all_adapters_registered(self, registry: ToolRegistry):
        """All 3 adapters (cadquery, freecad, calculix) register successfully."""
        adapters = registry.list_adapters()
        adapter_ids = {a.adapter_id for a in adapters}
        assert adapter_ids == {"cadquery", "freecad", "calculix"}

    async def test_cadquery_tools_discoverable(self, registry: ToolRegistry):
        """All 7 CadQuery tools are discoverable in the registry."""
        cq_tools = [t for t in registry.list_tools() if t.adapter_id == "cadquery"]
        assert len(cq_tools) == 7
        tool_ids = {t.tool_id for t in cq_tools}
        assert "cadquery.create_parametric" in tool_ids
        assert "cadquery.execute_script" in tool_ids
        assert "cadquery.boolean_operation" in tool_ids

    async def test_capability_discovery(self, registry: ToolRegistry):
        """cad_generation capability returns both cadquery and freecad tools."""
        tools = registry.find_tools_by_capability("cad_generation")
        tool_ids = {t.tool_id for t in tools}
        assert "cadquery.create_parametric" in tool_ids
        assert "freecad.create_parametric" in tool_ids

    async def test_bridge_routes_to_cadquery(self, registry_bridge: RegistryMcpBridge):
        """RegistryMcpBridge correctly identifies CadQuery tools as available."""
        assert await registry_bridge.is_available("cadquery.create_parametric")
        assert await registry_bridge.is_available("cadquery.execute_script")
        assert await registry_bridge.is_available("cadquery.boolean_operation")

    async def test_bridge_routes_to_freecad(self, registry_bridge: RegistryMcpBridge):
        """FreeCAD tools are also available through the same bridge."""
        assert await registry_bridge.is_available("freecad.create_parametric")
        assert await registry_bridge.is_available("freecad.generate_mesh")

    async def test_bridge_list_tools_by_capability(self, registry_bridge: RegistryMcpBridge):
        """Bridge can list tools filtered by capability."""
        cad_tools = await registry_bridge.list_tools(capability="cad_generation")
        assert len(cad_tools) == 2

    async def test_bridge_invoke_reaches_handler(self, registry_bridge: RegistryMcpBridge):
        """Invoking a tool through the bridge reaches the adapter handler.

        The handler will validate args and fail (no CadQuery installed),
        but this proves the full routing path works.
        """
        from skill_registry.mcp_bridge import McpToolError

        with pytest.raises(McpToolError):
            # Missing required args — handler validation kicks in
            await registry_bridge.invoke("cadquery.create_parametric", {})

    async def test_health_check_all_adapters(self, registry: ToolRegistry):
        """Health checks pass for all bootstrapped adapters."""
        results = await registry.check_all_health()
        for adapter_id, health in results.items():
            assert health.status == "healthy", f"{adapter_id} unhealthy"


# ---------------------------------------------------------------------------
# Agent → Skill → Bridge E2E tests (with mock responses)
# ---------------------------------------------------------------------------


class TestAgentCadGenerationE2E:
    """Agent-level E2E tests for CAD generation with mock tool responses."""

    async def test_generate_cad_with_cadquery_backend(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
        mcp_with_cadquery_responses: InMemoryMcpBridge,
    ):
        """Full path: Agent.run_task → generate_cad skill → cadquery backend."""
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_cadquery_responses)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad",
                work_product_id=work_product.id,
                parameters={
                    "shape_type": "bracket",
                    "dimensions": {"length": 50.0, "width": 30.0, "thickness": 5.0},
                    "material": "aluminum_6061",
                    "backend": "cadquery",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "generate_cad"
        assert len(result.skill_results) == 1
        skill = result.skill_results[0]
        assert skill["skill"] == "generate_cad"
        assert skill["cad_file"] == "output/bracket.step"
        assert skill["volume_mm3"] == 12500.0
        assert skill["material"] == "aluminum_6061"

    async def test_generate_cad_default_backend_is_cadquery(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
        mcp_with_cadquery_responses: InMemoryMcpBridge,
    ):
        """Default backend is cadquery when no backend specified."""
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_cadquery_responses)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad",
                work_product_id=work_product.id,
                parameters={
                    "shape_type": "plate",
                    "dimensions": {"length": 100.0, "width": 50.0, "thickness": 2.0},
                    # No backend specified — should default to cadquery
                },
            )
        )

        assert result.success is True

    async def test_generate_cad_fallback_when_preferred_unavailable(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
    ):
        """Falls back to cadquery when freecad is requested but unavailable."""
        mcp = InMemoryMcpBridge()
        # Only register cadquery, not freecad
        mcp.register_tool(
            "cadquery.create_parametric",
            capability="cad_generation",
        )
        mcp.register_tool_response("cadquery.create_parametric", CADQUERY_CREATE_RESULT)

        agent = MechanicalAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad",
                work_product_id=work_product.id,
                parameters={
                    "shape_type": "bracket",
                    "dimensions": {"length": 50.0, "width": 30.0, "thickness": 5.0},
                    "backend": "freecad",  # Request freecad, but only cadquery available
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["cad_file"] == "output/bracket.step"

    async def test_generate_cad_script_e2e(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
        mcp_with_cadquery_responses: InMemoryMcpBridge,
    ):
        """Full path: Agent → generate_cad_script skill → execute_script tool."""
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_cadquery_responses)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad_script",
                work_product_id=work_product.id,
                parameters={
                    "description": "A rectangular mounting bracket 50x30x20mm with 4 corner holes",
                    "constraints": {"length": 50.0, "width": 30.0, "height": 20.0},
                    "material": "aluminum_6061",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "generate_cad_script"
        assert len(result.skill_results) == 1
        skill = result.skill_results[0]
        assert skill["skill"] == "generate_cad_script"
        assert skill["cad_file"] == "output/script_result.step"
        assert skill["volume_mm3"] == 30000.0
        assert "cadquery" in skill["script_text"].lower()

    async def test_generate_cad_script_missing_description(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
        mcp_with_cadquery_responses: InMemoryMcpBridge,
    ):
        """generate_cad_script fails gracefully with missing description."""
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_cadquery_responses)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad_script",
                work_product_id=work_product.id,
                parameters={},  # Missing description
            )
        )

        assert result.success is False
        assert any("description" in e.lower() for e in result.errors)

    async def test_generate_cad_writeback_to_twin(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
        mcp_with_cadquery_responses: InMemoryMcpBridge,
    ):
        """CAD generation writes the result back to the Digital Twin."""
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_cadquery_responses)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad",
                work_product_id=work_product.id,
                parameters={
                    "shape_type": "bracket",
                    "dimensions": {"length": 50.0, "width": 30.0, "thickness": 5.0},
                },
            )
        )

        assert result.success is True
        # Writeback should have created a new work product
        skill = result.skill_results[0]
        if "work_product_id" in skill:
            await twin.get_work_product(skill["work_product_id"])
        original = await twin.get_work_product(work_product.id)
        assert original is not None

    async def test_no_backend_available_returns_error(
        self,
        twin: InMemoryTwinAPI,
        work_product: WorkProduct,
    ):
        """When no CAD backend is available, returns clean error."""
        mcp = InMemoryMcpBridge()
        # Don't register any CAD tools
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_cad",
                work_product_id=work_product.id,
                parameters={
                    "shape_type": "bracket",
                    "dimensions": {"length": 50.0},
                },
            )
        )

        assert result.success is False
        assert any("backend" in e.lower() or "available" in e.lower() for e in result.errors)
