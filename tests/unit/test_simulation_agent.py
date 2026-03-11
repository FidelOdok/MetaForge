"""Tests for the simulation engineering domain agent."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from domain_agents.simulation.agent import (
    SimulationAgent,
    SimulationResult,
    TaskRequest,
    TaskResult,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge


def _spice_response(convergence: bool = True) -> dict:
    """Build a mock SPICE tool response."""
    return {
        "results": {"Vout": 3.3, "Iload": 0.5},
        "waveforms": ["sim/output/vout.csv"],
        "convergence": convergence,
        "sim_time_s": 2.5,
    }


def _fea_response(
    safety_factor: float = 2.5,
    max_stress: float = 150.0,
) -> dict:
    """Build a mock FEA tool response."""
    return {
        "max_stress_mpa": max_stress,
        "max_displacement_mm": 0.12,
        "safety_factor": safety_factor,
        "solver_time_s": 8.3,
    }


def _cfd_response(convergence_residual: float = 1e-5) -> dict:
    """Build a mock CFD tool response."""
    return {
        "max_velocity_ms": 12.5,
        "pressure_drop_pa": 350.0,
        "max_temperature_c": 85.2,
        "convergence_residual": convergence_residual,
    }


@pytest.fixture
def mock_twin() -> AsyncMock:
    twin = AsyncMock()
    twin.get_artifact.return_value = MagicMock(id=uuid4(), name="drone-fc", domain="simulation")
    return twin


@pytest.fixture
def mcp_bridge() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    # Register simulation tools
    bridge.register_tool("spice.run_simulation", "circuit_simulation")
    bridge.register_tool("calculix.run_fea", "structural_analysis")
    bridge.register_tool("calculix.run_thermal", "cfd_analysis")
    # Register default responses
    bridge.register_tool_response("spice.run_simulation", _spice_response())
    bridge.register_tool_response("calculix.run_fea", _fea_response())
    bridge.register_tool_response("calculix.run_thermal", _cfd_response())
    return bridge


@pytest.fixture
def agent(mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge) -> SimulationAgent:
    return SimulationAgent(twin=mock_twin, mcp=mcp_bridge)


# --- SimulationAgent construction and metadata ---


class TestSimulationAgent:
    """Basic agent construction and properties."""

    async def test_agent_creation(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)
        assert agent.twin is mock_twin
        assert agent.mcp is mcp_bridge
        assert agent.session_id is not None

    async def test_agent_creation_with_session_id(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        sid = uuid4()
        agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge, session_id=sid)
        assert agent.session_id == sid

    async def test_supported_tasks(self):
        assert SimulationAgent.SUPPORTED_TASKS == {
            "run_spice",
            "run_fea",
            "run_cfd",
            "full_simulation",
        }

    async def test_unsupported_task_type_fails(self, agent: SimulationAgent):
        request = TaskRequest(
            task_type="do_magic",
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("Unsupported task type" in e for e in result.errors)
        assert "do_magic" in result.errors[0]

    async def test_missing_artifact(self, agent: SimulationAgent, mock_twin: AsyncMock):
        """Missing artifact should produce an error."""
        mock_twin.get_artifact.return_value = None
        request = TaskRequest(
            task_type="run_spice",
            artifact_id=uuid4(),
            parameters={"netlist_path": "sim/power_supply.cir"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("not found" in e for e in result.errors)


# --- SPICE simulation ---


class TestRunSpice:
    """Tests for the run_spice task type."""

    async def test_spice_passes_convergence(self, agent: SimulationAgent):
        """SPICE with converged simulation should succeed."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="run_spice",
            artifact_id=artifact_id,
            parameters={
                "netlist_path": "sim/power_supply.cir",
                "analysis_type": "dc",
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "run_spice"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "run_spice"
        assert result.skill_results[0]["convergence"] is True
        assert result.skill_results[0]["sim_time_s"] > 0

    async def test_spice_fails_no_convergence(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """SPICE without convergence should report failure."""
        mcp_bridge.register_tool_response(
            "spice.run_simulation",
            _spice_response(convergence=False),
        )
        agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)
        request = TaskRequest(
            task_type="run_spice",
            artifact_id=uuid4(),
            parameters={"netlist_path": "sim/power_supply.cir"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("converge" in w.lower() for w in result.warnings)

    async def test_spice_missing_netlist(self, agent: SimulationAgent):
        """SPICE should fail when netlist_path is missing."""
        request = TaskRequest(
            task_type="run_spice",
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("netlist_path" in e for e in result.errors)


# --- FEA simulation ---


class TestRunFea:
    """Tests for the run_fea task type."""

    async def test_fea_passes_high_safety(self, agent: SimulationAgent):
        """FEA with high safety factor should succeed."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="run_fea",
            artifact_id=artifact_id,
            parameters={
                "mesh_file": "mesh/bracket.inp",
                "load_cases": [{"name": "gravity", "force_n": 100}],
                "analysis_type": "static",
                "material": "steel_1018",
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "run_fea"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "run_fea"
        assert result.skill_results[0]["safety_factor"] >= 1.0

    async def test_fea_fails_low_safety(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        """FEA with low safety factor should report failure."""
        mcp_bridge.register_tool_response(
            "calculix.run_fea",
            _fea_response(safety_factor=0.5, max_stress=500.0),
        )
        agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)
        request = TaskRequest(
            task_type="run_fea",
            artifact_id=uuid4(),
            parameters={"mesh_file": "mesh/bracket.inp"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("safety factor" in w.lower() for w in result.warnings)

    async def test_fea_missing_mesh_file(self, agent: SimulationAgent):
        """FEA should fail when mesh_file is missing."""
        request = TaskRequest(
            task_type="run_fea",
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("mesh_file" in e for e in result.errors)


# --- CFD simulation ---


class TestRunCfd:
    """Tests for the run_cfd task type."""

    async def test_cfd_passes_convergence(self, agent: SimulationAgent):
        """CFD with good convergence should succeed."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="run_cfd",
            artifact_id=artifact_id,
            parameters={
                "geometry_file": "cad/enclosure.step",
                "fluid_properties": {"density_kg_m3": 1.225, "viscosity_pa_s": 1.8e-5},
                "boundary_conditions": {"inlet_velocity_ms": 5.0},
                "mesh_resolution": "medium",
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "run_cfd"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "run_cfd"
        assert result.skill_results[0]["convergence_residual"] < 1e-3

    async def test_cfd_fails_no_convergence(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """CFD with high residual should report failure."""
        mcp_bridge.register_tool_response(
            "calculix.run_thermal",
            _cfd_response(convergence_residual=0.1),
        )
        agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)
        request = TaskRequest(
            task_type="run_cfd",
            artifact_id=uuid4(),
            parameters={"geometry_file": "cad/enclosure.step"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("residual" in w.lower() for w in result.warnings)

    async def test_cfd_missing_geometry_file(self, agent: SimulationAgent):
        """CFD should fail when geometry_file is missing."""
        request = TaskRequest(
            task_type="run_cfd",
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("geometry_file" in e for e in result.errors)


# --- Full simulation ---


class TestFullSimulation:
    """Tests for the full_simulation task type."""

    async def test_full_simulation_runs_all(self, agent: SimulationAgent):
        """Full simulation should run all three and aggregate results."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="full_simulation",
            artifact_id=artifact_id,
            parameters={
                "netlist_path": "sim/power_supply.cir",
                "mesh_file": "mesh/bracket.inp",
                "geometry_file": "cad/enclosure.step",
            },
        )
        result = await agent.run_task(request)

        assert result.task_type == "full_simulation"
        assert result.artifact_id == artifact_id
        assert result.success is True
        assert len(result.skill_results) == 3
        skills_run = {r["skill"] for r in result.skill_results}
        assert skills_run == {"run_spice", "run_fea", "run_cfd"}

    async def test_full_simulation_partial_parameters(self, agent: SimulationAgent):
        """Full simulation should only run sims for which parameters are provided."""
        request = TaskRequest(
            task_type="full_simulation",
            artifact_id=uuid4(),
            parameters={"netlist_path": "sim/power_supply.cir"},
        )
        result = await agent.run_task(request)

        assert result.task_type == "full_simulation"
        assert result.success is True
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "run_spice"

    async def test_full_simulation_no_parameters(self, agent: SimulationAgent):
        """Full simulation with no parameters should error."""
        request = TaskRequest(
            task_type="full_simulation",
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("No simulations could be run" in e for e in result.errors)

    async def test_full_simulation_mixed_success(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Full simulation with one failing sim should report overall failure."""
        # SPICE converges, FEA has low safety factor
        mcp_bridge.register_tool_response(
            "calculix.run_fea",
            _fea_response(safety_factor=0.3),
        )
        agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)
        request = TaskRequest(
            task_type="full_simulation",
            artifact_id=uuid4(),
            parameters={
                "netlist_path": "sim/power_supply.cir",
                "mesh_file": "mesh/bracket.inp",
            },
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert len(result.skill_results) == 2


# --- TaskRequest model ---


class TestTaskRequest:
    """Tests for the TaskRequest Pydantic model."""

    def test_task_request_defaults(self):
        artifact_id = uuid4()
        req = TaskRequest(task_type="run_spice", artifact_id=artifact_id)
        assert req.branch == "main"
        assert req.parameters == {}

    def test_task_request_with_parameters(self):
        artifact_id = uuid4()
        params = {"netlist_path": "sim/power_supply.cir"}
        req = TaskRequest(
            task_type="run_spice",
            artifact_id=artifact_id,
            parameters=params,
            branch="feature-1",
        )
        assert req.branch == "feature-1"
        assert req.parameters == params
        assert req.artifact_id == artifact_id


# --- TaskResult model ---


class TestTaskResult:
    """Tests for the TaskResult Pydantic model."""

    def test_task_result_defaults(self):
        artifact_id = uuid4()
        res = TaskResult(
            task_type="run_spice",
            artifact_id=artifact_id,
            success=True,
        )
        assert res.skill_results == []
        assert res.errors == []
        assert res.warnings == []

    def test_task_result_with_data(self):
        artifact_id = uuid4()
        res = TaskResult(
            task_type="run_spice",
            artifact_id=artifact_id,
            success=False,
            errors=["Simulation failed"],
            warnings=["High residual"],
            skill_results=[{"skill": "run_spice", "data": {}}],
        )
        assert not res.success
        assert len(res.errors) == 1
        assert len(res.warnings) == 1
        assert len(res.skill_results) == 1


# --- PydanticAI integration ---


class TestSimulationResult:
    """Tests for the SimulationResult structured output model."""

    def test_simulation_result_defaults(self):
        result = SimulationResult()
        assert result.overall_passed is True
        assert result.convergence_achieved is True
        assert result.artifacts == []
        assert result.analysis == {}

    def test_simulation_result_with_data(self):
        result = SimulationResult(
            overall_passed=False,
            convergence_achieved=False,
            recommendations=["Refine mesh in critical area"],
            tool_calls=[
                {"tool": "run_fea", "result": "fail"},
                {"tool": "run_spice", "result": "pass"},
            ],
        )
        assert not result.overall_passed
        assert not result.convergence_achieved
        assert len(result.tool_calls) == 2
        assert len(result.recommendations) == 1


class TestSimulationHardcodedFallback:
    """Tests verifying hardcoded dispatch when LLM is unavailable."""

    async def test_fallback_when_no_llm_configured(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Agent should use hardcoded dispatch when METAFORGE_LLM_PROVIDER is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)

            request = TaskRequest(
                task_type="run_spice",
                artifact_id=uuid4(),
                parameters={
                    "netlist_path": "sim/power_supply.cir",
                    "analysis_type": "dc",
                },
            )
            result = await agent.run_task(request)

            assert result.success is True
            assert result.task_type == "run_spice"

    async def test_unsupported_task_in_hardcoded_mode(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Unsupported tasks should fail gracefully in hardcoded mode."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)

            request = TaskRequest(
                task_type="unsupported_task",
                artifact_id=uuid4(),
            )
            result = await agent.run_task(request)

            assert result.success is False
            assert any("Unsupported task type" in e for e in result.errors)
