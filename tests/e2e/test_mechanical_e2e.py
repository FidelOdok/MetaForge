"""End-to-end tests for the mechanical engineering vertical.

Exercises the full stack: MechanicalAgent → MCP Protocol → CalculiX Server → Digital Twin.
No mocks of internal interfaces — only the CalculiX solver binary is stubbed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest
from domain_agents.mechanical.skills.validate_stress.handler import ValidateStressHandler
from domain_agents.mechanical.skills.validate_stress.schema import (
    StressConstraint,
    ValidateStressInput,
)
from mcp_core.client import McpClient
from mcp_core.schemas import ToolManifest as ClientToolManifest
from mcp_core.transports import LoopbackTransport
from skill_registry.mcp_client_bridge import McpClientBridge
from skill_registry.skill_base import SkillContext
from tool_registry.tools.calculix.adapter import CalculixServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.artifact import Artifact
from twin_core.models.enums import ArtifactType

# Realistic FEA results for a drone motor mount bracket
BRACKET_FEA_RESULT = {
    "max_von_mises": {
        "bracket_body": 85.3,
        "bracket_mount": 42.1,
        "fillet_region": 120.7,
    },
    "solver_time": 14.2,
    "mesh_elements": 52000,
    "node_count": 18500,
}


def _create_calculix_server() -> CalculixServer:
    """Create a CalculiX server with a stubbed solver returning realistic data."""
    server = CalculixServer()
    server._execute_solver = AsyncMock(return_value=BRACKET_FEA_RESULT)
    server._execute_thermal_solver = AsyncMock(
        return_value={
            "max_temperature": 85.0,
            "min_temperature": 22.0,
            "temperature_distribution": {"motor_zone": 85.0, "frame_zone": 35.0},
            "solver_time": 8.5,
        }
    )
    server._validate_mesh_file = AsyncMock(
        return_value={
            "valid": True,
            "element_count": 52000,
            "node_count": 18500,
            "max_aspect_ratio": 4.2,
            "issues": [],
        }
    )
    return server


async def _setup_mcp_stack() -> tuple[McpClient, McpClientBridge, CalculixServer]:
    """Wire up CalculixServer → LoopbackTransport → McpClient → McpClientBridge."""
    server = _create_calculix_server()
    transport = LoopbackTransport(server)
    client = McpClient()
    await client.connect("calculix", transport)

    # Register tool manifests on the client side (mirroring server's registrations)
    for tool_id, reg in server._tools.items():
        m = reg.manifest
        client.register_manifest(
            ClientToolManifest(
                tool_id=m.tool_id,
                adapter_id=m.adapter_id,
                name=m.name,
                description=m.description,
                capability=m.capability,
                input_schema=m.input_schema,
                output_schema=m.output_schema,
                phase=m.phase,
            )
        )

    bridge = McpClientBridge(client)
    return client, bridge, server


def _make_bracket_artifact() -> Artifact:
    """Create a realistic drone motor mount bracket artifact."""
    return Artifact(
        name="motor-mount-bracket",
        type=ArtifactType.CAD_MODEL,
        domain="mechanical",
        file_path="models/motor_mount_bracket.step",
        content_hash="sha256:a1b2c3d4e5f6",
        format="step",
        created_by="human",
        metadata={
            "material": "Al6061-T6",
            "mass_kg": 0.045,
            "description": "Motor mount bracket for drone flight controller",
        },
    )


# ---------------------------------------------------------------------------
# Test class: Full vertical through MechanicalAgent
# ---------------------------------------------------------------------------


class TestMechanicalAgentE2E:
    """E2E tests exercising MechanicalAgent → MCP → CalculiX → Twin."""

    @pytest.fixture
    async def stack(self):
        """Set up the complete stack: Twin + MCP + Agent."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_mcp_stack()

        # Add artifact to the Twin
        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)

        agent = MechanicalAgent(twin=twin, mcp=bridge)

        return {
            "twin": twin,
            "client": client,
            "bridge": bridge,
            "server": server,
            "agent": agent,
            "artifact": created,
        }

    async def test_validate_stress_passes(self, stack):
        """Stress validation passes when all regions are within limits."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="validate_stress",
                artifact_id=s["artifact"].id,
                parameters={
                    "mesh_file_path": "models/motor_mount_bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {
                            "max_von_mises_mpa": 276.0,  # Al6061-T6 yield
                            "safety_factor": 1.5,
                            "material": "Al6061-T6",
                        }
                    ],
                },
            )
        )

        assert result.success is True
        assert result.task_type == "validate_stress"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["overall_passed"] is True
        assert result.errors == []

        # Verify the CalculiX solver was actually called through the full stack
        s["server"]._execute_solver.assert_awaited_once_with(
            "models/motor_mount_bracket.inp", "static_stress"
        )

    async def test_validate_stress_fails_tight_constraint(self, stack):
        """Stress validation fails when constraint is too tight for the fillet region."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="validate_stress",
                artifact_id=s["artifact"].id,
                parameters={
                    "mesh_file_path": "models/motor_mount_bracket.inp",
                    "load_case": "crash_10g",
                    "constraints": [
                        {
                            "max_von_mises_mpa": 150.0,  # Too tight for fillet (120.7 / 1.5 = 100)
                            "safety_factor": 1.5,
                            "material": "Al6061-T6",
                        }
                    ],
                },
            )
        )

        assert result.success is False
        assert len(result.warnings) > 0
        # fillet_region at 120.7 MPa exceeds 150/1.5 = 100 MPa allowable
        constraint_results = result.skill_results[0]["constraint_results"]
        fillet = [r for r in constraint_results if r["region"] == "fillet_region"]
        assert len(fillet) == 1
        assert fillet[0]["passed"] is False

    async def test_artifact_not_found(self, stack):
        """Agent returns error when artifact doesn't exist in Twin."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="validate_stress",
                artifact_id=uuid4(),  # Non-existent
                parameters={"mesh_file_path": "x.inp", "load_case": "lc1"},
            )
        )

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_full_validation(self, stack):
        """Full validation runs the stress pipeline end-to-end."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="full_validation",
                artifact_id=s["artifact"].id,
                parameters={
                    "mesh_file_path": "models/motor_mount_bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {
                            "max_von_mises_mpa": 276.0,
                            "safety_factor": 1.5,
                            "material": "Al6061-T6",
                        }
                    ],
                },
            )
        )

        assert result.success is True
        assert result.task_type == "full_validation"

    async def test_unsupported_task_type(self, stack):
        """Agent rejects unknown task types."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="analyze_vibration",
                artifact_id=s["artifact"].id,
            )
        )

        assert result.success is False
        assert any("Unsupported" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: Full vertical through ValidateStressHandler (skill path)
# ---------------------------------------------------------------------------


class TestValidateStressSkillE2E:
    """E2E tests exercising ValidateStressHandler → MCP → CalculiX → Twin."""

    @pytest.fixture
    async def skill_stack(self):
        """Set up the complete stack for direct skill execution."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_mcp_stack()

        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)

        context = SkillContext(
            twin=twin,
            mcp=bridge,
            logger=structlog.get_logger().bind(skill="validate_stress"),
            session_id=uuid4(),
            branch="main",
        )
        handler = ValidateStressHandler(context)

        return {
            "twin": twin,
            "server": server,
            "handler": handler,
            "artifact": created,
        }

    async def test_skill_execute_pass(self, skill_stack):
        """Skill execution returns pass with detailed results."""
        s = skill_stack
        output = await s["handler"].execute(
            ValidateStressInput(
                artifact_id=s["artifact"].id,
                mesh_file_path="models/motor_mount_bracket.inp",
                load_case="hover_3g",
                constraints=[
                    StressConstraint(
                        max_von_mises_mpa=276.0,
                        safety_factor=1.5,
                        material="Al6061-T6",
                    )
                ],
            )
        )

        assert output.overall_passed is True
        assert output.artifact_id == s["artifact"].id
        assert output.max_stress_mpa == 120.7  # fillet_region
        assert output.critical_region == "fillet_region"
        assert output.solver_time_seconds == 14.2
        assert output.mesh_elements == 52000
        assert len(output.results) == 3  # 3 regions

        # Check safety factor for each region
        for result in output.results:
            assert result.passed is True
            assert result.safety_factor_achieved > 1.5

    async def test_skill_execute_fail(self, skill_stack):
        """Skill execution returns failure with violated constraints."""
        s = skill_stack
        output = await s["handler"].execute(
            ValidateStressInput(
                artifact_id=s["artifact"].id,
                mesh_file_path="models/motor_mount_bracket.inp",
                load_case="crash_10g",
                constraints=[
                    StressConstraint(
                        max_von_mises_mpa=100.0,  # Very tight
                        safety_factor=1.0,
                        material="Al6061-T6",
                    )
                ],
            )
        )

        assert output.overall_passed is False
        failed_regions = [r for r in output.results if not r.passed]
        assert len(failed_regions) == 1  # Only fillet at 120.7 > 100.0
        assert failed_regions[0].region == "fillet_region"

    async def test_skill_run_pipeline(self, skill_stack):
        """Full skill pipeline (validate → preconditions → execute → wrap)."""
        s = skill_stack
        skill_result = await s["handler"].run(
            ValidateStressInput(
                artifact_id=s["artifact"].id,
                mesh_file_path="models/motor_mount_bracket.inp",
                load_case="hover_3g",
                constraints=[
                    StressConstraint(
                        max_von_mises_mpa=276.0,
                        safety_factor=1.5,
                        material="Al6061-T6",
                    )
                ],
            )
        )

        assert skill_result.success is True
        assert skill_result.data is not None
        assert skill_result.duration_ms > 0
        assert skill_result.errors == []

    async def test_skill_precondition_missing_artifact(self, skill_stack):
        """Skill fails preconditions when artifact not in Twin."""
        s = skill_stack
        skill_result = await s["handler"].run(
            ValidateStressInput(
                artifact_id=uuid4(),  # Not in Twin
                mesh_file_path="models/missing.inp",
                load_case="lc1",
                constraints=[
                    StressConstraint(
                        max_von_mises_mpa=276.0, material="Al6061-T6"
                    )
                ],
            )
        )

        assert skill_result.success is False
        assert any("not found" in e for e in skill_result.errors)


