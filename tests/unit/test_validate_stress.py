"""Tests for the validate_stress skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.mechanical.skills.validate_stress.handler import ValidateStressHandler
from domain_agents.mechanical.skills.validate_stress.schema import (
    StressConstraint,
    StressResult,
    ValidateStressInput,
    ValidateStressOutput,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    ctx.metrics_collector = None
    ctx.domain = "unknown"
    return ctx


@pytest.fixture()
def sample_input() -> ValidateStressInput:
    return ValidateStressInput(
        work_product_id=uuid4(),
        mesh_file_path="/project/mesh/bracket.inp",
        load_case="static_load_1",
        constraints=[
            StressConstraint(
                max_von_mises_mpa=250.0,
                safety_factor=1.5,
                material="aluminum_6061",
            )
        ],
    )


# ---------------------------------------------------------------------------
# TestStressSchemas
# ---------------------------------------------------------------------------


class TestStressSchemas:
    def test_valid_input(self) -> None:
        inp = ValidateStressInput(
            work_product_id=uuid4(),
            mesh_file_path="/mesh/test.inp",
            load_case="load_1",
            constraints=[
                StressConstraint(
                    max_von_mises_mpa=200.0,
                    safety_factor=2.0,
                    material="steel_304",
                )
            ],
        )
        assert inp.mesh_file_path == "/mesh/test.inp"
        assert inp.constraints[0].safety_factor == 2.0

    def test_input_requires_constraints(self) -> None:
        with pytest.raises(ValidationError):
            ValidateStressInput(
                work_product_id=uuid4(),
                mesh_file_path="/mesh/test.inp",
                load_case="load_1",
                constraints=[],
            )

    def test_constraint_safety_factor_minimum(self) -> None:
        with pytest.raises(ValidationError):
            StressConstraint(
                max_von_mises_mpa=200.0,
                safety_factor=0.5,
                material="aluminum",
            )

    def test_output_model(self) -> None:
        aid = uuid4()
        output = ValidateStressOutput(
            work_product_id=aid,
            overall_passed=True,
            results=[
                StressResult(
                    region="body",
                    max_von_mises_mpa=100.0,
                    allowable_mpa=166.67,
                    safety_factor_achieved=2.5,
                    passed=True,
                )
            ],
            max_stress_mpa=100.0,
            critical_region="body",
        )
        assert output.overall_passed is True
        assert output.work_product_id == aid
        assert len(output.results) == 1
        assert output.solver_time_seconds == 0.0
        assert output.mesh_elements == 0


# ---------------------------------------------------------------------------
# TestValidateStressHandler
# ---------------------------------------------------------------------------


class TestValidateStressHandler:
    async def test_stress_passes(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Stress below allowable should pass."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {"bracket_body": 100.0, "bracket_mount": 50.0},
                "solver_time": 12.5,
                "mesh_elements": 45000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.overall_passed is True
        assert output.max_stress_mpa == 100.0
        assert output.critical_region == "bracket_body"
        assert all(r.passed for r in output.results)

    async def test_stress_fails(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Stress above allowable should fail."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        # allowable = 250.0 / 1.5 = 166.67, so 200.0 > 166.67 -> fail
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {"bracket_body": 200.0},
                "solver_time": 10.0,
                "mesh_elements": 30000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.overall_passed is False
        assert output.max_stress_mpa == 200.0
        assert any(not r.passed for r in output.results)

    async def test_multiple_regions(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Multiple regions should each get a result entry."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {
                    "region_a": 80.0,
                    "region_b": 120.0,
                    "region_c": 160.0,
                },
                "solver_time": 15.0,
                "mesh_elements": 60000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(sample_input)

        # 1 constraint x 3 regions = 3 results
        assert len(output.results) == 3
        assert output.critical_region == "region_c"
        assert output.max_stress_mpa == 160.0

    async def test_multiple_constraints(self, mock_context: SkillContext) -> None:
        """Multiple constraints should produce results for each constraint x region."""
        inp = ValidateStressInput(
            work_product_id=uuid4(),
            mesh_file_path="/mesh/part.inp",
            load_case="load_2",
            constraints=[
                StressConstraint(
                    max_von_mises_mpa=250.0,
                    safety_factor=1.5,
                    material="aluminum_6061",
                ),
                StressConstraint(
                    max_von_mises_mpa=400.0,
                    safety_factor=2.0,
                    material="steel_304",
                ),
            ],
        )
        mock_context.twin.get_work_product.return_value = {"id": str(inp.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {"body": 150.0},
                "solver_time": 8.0,
                "mesh_elements": 20000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(inp)

        # 2 constraints x 1 region = 2 results
        assert len(output.results) == 2

    async def test_critical_region_identified(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """The critical region should be the one with the highest stress."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {
                    "low_stress": 10.0,
                    "high_stress": 155.0,
                    "mid_stress": 90.0,
                },
                "solver_time": 5.0,
                "mesh_elements": 10000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.critical_region == "high_stress"
        assert output.max_stress_mpa == 155.0

    async def test_solver_metadata_captured(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Solver time and mesh element count should be captured from FEA result."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {"region": 50.0},
                "solver_time": 42.7,
                "mesh_elements": 99000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.solver_time_seconds == 42.7
        assert output.mesh_elements == 99000


# ---------------------------------------------------------------------------
# TestPreconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    async def test_precondition_missing_artifact(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Missing work_product should fail preconditions."""
        mock_context.twin.get_work_product.return_value = None
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")

        handler = ValidateStressHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not found in Twin" in errors[0]

    async def test_precondition_tool_unavailable(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Unavailable CalculiX tool should fail preconditions."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        # Don't register the tool => not available

        handler = ValidateStressHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not available" in errors[0]

    async def test_preconditions_pass(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """All preconditions met should return empty errors."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")

        handler = ValidateStressHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert errors == []


# ---------------------------------------------------------------------------
# TestSkillRunPipeline
# ---------------------------------------------------------------------------


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """Full run() pipeline should return SkillResult with success=True."""
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {"bracket_body": 100.0},
                "solver_time": 5.0,
                "mesh_elements": 25000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, ValidateStressOutput)
        assert result.data.overall_passed is True
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        """run() should return failure when preconditions are not met."""
        mock_context.twin.get_work_product.return_value = None
        # Don't register calculix tool either

        handler = ValidateStressHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is False
        assert len(result.errors) >= 1
        assert result.data is None
