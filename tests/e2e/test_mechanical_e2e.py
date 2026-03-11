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
                constraints=[StressConstraint(max_von_mises_mpa=276.0, material="Al6061-T6")],
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


# ---------------------------------------------------------------------------
# Test class: Check tolerances through MechanicalAgent
# ---------------------------------------------------------------------------


class TestCheckTolerancesE2E:
    """E2E tests for tolerance checking pipeline (pure computation, no MCP)."""

    @pytest.fixture
    async def stack(self):
        """Set up Twin + MCP + Agent for tolerance checking."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_mcp_stack()
        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)
        agent = MechanicalAgent(twin=twin, mcp=bridge)
        return {"twin": twin, "agent": agent, "artifact": created}

    async def test_tolerances_all_pass(self, stack):
        """All tolerances pass CNC milling capabilities (Cp >= 1.33)."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="check_tolerances",
                artifact_id=s["artifact"].id,
                parameters={
                    "manufacturing_process": {
                        "process_type": "cnc_milling",
                        "achievable_tolerance": 0.01,
                    },
                    "tolerances": [
                        {
                            "dimension_id": "D1",
                            "feature_name": "bore_diameter",
                            "nominal_value": 8.0,
                            "upper_tolerance": 0.05,
                            "lower_tolerance": -0.05,
                        },
                        {
                            "dimension_id": "D2",
                            "feature_name": "mounting_hole_spacing",
                            "nominal_value": 25.0,
                            "upper_tolerance": 0.1,
                            "lower_tolerance": -0.1,
                        },
                    ],
                },
            )
        )

        assert result.success is True
        assert result.task_type == "check_tolerances"
        assert len(result.skill_results) == 1

        tol_result = result.skill_results[0]
        assert tol_result["skill"] == "check_tolerance"
        assert tol_result["overall_status"] == "pass"
        assert tol_result["total_dimensions_checked"] == 2
        assert tol_result["passed"] == 2
        assert tol_result["failures"] == 0

    async def test_tolerance_too_tight_fails(self, stack):
        """Tolerance tighter than process capability triggers failure."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="check_tolerances",
                artifact_id=s["artifact"].id,
                parameters={
                    "manufacturing_process": {
                        "process_type": "3d_printing_fdm",
                        "achievable_tolerance": 0.3,
                    },
                    "tolerances": [
                        {
                            "dimension_id": "D1",
                            "feature_name": "pin_hole",
                            "nominal_value": 4.0,
                            "upper_tolerance": 0.01,
                            "lower_tolerance": -0.01,
                        },
                    ],
                },
            )
        )

        assert result.success is False
        assert result.skill_results[0]["overall_status"] == "fail"
        assert result.skill_results[0]["failures"] >= 1

    async def test_tolerance_marginal_warns(self, stack):
        """Marginal Cp (1.0 <= Cp < 1.33) yields marginal status with warnings."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="check_tolerances",
                artifact_id=s["artifact"].id,
                parameters={
                    "manufacturing_process": {
                        "process_type": "cnc_milling",
                        "achievable_tolerance": 0.05,
                    },
                    "tolerances": [
                        {
                            "dimension_id": "D1",
                            "feature_name": "slot_width",
                            "nominal_value": 10.0,
                            "upper_tolerance": 0.06,
                            "lower_tolerance": -0.06,
                        },
                    ],
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["overall_status"] == "marginal"
        assert result.skill_results[0]["warnings"] >= 1
        assert len(result.warnings) > 0

    async def test_tolerance_missing_process_fails(self, stack):
        """Missing manufacturing_process returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="check_tolerances",
                artifact_id=s["artifact"].id,
                parameters={
                    "tolerances": [
                        {
                            "dimension_id": "D1",
                            "feature_name": "bore",
                            "nominal_value": 8.0,
                            "upper_tolerance": 0.05,
                            "lower_tolerance": -0.05,
                        }
                    ],
                },
            )
        )

        assert result.success is False
        assert any("manufacturing_process" in e for e in result.errors)

    async def test_tolerance_stack_up_analysis(self, stack):
        """Stack-up analysis runs when check_stack_up is True."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="check_tolerances",
                artifact_id=s["artifact"].id,
                parameters={
                    "manufacturing_process": {
                        "process_type": "cnc_milling",
                        "achievable_tolerance": 0.01,
                    },
                    "tolerances": [
                        {
                            "dimension_id": "D1",
                            "feature_name": "part_a",
                            "nominal_value": 10.0,
                            "upper_tolerance": 0.05,
                            "lower_tolerance": -0.05,
                        },
                        {
                            "dimension_id": "D2",
                            "feature_name": "part_b",
                            "nominal_value": 15.0,
                            "upper_tolerance": 0.05,
                            "lower_tolerance": -0.05,
                        },
                    ],
                    "check_stack_up": True,
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["total_dimensions_checked"] == 2


# ---------------------------------------------------------------------------
# Test class: Generate mesh through MechanicalAgent
# ---------------------------------------------------------------------------


FREECAD_MESH_RESULT = {
    "mesh_file": "output/motor_mount_bracket.inp",
    "num_nodes": 24500,
    "num_elements": 65000,
    "element_types": ["C3D10"],
    "quality_metrics": {
        "min_angle": 22.5,
        "max_aspect_ratio": 5.8,
        "avg_quality": 0.87,
        "jacobian_ratio": 0.92,
    },
}

FREECAD_MESH_BAD_QUALITY = {
    "mesh_file": "output/motor_mount_bracket.inp",
    "num_nodes": 12000,
    "num_elements": 30000,
    "element_types": ["C3D4"],
    "quality_metrics": {
        "min_angle": 8.0,
        "max_aspect_ratio": 18.0,
        "avg_quality": 0.45,
        "jacobian_ratio": 0.6,
    },
}


class TestGenerateMeshE2E:
    """E2E tests for mesh generation pipeline via FreeCAD MCP."""

    @pytest.fixture
    async def stack(self):
        """Set up Twin + MCP (with FreeCAD tool) + Agent."""
        from skill_registry.mcp_bridge import InMemoryMcpBridge

        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        mcp.register_tool(
            "freecad.generate_mesh", capability="mesh_generation", name="Generate Mesh"
        )
        mcp.register_tool_response("freecad.generate_mesh", FREECAD_MESH_RESULT)

        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)
        agent = MechanicalAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "mcp": mcp, "agent": agent, "artifact": created}

    async def test_generate_mesh_good_quality(self, stack):
        """Mesh generation succeeds with acceptable quality metrics."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_mesh",
                artifact_id=s["artifact"].id,
                parameters={
                    "cad_file": "models/motor_mount_bracket.step",
                    "element_size": 0.5,
                    "algorithm": "netgen",
                    "output_format": "inp",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "generate_mesh"
        assert len(result.skill_results) == 1

        mesh_result = result.skill_results[0]
        assert mesh_result["skill"] == "generate_mesh"
        assert mesh_result["num_nodes"] == 24500
        assert mesh_result["num_elements"] == 65000
        assert mesh_result["quality_acceptable"] is True
        assert mesh_result["algorithm_used"] == "netgen"
        assert mesh_result["quality_issues"] == []

    async def test_generate_mesh_bad_quality_fails(self):
        """Mesh fails quality thresholds (low min_angle, high aspect ratio)."""
        from skill_registry.mcp_bridge import InMemoryMcpBridge

        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        mcp.register_tool(
            "freecad.generate_mesh", capability="mesh_generation", name="Generate Mesh"
        )
        mcp.register_tool_response("freecad.generate_mesh", FREECAD_MESH_BAD_QUALITY)

        artifact = await twin.create_artifact(_make_bracket_artifact())
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_mesh",
                artifact_id=artifact.id,
                parameters={
                    "cad_file": "models/motor_mount_bracket.step",
                    "min_angle_threshold": 15.0,
                    "max_aspect_ratio_threshold": 10.0,
                },
            )
        )

        assert result.success is False
        assert result.skill_results[0]["quality_acceptable"] is False
        assert len(result.skill_results[0]["quality_issues"]) > 0
        assert len(result.warnings) > 0

    async def test_generate_mesh_missing_cad_file(self, stack):
        """Missing cad_file parameter returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_mesh",
                artifact_id=s["artifact"].id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("cad_file" in e for e in result.errors)

    async def test_generate_mesh_unsupported_extension(self, stack):
        """Unsupported CAD file extension returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_mesh",
                artifact_id=s["artifact"].id,
                parameters={
                    "cad_file": "models/bracket.dwg",
                },
            )
        )

        assert result.success is False

    async def test_generate_mesh_gmsh_algorithm(self, stack):
        """Mesh generation works with gmsh algorithm."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_mesh",
                artifact_id=s["artifact"].id,
                parameters={
                    "cad_file": "models/motor_mount_bracket.stp",
                    "algorithm": "gmsh",
                    "output_format": "unv",
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["algorithm_used"] == "gmsh"


# ---------------------------------------------------------------------------
# Test class: Generate CAD through MechanicalAgent
# ---------------------------------------------------------------------------


FREECAD_CAD_RESULT = {
    "cad_file": "output/bracket_generated.step",
    "volume_mm3": 12500.0,
    "surface_area_mm2": 8400.0,
    "bounding_box": {
        "min_x": 0.0,
        "min_y": 0.0,
        "min_z": 0.0,
        "max_x": 50.0,
        "max_y": 30.0,
        "max_z": 5.0,
    },
    "parameters_used": {"width": 50.0, "height": 30.0, "thickness": 5.0},
}


class TestGenerateCadE2E:
    """E2E tests for parametric CAD generation pipeline via FreeCAD MCP."""

    @pytest.fixture
    async def stack(self):
        """Set up Twin + MCP (with FreeCAD create_parametric tool) + Agent."""
        from skill_registry.mcp_bridge import InMemoryMcpBridge

        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        mcp.register_tool(
            "freecad.create_parametric",
            capability="cad_generation",
            name="Create Parametric",
        )
        mcp.register_tool_response("freecad.create_parametric", FREECAD_CAD_RESULT)

        artifact = _make_bracket_artifact()
        created = await twin.create_artifact(artifact)
        agent = MechanicalAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "mcp": mcp, "agent": agent, "artifact": created}

    async def test_generate_bracket(self, stack):
        """Happy path: generate a bracket with full dimensions."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_cad",
                artifact_id=s["artifact"].id,
                parameters={
                    "shape_type": "bracket",
                    "dimensions": {"width": 50.0, "height": 30.0, "thickness": 5.0},
                    "material": "aluminum_6061",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "generate_cad"
        assert len(result.skill_results) == 1

        cad_result = result.skill_results[0]
        assert cad_result["skill"] == "generate_cad"
        assert cad_result["cad_file"] == "output/bracket_generated.step"
        assert cad_result["volume_mm3"] == 12500.0
        assert cad_result["surface_area_mm2"] == 8400.0
        assert cad_result["shape_type"] == "bracket"
        assert cad_result["material"] == "aluminum_6061"
        assert cad_result["bounding_box"]["max_x"] == 50.0

    async def test_generate_plate(self, stack):
        """Generate a plate shape type."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_cad",
                artifact_id=s["artifact"].id,
                parameters={
                    "shape_type": "plate",
                    "dimensions": {"width": 100.0, "height": 80.0, "thickness": 2.0},
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["shape_type"] == "plate"

    async def test_generate_missing_shape_type(self, stack):
        """Missing shape_type parameter returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_cad",
                artifact_id=s["artifact"].id,
                parameters={
                    "dimensions": {"width": 50.0},
                },
            )
        )

        assert result.success is False
        assert any("shape_type" in e for e in result.errors)

    async def test_generate_missing_dimensions(self, stack):
        """Missing dimensions parameter returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_cad",
                artifact_id=s["artifact"].id,
                parameters={
                    "shape_type": "bracket",
                },
            )
        )

        assert result.success is False
        assert any("dimensions" in e for e in result.errors)

    async def test_generate_unsupported_shape(self, stack):
        """Unsupported shape_type returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_cad",
                artifact_id=s["artifact"].id,
                parameters={
                    "shape_type": "gearbox",
                    "dimensions": {"width": 50.0},
                },
            )
        )

        assert result.success is False