# ---------------------------------------------------------------------------
# Test class: MCP protocol layer verification
# ---------------------------------------------------------------------------


class TestMcpProtocolE2E:
    """Verify the MCP protocol stack works end-to-end."""

    async def test_tool_discovery_through_stack(self):
        """McpClient discovers tools through LoopbackTransport."""
        client, bridge, _server = await _setup_mcp_stack()
        tools = await bridge.list_tools()
        tool_ids = {t["tool_id"] for t in tools}

        assert "calculix.run_fea" in tool_ids
        assert "calculix.run_thermal" in tool_ids
        assert "calculix.validate_mesh" in tool_ids

    async def test_tool_availability_check(self):
        """Bridge correctly reports tool availability."""
        _client, bridge, _server = await _setup_mcp_stack()

        assert await bridge.is_available("calculix.run_fea") is True
        assert await bridge.is_available("nonexistent.tool") is False

    async def test_tool_invocation_through_full_stack(self):
        """Direct tool invocation through the full MCP protocol stack."""
        _client, bridge, server = await _setup_mcp_stack()

        result = await bridge.invoke(
            "calculix.run_fea",
            {
                "mesh_file": "test.inp",
                "load_case": "lc1",
                "analysis_type": "static_stress",
            },
        )

        assert result["max_von_mises"]["bracket_body"] == 85.3
        assert result["solver_time"] == 14.2
        server._execute_solver.assert_awaited_once()

    async def test_capability_filter(self):
        """Bridge filters tools by capability."""
        _client, bridge, _server = await _setup_mcp_stack()

        stress_tools = await bridge.list_tools(capability="stress_analysis")
        assert len(stress_tools) == 1
        assert stress_tools[0]["tool_id"] == "calculix.run_fea"

        thermal_tools = await bridge.list_tools(capability="thermal_analysis")
        assert len(thermal_tools) == 1
        assert thermal_tools[0]["tool_id"] == "calculix.run_thermal"

    async def test_health_check_through_loopback(self):
        """Health check works through LoopbackTransport."""
        client, _bridge, _server = await _setup_mcp_stack()
        health = await client.health_check("calculix")

        assert health.status == "healthy"
        assert health.adapter_id == "calculix"
        assert health.version == "0.1.0"
        assert health.tools_available == 3


