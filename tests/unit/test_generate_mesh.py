"""Tests for the generate_mesh skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest
from domain_agents.mechanical.skills.generate_mesh.handler import GenerateMeshHandler
from domain_agents.mechanical.skills.generate_mesh.schema import (
    GenerateMeshInput,
    GenerateMeshOutput,
    MeshQualityMetrics,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_MESH_RESPONSE = {
    "mesh_file": "/tmp/bracket.inp",
    "num_nodes": 12500,
    "num_elements": 45000,
    "element_types": ["C3D10"],
    "quality_metrics": {
        "min_angle": 22.5,
        "max_aspect_ratio": 4.2,
        "avg_quality": 0.87,
        "jacobian_ratio": 0.0,
    },
}

BAD_QUALITY_MESH_RESPONSE = {
    "mesh_file": "/tmp/bracket_bad.inp",
    "num_nodes": 8000,
    "num_elements": 30000,
    "element_types": ["C3D4"],
    "quality_metrics": {
        "min_angle": 8.0,
        "max_aspect_ratio": 15.0,
        "avg_quality": 0.45,
        "jacobian_ratio": 0.0,
    },
}


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture()
def sample_input() -> GenerateMeshInput:
    return GenerateMeshInput(
        artifact_id="artifact-mesh-1",
        cad_file="/project/cad/bracket.step",
        element_size=1.0,
        algorithm="netgen",
        output_format="inp",
    )


# ---------------------------------------------------------------------------
# TestMeshModels
# ---------------------------------------------------------------------------


class TestMeshModels:
    def test_mesh_quality_metrics_defaults(self) -> None:
        m = MeshQualityMetrics()
        assert m.min_angle == 0.0
        assert m.max_aspect_ratio == 0.0
        assert m.avg_quality == 0.0
        assert m.jacobian_ratio == 0.0

    def test_generate_mesh_input_defaults(self) -> None:
        inp = GenerateMeshInput(
            artifact_id="art-1",
            cad_file="/path/to/model.step",
        )
        assert inp.element_size == 1.0
        assert inp.algorithm == "netgen"
        assert inp.output_format == "inp"
        assert inp.min_angle_threshold == 15.0
        assert inp.max_aspect_ratio_threshold == 10.0
        assert inp.refinement_regions == []

    def test_generate_mesh_input_custom(self) -> None:
        inp = GenerateMeshInput(
            artifact_id="art-2",
            cad_file="/path/to/model.stl",
            element_size=0.5,
            algorithm="gmsh",
            output_format="unv",
            min_angle_threshold=20.0,
            max_aspect_ratio_threshold=5.0,
            refinement_regions=[{"name": "fillet", "element_size": 0.2}],
        )
        assert inp.element_size == 0.5
        assert inp.algorithm == "gmsh"
        assert inp.output_format == "unv"
        assert inp.min_angle_threshold == 20.0
        assert inp.max_aspect_ratio_threshold == 5.0
        assert len(inp.refinement_regions) == 1

    def test_generate_mesh_output_model(self) -> None:
        output = GenerateMeshOutput(
            artifact_id="art-1",
            mesh_file="/tmp/mesh.inp",
            num_nodes=1000,
            num_elements=5000,
            element_types=["C3D10", "C3D4"],
            quality_metrics=MeshQualityMetrics(
                min_angle=18.0, max_aspect_ratio=5.0, avg_quality=0.9
            ),
            quality_acceptable=True,
            quality_issues=[],
            algorithm_used="netgen",
            element_size_used=1.0,
        )
        assert output.num_nodes == 1000
        assert output.num_elements == 5000
        assert output.quality_acceptable is True
        assert output.quality_issues == []
        assert len(output.element_types) == 2

    def test_invalid_algorithm_not_blocked_by_schema(self) -> None:
        """Schema doesn't validate algorithm enum -- handler does that."""
        inp = GenerateMeshInput(
            artifact_id="art-1",
            cad_file="/path/to/model.step",
            algorithm="invalid_algo",
        )
        assert inp.algorithm == "invalid_algo"


# ---------------------------------------------------------------------------
# TestGenerateMeshHandler
# ---------------------------------------------------------------------------


