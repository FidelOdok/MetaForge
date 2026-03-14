"""Tests for the mechanical design workflow pipeline (MET-220)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest
from domain_agents.mechanical.workflows import (
    DesignWorkflowParams,
    MechanicalDesignWorkflow,
    StepResult,
    WorkflowResult,
)
from shared.storage import FileStorageService
from skill_registry.mcp_bridge import InMemoryMcpBridge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_root(tmp_path: object) -> str:
    """Create a temporary storage root."""
    # tmp_path is a pathlib.Path from pytest
    root = str(tmp_path)  # type: ignore[arg-type]
    return root


@pytest.fixture
def storage(storage_root: str) -> FileStorageService:
    return FileStorageService(storage_root=storage_root)


@pytest.fixture
def mock_twin() -> AsyncMock:
    twin = AsyncMock()
    # Default: work_product exists
    twin.get_work_product.return_value = MagicMock(id=uuid4(), name="bracket", domain="mechanical")
    twin.create_work_product.return_value = MagicMock(id=uuid4())
    twin.update_work_product.return_value = MagicMock(id=uuid4())
    return twin


@pytest.fixture
def mcp_bridge() -> InMemoryMcpBridge:
    """MCP bridge with all tools registered for a successful pipeline."""
    bridge = InMemoryMcpBridge()

    # CAD tool
    bridge.register_tool("freecad.create_parametric", "cad_generation")
    bridge.register_tool_response(
        "freecad.create_parametric",
        {
            "cad_file": "output/bracket_test.step",
            "volume_mm3": 12500.0,
            "surface_area_mm2": 5400.0,
            "bounding_box": {
                "min_x": 0.0,
                "min_y": 0.0,
                "min_z": 0.0,
                "max_x": 100.0,
                "max_y": 50.0,
                "max_z": 10.0,
            },
            "parameters_used": {"width": 100.0, "height": 50.0, "thickness": 10.0},
        },
    )

    # Mesh tool
    bridge.register_tool("freecad.generate_mesh", "meshing")
    bridge.register_tool_response(
        "freecad.generate_mesh",
        {
            "mesh_file": "output/bracket_test.inp",
            "num_nodes": 12000,
            "num_elements": 45000,
            "element_types": ["C3D10"],
            "quality_metrics": {
                "min_angle": 25.0,
                "max_aspect_ratio": 3.5,
                "avg_quality": 0.85,
                "jacobian_ratio": 0.92,
            },
        },
    )

    # FEA tool
    bridge.register_tool("calculix.run_fea", "stress_analysis")
    bridge.register_tool_response(
        "calculix.run_fea",
        {
            "max_von_mises": {"bracket_body": 80.0, "bracket_mount": 40.0},
            "solver_time": 12.5,
            "mesh_elements": 45000,
        },
    )

    return bridge


@pytest.fixture
def workflow_params() -> DesignWorkflowParams:
    return DesignWorkflowParams(
        work_product_id=uuid4(),
        session_id=uuid4(),
        branch="main",
        shape_type="bracket",
        dimensions={"width": 100.0, "height": 50.0, "thickness": 10.0},
        material="aluminum_6061",
        element_size=1.0,
        mesh_algorithm="netgen",
        output_format="inp",
        load_case="gravity",
        stress_constraints=[
            {"max_von_mises_mpa": 250.0, "safety_factor": 1.5},
        ],
    )


# ---------------------------------------------------------------------------
# Workflow class tests
# ---------------------------------------------------------------------------


class TestMechanicalDesignWorkflow:
    """Tests for the MechanicalDesignWorkflow class."""

    async def test_happy_path_full_pipeline(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Full pipeline succeeds when all steps pass."""
        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is True
        assert len(result.steps) == 3
        assert result.steps[0].step_name == "generate_cad"
        assert result.steps[0].success is True
        assert result.steps[1].step_name == "generate_mesh"
        assert result.steps[1].success is True
        assert result.steps[2].step_name == "validate_stress"
        assert result.steps[2].success is True
        assert result.total_duration_ms > 0
        assert "PASSED" in result.summary
        assert len(result.recommendations) == 0

    async def test_stress_validation_failure_with_recommendations(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Pipeline should fail with recommendations when stress exceeds limits."""
        # Set tight stress constraints that will fail
        # bracket_body=80 MPa, allowable = 60/1.5 = 40 MPa => fail
        workflow_params.stress_constraints = [
            {"max_von_mises_mpa": 60.0, "safety_factor": 1.5},
        ]

        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is False
        assert len(result.steps) == 3
        assert result.steps[0].success is True  # CAD ok
        assert result.steps[1].success is True  # Mesh ok
        assert result.steps[2].success is False  # Stress fails
        assert len(result.recommendations) > 0
        has_fix = any(
            "wall thickness" in r or "stronger material" in r for r in result.recommendations
        )
        assert has_fix
        assert "FAILED" in result.summary

    async def test_cad_generation_failure_stops_pipeline(
        self,
        mock_twin: AsyncMock,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Pipeline stops at CAD step if FreeCAD tool is not available."""
        # Bridge with no CAD tool registered
        bridge = InMemoryMcpBridge()
        bridge.register_tool("calculix.run_fea", "stress_analysis")

        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is False
        assert len(result.steps) == 1  # Only CAD step attempted
        assert result.steps[0].step_name == "generate_cad"
        assert result.steps[0].success is False
        assert len(result.recommendations) > 0

    async def test_mesh_generation_failure_stops_pipeline(
        self,
        mock_twin: AsyncMock,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Pipeline stops at mesh step if meshing fails."""
        # Bridge with CAD tool but broken mesh tool
        bridge = InMemoryMcpBridge()
        bridge.register_tool("freecad.create_parametric", "cad_generation")
        bridge.register_tool_response(
            "freecad.create_parametric",
            {
                "cad_file": "output/bracket.step",
                "volume_mm3": 12500.0,
                "surface_area_mm2": 5400.0,
                "bounding_box": {},
                "parameters_used": {"width": 100.0},
            },
        )
        # No mesh tool response -> will fail

        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is False
        assert len(result.steps) == 2  # CAD + Mesh
        assert result.steps[0].success is True
        assert result.steps[1].success is False
        assert any("Mesh" in r or "mesh" in r for r in result.recommendations)

    async def test_fea_solver_failure(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Pipeline fails gracefully when FEA solver errors out."""
        # Remove FEA tool response so it raises an error
        bridge = InMemoryMcpBridge()
        bridge.register_tool("freecad.create_parametric", "cad_generation")
        bridge.register_tool_response(
            "freecad.create_parametric",
            {
                "cad_file": "output/bracket.step",
                "volume_mm3": 12500.0,
                "surface_area_mm2": 5400.0,
                "bounding_box": {},
                "parameters_used": {"width": 100.0},
            },
        )
        bridge.register_tool("freecad.generate_mesh", "meshing")
        bridge.register_tool_response(
            "freecad.generate_mesh",
            {
                "mesh_file": "output/bracket.inp",
                "num_nodes": 12000,
                "num_elements": 45000,
                "element_types": ["C3D10"],
                "quality_metrics": {
                    "min_angle": 25.0,
                    "max_aspect_ratio": 3.5,
                    "avg_quality": 0.85,
                    "jacobian_ratio": 0.92,
                },
            },
        )
        # No FEA response -> invoke will raise

        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is False
        assert len(result.steps) == 3
        assert result.steps[2].step_name == "validate_stress"
        assert result.steps[2].success is False
        assert any("FEA solver failed" in e for e in result.steps[2].errors)

    async def test_work_products_created_in_twin(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Twin writeback creates CAD_MODEL, MESH, and updates with FEA_RESULT."""
        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is True
        # writeback_cad creates a work product
        assert mock_twin.create_work_product.call_count >= 2  # CAD + Mesh
        # writeback_stress updates an existing work product
        assert mock_twin.update_work_product.call_count >= 1

    async def test_files_saved_to_storage(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """Generated files are persisted via the storage service."""
        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is True
        # CAD and mesh steps should save files
        assert result.steps[0].file_path != ""
        assert result.steps[1].file_path != ""

        # Verify files exist on disk
        files = storage.list_files(str(workflow_params.session_id))
        assert len(files) >= 2

    async def test_no_stress_constraints_passes_vacuously(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
        workflow_params: DesignWorkflowParams,
    ) -> None:
        """No stress constraints means validation passes vacuously."""
        workflow_params.stress_constraints = []

        workflow = MechanicalDesignWorkflow(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        result = await workflow.run(workflow_params)

        assert result.success is True
        assert result.steps[2].step_name == "validate_stress"
        assert result.steps[2].success is True


# ---------------------------------------------------------------------------
# Agent integration tests
# ---------------------------------------------------------------------------


class TestDesignWorkflowViaAgent:
    """Tests for the design_workflow task type through MechanicalAgent."""

    async def test_agent_runs_design_workflow(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
    ) -> None:
        """Agent dispatches design_workflow task to the workflow pipeline."""
        agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        request = TaskRequest(
            task_type="design_workflow",
            work_product_id=uuid4(),
            parameters={
                "shape_type": "bracket",
                "dimensions": {"width": 100.0, "height": 50.0, "thickness": 10.0},
                "material": "aluminum_6061",
                "stress_constraints": [
                    {"max_von_mises_mpa": 250.0, "safety_factor": 1.5},
                ],
            },
        )
        result = await agent.run_task(request)

        assert result.task_type == "design_workflow"
        assert result.success is True
        assert len(result.skill_results) == 3
        assert len(result.errors) == 0

    async def test_agent_missing_storage(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
    ) -> None:
        """Agent returns error when storage is not configured."""
        agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge)
        request = TaskRequest(
            task_type="design_workflow",
            work_product_id=uuid4(),
            parameters={
                "shape_type": "bracket",
                "dimensions": {"width": 100.0, "height": 50.0, "thickness": 10.0},
            },
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("FileStorageService" in e for e in result.errors)

    async def test_agent_missing_shape_type(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
    ) -> None:
        """Agent returns error when shape_type parameter is missing."""
        agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        request = TaskRequest(
            task_type="design_workflow",
            work_product_id=uuid4(),
            parameters={
                "dimensions": {"width": 100.0},
            },
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("shape_type" in e for e in result.errors)

    async def test_agent_missing_dimensions(
        self,
        mock_twin: AsyncMock,
        mcp_bridge: InMemoryMcpBridge,
        storage: FileStorageService,
    ) -> None:
        """Agent returns error when dimensions parameter is missing."""
        agent = MechanicalAgent(twin=mock_twin, mcp=mcp_bridge, storage=storage)
        request = TaskRequest(
            task_type="design_workflow",
            work_product_id=uuid4(),
            parameters={
                "shape_type": "bracket",
            },
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("dimensions" in e for e in result.errors)

    async def test_agent_design_workflow_in_supported_tasks(self) -> None:
        """design_workflow is in the SUPPORTED_TASKS set."""
        assert "design_workflow" in MechanicalAgent.SUPPORTED_TASKS


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestWorkflowModels:
    """Tests for workflow Pydantic models."""

    def test_step_result_defaults(self) -> None:
        step = StepResult(step_name="generate_cad", success=True)
        assert step.skill_output == {}
        assert step.work_product_id == ""
        assert step.file_path == ""
        assert step.errors == []
        assert step.duration_ms == 0.0

    def test_workflow_result_defaults(self) -> None:
        result = WorkflowResult(success=True)
        assert result.steps == []
        assert result.recommendations == []
        assert result.summary == ""
        assert result.total_duration_ms == 0.0

    def test_design_workflow_params_defaults(self) -> None:
        params = DesignWorkflowParams(
            work_product_id=uuid4(),
            session_id=uuid4(),
        )
        assert params.branch == "main"
        assert params.shape_type == "bracket"
        assert params.material == "aluminum_6061"
        assert params.element_size == 1.0
        assert params.mesh_algorithm == "netgen"
        assert params.output_format == "inp"
        assert params.load_case == "default"
        assert params.stress_constraints == []
