"""Tests for the mechanical engineering domain agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest, TaskResult
from skill_registry.mcp_bridge import InMemoryMcpBridge


@pytest.fixture
def mock_twin() -> AsyncMock:
    twin = AsyncMock()
    # Default: artifact exists
    twin.get_artifact.return_value = MagicMock(
        id=uuid4(), name="bracket", domain="mechanical"
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
            "full_validation",
        }


# --- Stress validation ---


class TestValidateStress:
    """Tests for the validate_stress task type."""

    async def test_stress_passes(self, agent: MechanicalAgent):
        """Stress below allowable limit should pass."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            artifact_id=artifact_id,
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
        assert result.artifact_id == artifact_id
        assert len(result.errors) == 0
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["overall_passed"] is True

    async def test_stress_fails(self, agent: MechanicalAgent):
        """Stress exceeding allowable limit should fail."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            artifact_id=artifact_id,
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
        """Missing artifact should produce an error."""
        mock_twin.get_artifact.return_value = None
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            artifact_id=artifact_id,
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_fea_solver_error(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """FEA solver failure should produce an error."""
        # Create a bridge with no response registered for the tool
        bad_bridge = InMemoryMcpBridge()
        # No tool response registered -- invoke will raise McpToolError
        agent = MechanicalAgent(twin=mock_twin, mcp=bad_bridge)

        artifact_id = uuid4()
        request = TaskRequest(
            task_type="validate_stress",
            artifact_id=artifact_id,
            parameters={"mesh_file_path": "mesh/bracket.inp"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("FEA solver failed" in e for e in result.errors)

    async def test_stress_no_constraints(self, agent: MechanicalAgent):
        """No constraints means all pass (vacuous truth)."""
        request = TaskRequest(
            task_type="validate_stress",
            artifact_id=uuid4(),
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
            artifact_id=uuid4(),
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
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("cad_file" in e for e in result.errors)


# --- Full validation ---


class TestFullValidation:
    """Tests for the full_validation task type."""

    async def test_full_validation_delegates_to_stress(self, agent: MechanicalAgent):
        """Full validation should run stress validation and aggregate results."""
        artifact_id = uuid4()
        request = TaskRequest(
            task_type="full_validation",
            artifact_id=artifact_id,
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
        assert result.artifact_id == artifact_id
        assert result.success is True
        assert len(result.skill_results) >= 1
        assert result.skill_results[0]["skill"] == "validate_stress"


# --- Unsupported task ---


class TestUnsupportedTask:
    """Tests for unsupported task types."""

    async def test_unsupported_task_type(self, agent: MechanicalAgent):
        request = TaskRequest(
            task_type="do_magic",
            artifact_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("Unsupported task type" in e for e in result.errors)
        assert "do_magic" in result.errors[0]


# --- TaskRequest model ---


class TestTaskRequest:
    """Tests for the TaskRequest Pydantic model."""

    def test_task_request_defaults(self):
        artifact_id = uuid4()
        req = TaskRequest(task_type="validate_stress", artifact_id=artifact_id)
        assert req.branch == "main"
        assert req.parameters == {}

    def test_task_request_with_parameters(self):
        artifact_id = uuid4()
        params = {"mesh_file_path": "mesh/bracket.inp", "load_case": "gravity"}
        req = TaskRequest(
            task_type="validate_stress",
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
            task_type="validate_stress",
            artifact_id=artifact_id,
            success=True,
        )
        assert res.skill_results == []
        assert res.errors == []
        assert res.warnings == []

    def test_task_result_with_data(self):
        artifact_id = uuid4()
        res = TaskResult(
            task_type="validate_stress",
            artifact_id=artifact_id,
            success=False,
            errors=["Something failed"],
            warnings=["Low quality mesh"],
            skill_results=[{"skill": "validate_stress", "data": {}}],
        )
        assert not res.success
        assert len(res.errors) == 1
        assert len(res.warnings) == 1
        assert len(res.skill_results) == 1