# ---------------------------------------------------------------------------
# Test class: Twin integration
# ---------------------------------------------------------------------------


class TestTwinIntegrationE2E:
    """Verify Digital Twin operations work within the E2E pipeline."""

    async def test_artifact_lifecycle_in_pipeline(self):
        """Create artifact, run analysis, update with results."""
        twin = InMemoryTwinAPI.create()
        _client, bridge, _server = await _setup_mcp_stack()

        # 1. Create artifact in Twin
        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)
        assert await twin.get_artifact(created.id) is not None

        # 2. Run agent analysis
        agent = MechanicalAgent(twin=twin, mcp=bridge)
        result = await agent.run_task(
            TaskRequest(
                task_type="validate_stress",
                artifact_id=created.id,
                parameters={
                    "mesh_file_path": "models/motor_mount_bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {
                            "max_von_mises_mpa": 276.0,
                            "safety_factor": 1.5,
                            "material": "Al6061-T6",
                        }
                    ],
                },
            )
        )
        assert result.success is True

        # 3. Update artifact with analysis results
        updated = await twin.update_artifact(
            created.id,
            {
                "metadata": {
                    **created.metadata,
                    "stress_analysis": {
                        "status": "passed",
                        "max_stress_mpa": 120.7,
                        "critical_region": "fillet_region",
                    },
                }
            },
        )
        assert "stress_analysis" in updated.metadata
        assert updated.metadata["stress_analysis"]["status"] == "passed"

    async def test_branched_analysis(self):
        """Run analysis on a design branch."""
        twin = InMemoryTwinAPI.create()
        _client, bridge, _server = await _setup_mcp_stack()

        # Create artifact on main
        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)

        # Initialize main branch and commit, then create design branch
        await twin.create_branch("main")
        await twin.commit("main", "Add motor mount bracket", "engineer")
        await twin.create_branch("design/v2-bracket", from_branch="main")

        # Run analysis (agent uses main branch by default)
        agent = MechanicalAgent(twin=twin, mcp=bridge)
        result = await agent.run_task(
            TaskRequest(
                task_type="validate_stress",
                artifact_id=created.id,
                parameters={
                    "mesh_file_path": "models/motor_mount_bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {
                            "max_von_mises_mpa": 276.0,
                            "safety_factor": 1.5,
                            "material": "Al6061-T6",
                        }
                    ],
                },
            )
        )
        assert result.success is True
