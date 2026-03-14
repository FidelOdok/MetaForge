"""Tests for the mechanical engineering domain agent."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from domain_agents.base_agent import AgentDependencies, get_llm_model, is_llm_available
from domain_agents.mechanical.agent import (
    MechanicalAgent,
    MechanicalResult,
    TaskRequest,
    TaskResult,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge


@pytest.fixture
def mock_twin() -> AsyncMock:
    twin = AsyncMock()
    # Default: work_product exists
    twin.get_work_product.return_value = MagicMock(id=uuid4(), name="bracket", domain="mechanical")
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


@pytest.fixture
def agent(mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge) -> MechanicalAgent:
    return MechanicalAgent(twin=mock_twin, mcp=mcp_bridge)


# --- MechanicalAgent construction and metadata ---


class TestMechanicalAgent:
    """Basic agent construction and properties."""

    async def test_agent_creation(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge)
        assert agent.twin is mock_twin
        assert agent.mcp is mcp_bridge
        assert agent.session_id is not None

    async def test_agent_creation_with_session_id(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        sid = uuid4()
        agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge, session_id=sid)
        assert agent.session_id == sid

    async def test_supported_tasks(self):
        assert MechanicalAgent.SUPPORTED_TASKS == {
            "validate_stress",
            "check_tolerances",
            "generate_mesh",
            "generate_cad",
            "full_validation",
        }


# --- Stress validation ---


class TestValidateStress:
    """Tests for the validate_stress task type."""

    async def test_stress_passes(self, agent: MechanicalAgent):
        """Stress below allowable limit should pass."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            work_product_id=work_product_id,
            parameters={
                "mesh_file_path": "mesh/bracket.inp",
                "load_case": "gravity",
                "constraints": [
                    {"max_von_mises_mpa": 300.0, "safety_factor": 1.5},
                ],
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "validate_stress"
        assert result.work_product_id == work_product_id
        assert len(result.errors) == 0
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["overall_passed"] is True

    async def test_stress_fails(self, agent: MechanicalAgent):
        """Stress exceeding allowable limit should fail."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            work_product_id=work_product_id,
            parameters={
                "mesh_file_path": "mesh/bracket.inp",
                "load_case": "gravity",
                "constraints": [
                    # 80 MPa / 1.5 = ~53.3 MPa allowable; bracket_body = 100 => fail
                    {"max_von_mises_mpa": 80.0, "safety_factor": 1.5},
                ],
            },
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert result.warnings == ["One or more stress constraints violated"]
        constraint_results = result.skill_results[0]["constraint_results"]
        # bracket_body (100 MPa) should fail, bracket_mount (50 MPa) should also fail
        failed = [r for r in constraint_results if not r["passed"]]
        assert len(failed) >= 1

    async def test_missing_artifact(self, agent: MechanicalAgent, mock_twin: AsyncMock):
        """Missing work_product should produce an error."""
        mock_twin.get_work_product.return_value = None
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            work_product_id=work_product_id,
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_fea_solver_error(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        """FEA solver failure should produce an error."""
        # Create a bridge with no response registered for the tool
        bad_bridge = InMemoryMcpBridge()
        # No tool response registered -- invoke will raise McpToolError
        agent = MechanicalAgent(twin=mock_twin, mcp=bad_bridge)

        work_product_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            work_product_id=work_product_id,
            parameters={"mesh_file_path": "mesh/bracket.inp"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("FEA solver failed" in e for e in result.errors)

    async def test_stress_no_constraints(self, agent: MechanicalAgent):
        """No constraints means all pass (vacuous truth)."""
        request = TaskRequest(
            task_type="validate_stress",
            work_product_id=uuid4(),
            parameters={
                "mesh_file_path": "mesh/bracket.inp",
                "constraints": [],
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert len(result.skill_results[0]["constraint_results"]) == 0


# --- Tolerance checking ---


class TestCheckTolerances:
    """Tests for the check_tolerances task type."""

    async def test_missing_manufacturing_process(self, agent: MechanicalAgent):
        request = TaskRequest(
            task_type="check_tolerances",
            work_product_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("manufacturing_process" in e for e in result.errors)


# --- Mesh generation ---


class TestGenerateMesh:
    """Tests for the generate_mesh task type."""

    async def test_missing_cad_file_parameter(self, agent: MechanicalAgent):
        """Agent should return error when cad_file parameter is missing."""
        request = TaskRequest(
            task_type="generate_mesh",
            work_product_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("cad_file" in e for e in result.errors)


# --- Full validation ---


class TestFullValidation:
    """Tests for the full_validation task type."""

    async def test_full_validation_delegates_to_stress(self, agent: MechanicalAgent):
        """Full validation should run stress validation and aggregate results."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="full_validation",
            work_product_id=work_product_id,
            parameters={
                "mesh_file_path": "mesh/bracket.inp",
                "load_case": "gravity",
                "constraints": [
                    {"max_von_mises_mpa": 300.0, "safety_factor": 1.5},
                ],
            },
        )
        result = await agent.run_task(request)

        assert result.task_type == "full_validation"
        assert result.work_product_id == work_product_id
        assert result.success is True
        assert len(result.skill_results) >= 1
        assert result.skill_results[0]["skill"] == "validate_stress"


# --- Unsupported task ---


