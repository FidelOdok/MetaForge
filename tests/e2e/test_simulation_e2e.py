"""End-to-end tests for the simulation engineering vertical.

Exercises the full stack: SimulationAgent → MCP Protocol → CalculiX/SPICE → Digital Twin.
Only the solver binaries are stubbed; all internal interfaces are real.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from domain_agents.simulation.agent import SimulationAgent, TaskRequest
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Realistic simulation results
# ---------------------------------------------------------------------------

SPICE_DC_RESULT: dict[str, Any] = {
    "results": {
        "V(out)": 3.3,
        "I(R1)": 0.0033,
        "V(vcc)": 5.0,
    },
    "waveforms": [],
    "convergence": True,
    "sim_time_s": 0.42,
}

SPICE_NON_CONVERGENT: dict[str, Any] = {
    "results": {},
    "waveforms": [],
    "convergence": False,
    "sim_time_s": 5.0,
}

FEA_STATIC_RESULT: dict[str, Any] = {
    "max_stress_mpa": 85.3,
    "max_displacement_mm": 0.12,
    "safety_factor": 3.24,
    "solver_time_s": 12.5,
}

FEA_UNSAFE_RESULT: dict[str, Any] = {
    "max_stress_mpa": 310.0,
    "max_displacement_mm": 2.8,
    "safety_factor": 0.89,
    "solver_time_s": 15.0,
}

CFD_CONVERGED_RESULT: dict[str, Any] = {
    "max_velocity_ms": 4.2,
    "pressure_drop_pa": 1250.0,
    "max_temperature_c": 72.3,
    "convergence_residual": 1e-5,
}

CFD_NOT_CONVERGED: dict[str, Any] = {
    "max_velocity_ms": 12.0,
    "pressure_drop_pa": 5000.0,
    "max_temperature_c": 150.0,
    "convergence_residual": 0.1,
}


def _make_mcp_bridge(
    *,
    spice_result: dict | None = None,
    fea_result: dict | None = None,
    cfd_result: dict | None = None,
) -> InMemoryMcpBridge:
    """Create a pre-configured InMemoryMcpBridge with simulation tool responses."""
    mcp = InMemoryMcpBridge()

    # Register tools
    mcp.register_tool("spice.run_simulation", capability="circuit_simulation", name="Run SPICE")
    mcp.register_tool("calculix.run_fea", capability="stress_analysis", name="Run FEA")
    mcp.register_tool("calculix.run_thermal", capability="thermal_analysis", name="Run CFD")

    # Register responses
    mcp.register_tool_response("spice.run_simulation", spice_result or SPICE_DC_RESULT)
    mcp.register_tool_response("calculix.run_fea", fea_result or FEA_STATIC_RESULT)
    mcp.register_tool_response("calculix.run_thermal", cfd_result or CFD_CONVERGED_RESULT)

    return mcp


def _make_circuit_artifact() -> WorkProduct:
    """Create a realistic circuit design work_product."""
    return WorkProduct(
        name="drone-fc-power-supply",
        type=WorkProductType.SCHEMATIC,
        domain="electronics",
        file_path="sim/power_supply.cir",
        content_hash="sha256:sim112233",
        format="spice",
        created_by="human",
        metadata={
            "circuit_type": "buck_converter",
            "input_voltage": 12.0,
            "output_voltage": 3.3,
        },
    )


def _make_mech_work_product() -> WorkProduct:
    """Create a mechanical design work_product for FEA/CFD."""
    return WorkProduct(
        name="motor-mount-bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/motor_mount_bracket.step",
        content_hash="sha256:mech112233",
        format="step",
        created_by="human",
        metadata={
            "material": "Al6061-T6",
            "mass_kg": 0.045,
        },
    )


# ---------------------------------------------------------------------------
# Test class: SPICE simulation through SimulationAgent
# ---------------------------------------------------------------------------


class TestSpiceSimulationE2E:
    """E2E tests for SPICE circuit simulation pipeline."""

    @pytest.fixture
    async def stack(self):
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_circuit_artifact())
        agent = SimulationAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "mcp": mcp, "agent": agent, "work_product": work_product}

    async def test_spice_dc_converges(self, stack):
        """SPICE DC analysis converges with correct results."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_spice",
                work_product_id=s["work_product"].id,
                parameters={
                    "netlist_path": "sim/power_supply.cir",
                    "analysis_type": "dc",
                    "params": {"v_in": 12.0},
                },
            )
        )

        assert result.success is True
        assert result.task_type == "run_spice"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["convergence"] is True
        assert result.skill_results[0]["results"]["V(out)"] == 3.3

    async def test_spice_non_convergent(self):
        """SPICE simulation returns failure when it doesn't converge."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge(spice_result=SPICE_NON_CONVERGENT)
        work_product = await twin.create_work_product(_make_circuit_artifact())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_spice",
                work_product_id=work_product.id,
                parameters={
                    "netlist_path": "sim/power_supply.cir",
                    "analysis_type": "transient",
                },
            )
        )

        assert result.success is False
        assert any("converge" in w for w in result.warnings)

    async def test_spice_missing_netlist(self, stack):
        """Missing netlist_path returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_spice",
                work_product_id=s["work_product"].id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("netlist_path" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: FEA through SimulationAgent
# ---------------------------------------------------------------------------


class TestFeaSimulationE2E:
    """E2E tests for FEA structural analysis pipeline."""

    @pytest.fixture
    async def stack(self):
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "mcp": mcp, "agent": agent, "work_product": work_product}

    async def test_fea_static_passes(self, stack):
        """FEA static analysis passes with safety factor > 1."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_fea",
                work_product_id=s["work_product"].id,
                parameters={
                    "mesh_file": "models/motor_mount_bracket.inp",
                    "load_cases": [{"name": "hover_3g", "force_n": 30, "direction": "z"}],
                    "analysis_type": "static",
                    "material": "Al6061-T6",
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["safety_factor"] == 3.24
        assert result.skill_results[0]["max_stress_mpa"] == 85.3

    async def test_fea_unsafe_fails(self):
        """FEA fails when safety factor is below 1.0."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge(fea_result=FEA_UNSAFE_RESULT)
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_fea",
                work_product_id=work_product.id,
                parameters={
                    "mesh_file": "models/motor_mount_bracket.inp",
                    "analysis_type": "static",
                },
            )
        )

        assert result.success is False
        assert any("safety factor" in w.lower() for w in result.warnings)

    async def test_fea_missing_mesh_file(self, stack):
        """Missing mesh_file returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_fea",
                work_product_id=s["work_product"].id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("mesh_file" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: CFD through SimulationAgent
# ---------------------------------------------------------------------------


class TestCfdSimulationE2E:
    """E2E tests for CFD thermal/flow analysis pipeline."""

    @pytest.fixture
    async def stack(self):
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "mcp": mcp, "agent": agent, "work_product": work_product}

    async def test_cfd_converges(self, stack):
        """CFD simulation converges with residual below threshold."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_cfd",
                work_product_id=s["work_product"].id,
                parameters={
                    "geometry_file": "models/motor_mount_bracket.step",
                    "fluid_properties": {"density_kg_m3": 1.225, "viscosity_pa_s": 1.8e-5},
                    "boundary_conditions": {"inlet_velocity_ms": 5.0, "outlet_pressure_pa": 101325},
                    "mesh_resolution": "medium",
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["max_velocity_ms"] == 4.2
        assert result.skill_results[0]["convergence_residual"] == 1e-5

    async def test_cfd_not_converged(self):
        """CFD fails when convergence residual exceeds threshold."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge(cfd_result=CFD_NOT_CONVERGED)
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_cfd",
                work_product_id=work_product.id,
                parameters={
                    "geometry_file": "models/motor_mount_bracket.step",
                },
            )
        )

        assert result.success is False
        assert any("residual" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Test class: Full simulation pipeline
# ---------------------------------------------------------------------------


class TestFullSimulationE2E:
    """E2E tests for running multiple simulations in one pass."""

    async def test_full_simulation_all_three(self):
        """Full simulation runs SPICE + FEA + CFD and aggregates results."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="full_simulation",
                work_product_id=work_product.id,
                parameters={
                    "netlist_path": "sim/power_supply.cir",
                    "mesh_file": "models/motor_mount_bracket.inp",
                    "geometry_file": "models/motor_mount_bracket.step",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "full_simulation"
        assert len(result.skill_results) == 3

        skills_run = {r["skill"] for r in result.skill_results}
        assert skills_run == {"run_spice", "run_fea", "run_cfd"}

    async def test_full_simulation_no_params_fails(self):
        """Full simulation fails when no simulation parameters are provided."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="full_simulation",
                work_product_id=work_product.id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("No simulations" in e for e in result.errors)

    async def test_full_simulation_partial_failure(self):
        """Full simulation reports failure if any sub-simulation fails."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge(fea_result=FEA_UNSAFE_RESULT)
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="full_simulation",
                work_product_id=work_product.id,
                parameters={
                    "netlist_path": "sim/power_supply.cir",
                    "mesh_file": "models/motor_mount_bracket.inp",
                },
            )
        )

        assert result.success is False
        assert len(result.skill_results) == 2


# ---------------------------------------------------------------------------
# Test class: Common agent behaviours
# ---------------------------------------------------------------------------


class TestSimulationAgentCommonE2E:
    """Common agent behaviour tests."""

    async def test_artifact_not_found(self):
        """Agent returns error when work_product doesn't exist."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_spice",
                work_product_id=uuid4(),
                parameters={"netlist_path": "x.cir"},
            )
        )

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_unsupported_task_type(self):
        """Agent rejects unknown task types."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_monte_carlo",
                work_product_id=work_product.id,
            )
        )

        assert result.success is False
        assert any("Unsupported" in e for e in result.errors)

    async def test_twin_update_after_simulation(self):
        """Verify Twin work_product can be updated with simulation results."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_mech_work_product())
        agent = SimulationAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_fea",
                work_product_id=work_product.id,
                parameters={"mesh_file": "models/motor_mount_bracket.inp"},
            )
        )
        assert result.success is True

        updated = await twin.update_work_product(
            work_product.id,
            {
                "metadata": {
                    **work_product.metadata,
                    "fea_results": result.skill_results[0],
                },
            },
        )
        assert "fea_results" in updated.metadata
        assert updated.metadata["fea_results"]["safety_factor"] == 3.24
