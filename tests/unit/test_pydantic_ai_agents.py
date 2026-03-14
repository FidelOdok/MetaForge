"""Tests for PydanticAI agent definitions with LLM-driven ReAct loop (MET-185).

All tests use pydantic_ai.models.test.TestModel -- no live LLM calls.

Covers:
- Agent creation and tool registration for all 4 domains (ME, EE, FW, SIM)
- System prompt content validation
- Structured result model validation (inheritance, defaults, serialization)
- TestModel execution returning structured output
- Dual-mode dispatch: LLM mode vs hardcoded fallback
- Agent singleton idempotency
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from domain_agents.base_agent import AgentDependencies, AgentResult
from domain_agents.electronics.agent import (
    ELECTRONICS_SYSTEM_PROMPT,
    ElectronicsAgent,
    ElectronicsResult,
)
from domain_agents.electronics.agent import (
    TaskRequest as EETaskRequest,
)
from domain_agents.firmware.agent import (
    FIRMWARE_SYSTEM_PROMPT,
    FirmwareAgent,
    FirmwareResult,
)
from domain_agents.firmware.agent import (
    TaskRequest as FWTaskRequest,
)
from domain_agents.mechanical.agent import (
    MECHANICAL_SYSTEM_PROMPT,
    MechanicalAgent,
    MechanicalResult,
)
from domain_agents.mechanical.agent import (
    TaskRequest as METaskRequest,
)
from domain_agents.simulation.agent import (
    SIMULATION_SYSTEM_PROMPT,
    SimulationAgent,
    SimulationResult,
)
from domain_agents.simulation.agent import (
    TaskRequest as SIMTaskRequest,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_twin() -> AsyncMock:
    twin = AsyncMock()
    twin.get_work_product.return_value = MagicMock(
        id=uuid4(), name="test-work_product", domain="mechanical"
    )
    return twin


@pytest.fixture
def mcp_bridge() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    bridge.register_tool("calculix.run_fea", "stress_analysis")
    bridge.register_tool_response(
        "calculix.run_fea",
        {
            "max_von_mises": {"bracket_body": 100.0, "bracket_mount": 50.0},
            "solver_time": 12.5,
            "mesh_elements": 45000,
        },
    )
    return bridge


@pytest.fixture(autouse=True)
def _reset_agent_singletons():
    """Reset module-level PydanticAI agent singletons between tests."""
    import domain_agents.electronics.agent as ee_mod
    import domain_agents.firmware.agent as fw_mod
    import domain_agents.mechanical.agent as me_mod
    import domain_agents.simulation.agent as sim_mod

    me_mod._pydantic_agent = None
    ee_mod._pydantic_agent = None
    fw_mod._pydantic_agent = None
    sim_mod._pydantic_agent = None
    yield
    me_mod._pydantic_agent = None
    ee_mod._pydantic_agent = None
    fw_mod._pydantic_agent = None
    sim_mod._pydantic_agent = None


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestSystemPrompts:
    """Verify each agent has a meaningful domain-specific system prompt."""

    def test_mechanical_prompt_content(self):
        assert "mechanical engineer" in MECHANICAL_SYSTEM_PROMPT.lower()
        assert "validate_stress" in MECHANICAL_SYSTEM_PROMPT
        assert "generate_mesh" in MECHANICAL_SYSTEM_PROMPT
        assert "check_tolerance" in MECHANICAL_SYSTEM_PROMPT

    def test_electronics_prompt_content(self):
        assert "electronics engineer" in ELECTRONICS_SYSTEM_PROMPT.lower()
        assert "run_erc" in ELECTRONICS_SYSTEM_PROMPT
        assert "run_drc" in ELECTRONICS_SYSTEM_PROMPT
        assert "KiCad" in ELECTRONICS_SYSTEM_PROMPT

    def test_firmware_prompt_content(self):
        assert "firmware engineer" in FIRMWARE_SYSTEM_PROMPT.lower()
        assert "generate_hal" in FIRMWARE_SYSTEM_PROMPT
        assert "scaffold_driver" in FIRMWARE_SYSTEM_PROMPT
        assert "configure_rtos" in FIRMWARE_SYSTEM_PROMPT
        assert "STM32" in FIRMWARE_SYSTEM_PROMPT

    def test_simulation_prompt_content(self):
        assert "simulation engineer" in SIMULATION_SYSTEM_PROMPT.lower()
        assert "run_fea" in SIMULATION_SYSTEM_PROMPT
        assert "run_spice" in SIMULATION_SYSTEM_PROMPT
        assert "run_cfd" in SIMULATION_SYSTEM_PROMPT
        assert "CalculiX" in SIMULATION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Result model tests
# ---------------------------------------------------------------------------


class TestResultModels:
    """Test domain-specific result models inherit from AgentResult."""

    def test_mechanical_result_inherits(self):
        assert issubclass(MechanicalResult, AgentResult)
        r = MechanicalResult(overall_passed=False, max_stress_mpa=200.0, critical_region="flange")
        assert r.overall_passed is False
        assert r.max_stress_mpa == 200.0
        assert r.work_products == []
        assert r.tool_calls == []

    def test_electronics_result_inherits(self):
        assert issubclass(ElectronicsResult, AgentResult)
        r = ElectronicsResult(overall_passed=True, total_erc_errors=0, total_drc_errors=0)
        assert r.overall_passed is True

    def test_firmware_result_inherits(self):
        assert issubclass(FirmwareResult, AgentResult)
        r = FirmwareResult(overall_passed=True, generated_files=["hal.c", "hal.h"])
        assert len(r.generated_files) == 2

    def test_simulation_result_inherits(self):
        assert issubclass(SimulationResult, AgentResult)
        r = SimulationResult(overall_passed=True, convergence_achieved=True)
        assert r.convergence_achieved is True

    def test_result_serialization_roundtrip(self):
        """Pydantic v2 model_dump / model_validate roundtrip."""
        original = MechanicalResult(
            overall_passed=False,
            max_stress_mpa=150.5,
            critical_region="bracket",
            recommendations=["Increase thickness"],
            tool_calls=[{"tool": "validate_stress"}],
        )
        data = original.model_dump()
        restored = MechanicalResult.model_validate(data)
        assert restored == original


# ---------------------------------------------------------------------------
# PydanticAI agent factory tests
# ---------------------------------------------------------------------------


class TestAgentFactory:
    """Test _get_or_create_pydantic_agent() factory for each domain."""

    def test_mechanical_agent_has_3_tools(self):
        """ME agent: validate_stress, generate_mesh, check_tolerance."""
        import domain_agents.mechanical.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            agent = mod._get_or_create_pydantic_agent()
            assert agent is not None
            tool_names = {t.name for t in agent._function_toolset.tools.values()}
            assert "validate_stress" in tool_names
            assert "generate_mesh" in tool_names
            assert "check_tolerance" in tool_names

    def test_electronics_agent_has_3_tools(self):
        """EE agent: run_erc, run_drc, check_power_budget."""
        import domain_agents.electronics.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            agent = mod._get_or_create_pydantic_agent()
            assert agent is not None
            tool_names = {t.name for t in agent._function_toolset.tools.values()}
            assert "run_erc" in tool_names
            assert "run_drc" in tool_names
            assert "check_power_budget" in tool_names

    def test_firmware_agent_has_3_tools(self):
        """FW agent: generate_hal, scaffold_driver, configure_rtos."""
        import domain_agents.firmware.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            agent = mod._get_or_create_pydantic_agent()
            assert agent is not None
            tool_names = {t.name for t in agent._function_toolset.tools.values()}
            assert "generate_hal" in tool_names
            assert "scaffold_driver" in tool_names
            assert "configure_rtos" in tool_names

    def test_simulation_agent_has_3_tools(self):
        """SIM agent: run_fea, run_spice, run_cfd."""
        import domain_agents.simulation.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            agent = mod._get_or_create_pydantic_agent()
            assert agent is not None
            tool_names = {t.name for t in agent._function_toolset.tools.values()}
            assert "run_fea" in tool_names
            assert "run_spice" in tool_names
            assert "run_cfd" in tool_names

    def test_factory_returns_none_without_llm_provider(self):
        """Agent factory returns None when no LLM provider is configured."""
        import domain_agents.electronics.agent as ee_mod
        import domain_agents.firmware.agent as fw_mod
        import domain_agents.mechanical.agent as me_mod
        import domain_agents.simulation.agent as sim_mod

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            os.environ.pop("METAFORGE_LLM_MODEL", None)
            assert me_mod._get_or_create_pydantic_agent() is None
            assert ee_mod._get_or_create_pydantic_agent() is None
            assert fw_mod._get_or_create_pydantic_agent() is None
            assert sim_mod._get_or_create_pydantic_agent() is None


# ---------------------------------------------------------------------------
# TestModel execution: agents produce structured output via LLM path
# ---------------------------------------------------------------------------


class TestMechanicalWithTestModel:
    """Test MechanicalAgent LLM path using TestModel."""

    async def test_returns_structured_result(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        agent = Agent(
            TestModel(),
            system_prompt=MECHANICAL_SYSTEM_PROMPT,
            output_type=MechanicalResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Validate stress on bracket", deps=deps)
        assert isinstance(result.output, MechanicalResult)
        assert isinstance(result.output.overall_passed, bool)

    async def test_custom_output(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        custom = MechanicalResult(
            overall_passed=False,
            max_stress_mpa=250.0,
            critical_region="bracket_mount",
            recommendations=["Increase wall thickness by 2mm"],
        )
        agent = Agent(
            TestModel(custom_output_args=custom.model_dump()),
            system_prompt=MECHANICAL_SYSTEM_PROMPT,
            output_type=MechanicalResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Check bracket stress", deps=deps)
        assert result.output.overall_passed is False
        assert result.output.max_stress_mpa == 250.0
        assert result.output.critical_region == "bracket_mount"


class TestElectronicsWithTestModel:
    """Test ElectronicsAgent LLM path using TestModel."""

    async def test_returns_structured_result(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        agent = Agent(
            TestModel(),
            system_prompt=ELECTRONICS_SYSTEM_PROMPT,
            output_type=ElectronicsResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Check power integrity", deps=deps)
        assert isinstance(result.output, ElectronicsResult)
        assert isinstance(result.output.total_erc_errors, int)

    async def test_custom_output(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        custom = ElectronicsResult(
            overall_passed=False,
            total_erc_errors=3,
            total_drc_errors=1,
            recommendations=["Fix unconnected pin on U1.VCC"],
        )
        agent = Agent(
            TestModel(custom_output_args=custom.model_dump()),
            system_prompt=ELECTRONICS_SYSTEM_PROMPT,
            output_type=ElectronicsResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Run ERC", deps=deps)
        assert result.output.total_erc_errors == 3
        assert result.output.overall_passed is False


class TestFirmwareWithTestModel:
    """Test FirmwareAgent LLM path using TestModel."""

    async def test_returns_structured_result(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        agent = Agent(
            TestModel(),
            system_prompt=FIRMWARE_SYSTEM_PROMPT,
            output_type=FirmwareResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Generate HAL for STM32F4", deps=deps)
        assert isinstance(result.output, FirmwareResult)
        assert isinstance(result.output.generated_files, list)

    async def test_custom_output(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        custom = FirmwareResult(
            overall_passed=True,
            generated_files=["hal_gpio.c", "hal_gpio.h", "hal_spi.c", "hal_spi.h"],
        )
        agent = Agent(
            TestModel(custom_output_args=custom.model_dump()),
            system_prompt=FIRMWARE_SYSTEM_PROMPT,
            output_type=FirmwareResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Generate HAL", deps=deps)
        assert result.output.overall_passed is True
        assert len(result.output.generated_files) == 4


class TestSimulationWithTestModel:
    """Test SimulationAgent LLM path using TestModel."""

    async def test_returns_structured_result(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        agent = Agent(
            TestModel(),
            system_prompt=SIMULATION_SYSTEM_PROMPT,
            output_type=SimulationResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Run FEA on bracket", deps=deps)
        assert isinstance(result.output, SimulationResult)
        assert isinstance(result.output.convergence_achieved, bool)

    async def test_custom_output(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        custom = SimulationResult(
            overall_passed=False,
            convergence_achieved=False,
            recommendations=["Refine mesh in stress concentration region"],
        )
        agent = Agent(
            TestModel(custom_output_args=custom.model_dump()),
            system_prompt=SIMULATION_SYSTEM_PROMPT,
            output_type=SimulationResult,
            deps_type=AgentDependencies,
        )
        deps = AgentDependencies(
            twin=mock_twin,
            mcp_bridge=mcp_bridge,
            session_id=str(uuid4()),
            branch="main",
        )
        result = await agent.run("Run CFD analysis", deps=deps)
        assert result.output.convergence_achieved is False
        assert result.output.overall_passed is False


# ---------------------------------------------------------------------------
# Dual-mode dispatch: verify hardcoded fallback
# ---------------------------------------------------------------------------


class TestDualModeDispatch:
    """Verify agents attempt LLM mode and fall back to hardcoded."""

    async def test_mechanical_hardcoded_fallback(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge)
            request = METaskRequest(
                task_type="validate_stress",
                work_product_id=uuid4(),
                parameters={
                    "mesh_file_path": "mesh/bracket.inp",
                    "load_case": "gravity",
                    "constraints": [{"max_von_mises_mpa": 300.0, "safety_factor": 1.5}],
                },
            )
            result = await agent.run_task(request)
            assert result.success is True

    async def test_electronics_hardcoded_fallback(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = ElectronicsAgent(twin=mock_twin, mcp=mcp_bridge)
            request = EETaskRequest(
                task_type="run_erc",
                work_product_id=uuid4(),
                parameters={"schematic_file": "eda/kicad/main.kicad_sch"},
            )
            result = await agent.run_task(request)
            assert result.task_type == "run_erc"

    async def test_firmware_hardcoded_fallback(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = FirmwareAgent(twin=mock_twin, mcp=mcp_bridge)
            request = FWTaskRequest(
                task_type="generate_hal",
                work_product_id=uuid4(),
                parameters={"mcu_family": "STM32F4", "peripherals": ["GPIO"]},
            )
            result = await agent.run_task(request)
            assert result.task_type == "generate_hal"

    async def test_simulation_hardcoded_fallback(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = SimulationAgent(twin=mock_twin, mcp=mcp_bridge)
            request = SIMTaskRequest(
                task_type="run_fea",
                work_product_id=uuid4(),
                parameters={"mesh_file": "mesh/bracket.inp"},
            )
            result = await agent.run_task(request)
            assert result.task_type == "run_fea"


# ---------------------------------------------------------------------------
# Agent singleton idempotency
# ---------------------------------------------------------------------------


class TestAgentSingleton:
    """Verify agent factory returns same instance on repeated calls."""

    def test_mechanical_singleton(self):
        import domain_agents.mechanical.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            a1 = mod._get_or_create_pydantic_agent()
            a2 = mod._get_or_create_pydantic_agent()
            assert a1 is a2

    def test_electronics_singleton(self):
        import domain_agents.electronics.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            a1 = mod._get_or_create_pydantic_agent()
            a2 = mod._get_or_create_pydantic_agent()
            assert a1 is a2

    def test_firmware_singleton(self):
        import domain_agents.firmware.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            a1 = mod._get_or_create_pydantic_agent()
            a2 = mod._get_or_create_pydantic_agent()
            assert a1 is a2

    def test_simulation_singleton(self):
        import domain_agents.simulation.agent as mod

        with patch.object(mod, "get_llm_model", return_value="test"):
            a1 = mod._get_or_create_pydantic_agent()
            a2 = mod._get_or_create_pydantic_agent()
            assert a1 is a2