class TestUnsupportedTask:
    """Tests for unsupported task types."""

    async def test_unsupported_task_type(self, agent: MechanicalAgent):
        request = TaskRequest(
            task_type="do_magic",
            work_product_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("Unsupported task type" in e for e in result.errors)
        assert "do_magic" in result.errors[0]


# --- TaskRequest model ---


class TestTaskRequest:
    """Tests for the TaskRequest Pydantic model."""

    def test_task_request_defaults(self):
        work_product_id = uuid4()
        req = TaskRequest(task_type="validate_stress", work_product_id=work_product_id)
        assert req.branch == "main"
        assert req.parameters == {}

    def test_task_request_with_parameters(self):
        work_product_id = uuid4()
        params = {"mesh_file_path": "mesh/bracket.inp", "load_case": "gravity"}
        req = TaskRequest(
            task_type="validate_stress",
            work_product_id=work_product_id,
            parameters=params,
            branch="feature-1",
        )
        assert req.branch == "feature-1"
        assert req.parameters == params
        assert req.work_product_id == work_product_id


# --- TaskResult model ---


class TestTaskResult:
    """Tests for the TaskResult Pydantic model."""

    def test_task_result_defaults(self):
        work_product_id = uuid4()
        res = TaskResult(
            task_type="validate_stress",
            work_product_id=work_product_id,
            success=True,
        )
        assert res.skill_results == []
        assert res.errors == []
        assert res.warnings == []

    def test_task_result_with_data(self):
        work_product_id = uuid4()
        res = TaskResult(
            task_type="validate_stress",
            work_product_id=work_product_id,
            success=False,
            errors=["Something failed"],
            warnings=["Low quality mesh"],
            skill_results=[{"skill": "validate_stress", "data": {}}],
        )
        assert not res.success
        assert len(res.errors) == 1
        assert len(res.warnings) == 1
        assert len(res.skill_results) == 1


# --- PydanticAI integration ---


class TestMechanicalResult:
    """Tests for the MechanicalResult structured output model."""

    def test_mechanical_result_defaults(self):
        result = MechanicalResult()
        assert result.overall_passed is True
        assert result.max_stress_mpa == 0.0
        assert result.critical_region == ""
        assert result.work_products == []
        assert result.analysis == {}
        assert result.recommendations == []
        assert result.tool_calls == []

    def test_mechanical_result_with_data(self):
        result = MechanicalResult(
            overall_passed=False,
            max_stress_mpa=250.5,
            critical_region="bracket_mount",
            work_products=[{"type": "mesh", "path": "mesh/bracket.inp"}],
            analysis={"solver": "calculix", "time_s": 12.5},
            recommendations=["Increase wall thickness"],
            tool_calls=[{"tool": "validate_stress", "result": "fail"}],
        )
        assert not result.overall_passed
        assert result.max_stress_mpa == 250.5
        assert result.critical_region == "bracket_mount"
        assert len(result.work_products) == 1
        assert len(result.tool_calls) == 1


class TestAgentDependencies:
    """Tests for AgentDependencies dataclass."""

    def test_agent_dependencies_creation(self):
        twin = MagicMock()
        mcp = InMemoryMcpBridge()
        deps = AgentDependencies(
            twin=twin,
            mcp_bridge=mcp,
            session_id="test-session-123",
            branch="feature-1",
        )
        assert deps.twin is twin
        assert deps.mcp_bridge is mcp
        assert deps.session_id == "test-session-123"
        assert deps.branch == "feature-1"

    def test_agent_dependencies_defaults(self):
        deps = AgentDependencies(
            twin=MagicMock(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id="test",
        )
        assert deps.branch == "main"


class TestLlmConfiguration:
    """Tests for LLM configuration helpers."""

    def test_no_provider_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            os.environ.pop("METAFORGE_LLM_MODEL", None)
            assert get_llm_model() is None
            assert is_llm_available() is False

    def test_openai_provider(self):
        with patch.dict(os.environ, {"METAFORGE_LLM_PROVIDER": "openai"}, clear=False):
            os.environ.pop("METAFORGE_LLM_MODEL", None)
            model = get_llm_model()
            assert model == "openai:gpt-4o"
            assert is_llm_available() is True

    def test_openai_with_custom_model(self):
        with patch.dict(
            os.environ,
            {"METAFORGE_LLM_PROVIDER": "openai", "METAFORGE_LLM_MODEL": "gpt-4-turbo"},
            clear=False,
        ):
            assert get_llm_model() == "openai:gpt-4-turbo"

    def test_anthropic_provider(self):
        with patch.dict(
            os.environ,
            {"METAFORGE_LLM_PROVIDER": "anthropic"},
            clear=False,
        ):
            os.environ.pop("METAFORGE_LLM_MODEL", None)
            model = get_llm_model()
            assert model == "anthropic:claude-sonnet-4-20250514"

    def test_unknown_provider_returns_none(self):
        with patch.dict(
            os.environ,
            {"METAFORGE_LLM_PROVIDER": "unknown_provider"},
            clear=False,
        ):
            assert get_llm_model() is None


class TestHardcodedFallback:
    """Tests verifying hardcoded dispatch when LLM is unavailable."""

    async def test_fallback_when_no_llm_configured(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Agent should use hardcoded dispatch when METAFORGE_LLM_PROVIDER is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge)

            request = TaskRequest(
                task_type="validate_stress",
                work_product_id=uuid4(),
                parameters={
                    "mesh_file_path": "mesh/bracket.inp",
                    "load_case": "gravity",
                    "constraints": [
                        {"max_von_mises_mpa": 300.0, "safety_factor": 1.5},
                    ],
                },
            )
            result = await agent.run_task(request)

            # Should still work via hardcoded path
            assert result.success is True
            assert result.task_type == "validate_stress"

    async def test_unsupported_task_in_hardcoded_mode(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Unsupported tasks should fail gracefully in hardcoded mode."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge)

            request = TaskRequest(
                task_type="unsupported_task",
                work_product_id=uuid4(),
            )
            result = await agent.run_task(request)

            assert result.success is False
            assert any("Unsupported task type" in e for e in result.errors)