class TestGenerateMeshHandler:
    async def test_generate_mesh_success(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Successful mesh generation with good quality."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.mesh_file == "/tmp/bracket.inp"
        assert output.num_nodes == 12500
        assert output.num_elements == 45000
        assert output.element_types == ["C3D10"]
        assert output.algorithm_used == "netgen"
        assert output.element_size_used == 1.0

    async def test_generate_mesh_quality_acceptable(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Mesh with quality within thresholds should be acceptable."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.quality_acceptable is True
        assert output.quality_issues == []
        assert output.quality_metrics.min_angle == 22.5
        assert output.quality_metrics.max_aspect_ratio == 4.2
        assert output.quality_metrics.avg_quality == 0.87

    async def test_generate_mesh_quality_unacceptable(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Mesh with quality outside thresholds should be unacceptable."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", BAD_QUALITY_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.quality_acceptable is False
        assert len(output.quality_issues) == 2
        # min_angle 8.0 < threshold 15.0
        assert any("angle" in issue.lower() for issue in output.quality_issues)
        # max_aspect_ratio 15.0 > threshold 10.0
        assert any("aspect ratio" in issue.lower() for issue in output.quality_issues)

    async def test_generate_mesh_with_custom_element_size(
        self, mock_context: SkillContext
    ) -> None:
        """Custom element size is passed through to the output."""
        inp = GenerateMeshInput(
            artifact_id="art-custom",
            cad_file="/project/cad/bracket.step",
            element_size=0.25,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        output = await handler.execute(inp)

        assert output.element_size_used == 0.25

    async def test_generate_mesh_with_gmsh_algorithm(
        self, mock_context: SkillContext
    ) -> None:
        """Gmsh algorithm should be accepted and passed through."""
        inp = GenerateMeshInput(
            artifact_id="art-gmsh",
            cad_file="/project/cad/bracket.step",
            algorithm="gmsh",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        output = await handler.execute(inp)

        assert output.algorithm_used == "gmsh"

    async def test_generate_mesh_missing_cad_file(
        self, mock_context: SkillContext
    ) -> None:
        """Missing CAD file extension should raise ValueError."""
        inp = GenerateMeshInput(
            artifact_id="art-bad",
            cad_file="/project/cad/bracket.iges",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        with pytest.raises(ValueError, match="Unsupported CAD file extension"):
            await handler.execute(inp)

    async def test_generate_mesh_unsupported_format(
        self, mock_context: SkillContext
    ) -> None:
        """Unsupported output format should raise ValueError."""
        inp = GenerateMeshInput(
            artifact_id="art-fmt",
            cad_file="/project/cad/bracket.step",
            output_format="vtk",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        with pytest.raises(ValueError, match="Unsupported output format"):
            await handler.execute(inp)

    async def test_generate_mesh_tool_not_available(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """MCP tool invocation should fail when tool is registered but no response exists."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        # Register tool as available so precondition passes, but don't register
        # a response so invoke() raises McpToolError
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")

        handler = GenerateMeshHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is False
        assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# TestGenerateMeshPreconditions
# ---------------------------------------------------------------------------


class TestGenerateMeshPreconditions:
    async def test_precondition_tool_available(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Preconditions should pass when artifact exists and tool is available."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")

        handler = GenerateMeshHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert errors == []

    async def test_precondition_tool_unavailable(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Preconditions should fail when FreeCAD tool is not available."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        # Don't register the tool => not available

        handler = GenerateMeshHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not available" in errors[0]

    async def test_precondition_artifact_missing(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Preconditions should fail when artifact is not found."""
        mock_context.twin.get_artifact.return_value = None
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")

        handler = GenerateMeshHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not found in Twin" in errors[0]


# ---------------------------------------------------------------------------
# TestGenerateMeshPipeline
# ---------------------------------------------------------------------------


class TestGenerateMeshPipeline:
    async def test_full_run_success(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """Full run() pipeline should return SkillResult with success=True."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", GOOD_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, GenerateMeshOutput)
        assert result.data.quality_acceptable is True
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_full_run_tool_unavailable(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """run() should return failure when preconditions are not met."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        # Don't register tool => precondition fails

        handler = GenerateMeshHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is False
        assert len(result.errors) >= 1
        assert result.data is None

    async def test_full_run_with_quality_issues(
        self, mock_context: SkillContext, sample_input: GenerateMeshInput
    ) -> None:
        """run() should succeed but report quality issues."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mock_context.mcp.register_tool_response(
            "freecad.generate_mesh", BAD_QUALITY_MESH_RESPONSE
        )

        handler = GenerateMeshHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is True
        assert result.data is not None
        assert result.data.quality_acceptable is False
        assert len(result.data.quality_issues) == 2


# ---------------------------------------------------------------------------
# TestGenerateMeshWithAgent
# ---------------------------------------------------------------------------


class TestGenerateMeshWithAgent:
    async def test_agent_routes_generate_mesh_task(self) -> None:
        """Agent should route generate_mesh task to the mesh handler."""
        twin = AsyncMock()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        artifact_id = uuid4()
        twin.get_artifact.return_value = {"id": str(artifact_id)}
        mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        mcp.register_tool_response("freecad.generate_mesh", GOOD_MESH_RESPONSE)

        request = TaskRequest(
            task_type="generate_mesh",
            artifact_id=artifact_id,
            parameters={
                "cad_file": "/project/cad/bracket.step",
                "element_size": 1.0,
                "algorithm": "netgen",
            },
        )

        result = await agent.run_task(request)

        assert result.task_type == "generate_mesh"
        assert result.success is True
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "generate_mesh"
        assert result.skill_results[0]["mesh_file"] == "/tmp/bracket.inp"
        assert result.skill_results[0]["num_nodes"] == 12500
        assert result.skill_results[0]["num_elements"] == 45000
        assert result.skill_results[0]["quality_acceptable"] is True

    async def test_agent_generate_mesh_missing_cad_file(self) -> None:
        """Agent should return error when cad_file parameter is missing."""
        twin = AsyncMock()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        artifact_id = uuid4()
        twin.get_artifact.return_value = {"id": str(artifact_id)}

        request = TaskRequest(
            task_type="generate_mesh",
            artifact_id=artifact_id,
            parameters={},
        )

        result = await agent.run_task(request)

        assert result.success is False
        assert any("cad_file" in e for e in result.errors)
