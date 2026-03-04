"""Tests for the check_tolerance skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest
from domain_agents.mechanical.skills.check_tolerance.handler import (
    CheckToleranceHandler,
)
from domain_agents.mechanical.skills.check_tolerance.schema import (
    CheckToleranceInput,
    CheckToleranceOutput,
    ManufacturingProcess,
    ToleranceResult,
    ToleranceSpec,
    ToleranceViolation,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def cnc_process() -> ManufacturingProcess:
    return ManufacturingProcess(
        process_type="cnc_milling",
        achievable_tolerance=0.05,
        surface_finish_ra=1.6,
        min_feature_size=0.5,
        max_aspect_ratio=10.0,
    )


@pytest.fixture()
def fdm_process() -> ManufacturingProcess:
    return ManufacturingProcess(
        process_type="3d_printing_fdm",
        achievable_tolerance=0.3,
        surface_finish_ra=12.0,
        min_feature_size=1.0,
        max_aspect_ratio=5.0,
    )


@pytest.fixture()
def passing_input(cnc_process: ManufacturingProcess) -> CheckToleranceInput:
    """Input where all tolerances should pass with CNC milling."""
    return CheckToleranceInput(
        artifact_id="artifact-123",
        tolerances=[
            ToleranceSpec(
                dimension_id="D1",
                feature_name="bore_diameter",
                nominal_value=25.0,
                upper_tolerance=0.1,
                lower_tolerance=-0.1,
                tolerance_grade="IT8",
            ),
            ToleranceSpec(
                dimension_id="D2",
                feature_name="shaft_length",
                nominal_value=100.0,
                upper_tolerance=0.15,
                lower_tolerance=-0.15,
            ),
        ],
        manufacturing_process=cnc_process,
        material="aluminum_6061",
    )


@pytest.fixture()
def tight_input(cnc_process: ManufacturingProcess) -> CheckToleranceInput:
    """Input where tolerance is tighter than process capability."""
    return CheckToleranceInput(
        artifact_id="artifact-456",
        tolerances=[
            ToleranceSpec(
                dimension_id="D1",
                feature_name="precision_bore",
                nominal_value=10.0,
                upper_tolerance=0.01,
                lower_tolerance=-0.01,
            ),
        ],
        manufacturing_process=cnc_process,
    )


# ---------------------------------------------------------------------------
# TestToleranceModels
# ---------------------------------------------------------------------------


class TestToleranceModels:
    def test_tolerance_spec_defaults(self) -> None:
        spec = ToleranceSpec(
            dimension_id="D1",
            feature_name="bore",
            nominal_value=10.0,
            upper_tolerance=0.05,
            lower_tolerance=-0.05,
        )
        assert spec.tolerance_grade == ""
        assert spec.tolerance_type == "bilateral"

    def test_manufacturing_process_model(self) -> None:
        proc = ManufacturingProcess(
            process_type="cnc_milling",
            achievable_tolerance=0.05,
        )
        assert proc.surface_finish_ra == 0.0
        assert proc.min_feature_size == 0.0
        assert proc.max_aspect_ratio == 0.0

    def test_check_tolerance_input_model(
        self, cnc_process: ManufacturingProcess
    ) -> None:
        inp = CheckToleranceInput(
            artifact_id="art-1",
            tolerances=[],
            manufacturing_process=cnc_process,
        )
        assert inp.material == "aluminum_6061"
        assert inp.check_stack_up is False
        assert inp.tolerances == []

    def test_check_tolerance_output_model(self) -> None:
        output = CheckToleranceOutput(
            artifact_id="art-1",
            process_type="cnc_milling",
            total_dimensions_checked=1,
            passed=1,
            warnings=0,
            failures=0,
            overall_status="pass",
            results=[
                ToleranceResult(
                    dimension_id="D1",
                    feature_name="bore",
                    nominal_value=10.0,
                    tolerance_range=0.1,
                    status="pass",
                    capability_index=2.0,
                    message="OK",
                )
            ],
            violations=[],
            summary="All good.",
        )
        assert output.overall_status == "pass"
        assert output.total_dimensions_checked == 1

    def test_tolerance_violation_model(self) -> None:
        v = ToleranceViolation(
            dimension_id="D1",
            feature_name="bore",
            violation_type="too_tight",
            severity="error",
            specified_tolerance=0.02,
            achievable_tolerance=0.05,
            message="Too tight",
            recommendation="Widen tolerance",
        )
        assert v.severity == "error"
        assert v.violation_type == "too_tight"

    def test_nominal_value_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ToleranceSpec(
                dimension_id="D1",
                feature_name="bore",
                nominal_value=0.0,
                upper_tolerance=0.05,
                lower_tolerance=-0.05,
            )

    def test_achievable_tolerance_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ManufacturingProcess(
                process_type="cnc_milling",
                achievable_tolerance=0.0,
            )


# ---------------------------------------------------------------------------
# TestCheckToleranceHandler
# ---------------------------------------------------------------------------


class TestCheckToleranceHandler:
    async def test_all_tolerances_pass(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """All tolerances well within CNC capability should pass."""
        mock_context.twin.get_artifact.return_value = {
            "id": passing_input.artifact_id
        }
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(passing_input)

        assert output.overall_status == "pass"
        assert output.passed == 2
        assert output.warnings == 0
        assert output.failures == 0
        assert all(r.status == "pass" for r in output.results)

    async def test_tolerance_too_tight_fails(
        self,
        mock_context: SkillContext,
        tight_input: CheckToleranceInput,
    ) -> None:
        """Tolerance tighter than achievable should fail."""
        mock_context.twin.get_artifact.return_value = {
            "id": tight_input.artifact_id
        }
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(tight_input)

        assert output.failures >= 1
        assert output.overall_status == "fail"
        # Should have a "too_tight" violation
        too_tight = [
            v for v in output.violations if v.violation_type == "too_tight"
        ]
        assert len(too_tight) >= 1
        assert too_tight[0].severity == "error"

    async def test_tolerance_marginal_warning(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Tolerance with Cp between 1.0 and 1.33 should get warning."""
        # CNC achievable = 0.05mm, process_sigma = 0.05/3 = 0.01667
        # For Cp = 1.2 (marginal): tol_range = 1.2 * 6 * 0.01667 = 0.12
        inp = CheckToleranceInput(
            artifact_id="art-marginal",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="marginal_bore",
                    nominal_value=20.0,
                    upper_tolerance=0.06,
                    lower_tolerance=-0.06,
                ),
            ],
            manufacturing_process=cnc_process,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        # tol_range = 0.12, sigma = 0.01667, Cp = 0.12 / (6*0.01667) = 1.2
        assert output.warnings == 1
        assert output.overall_status == "marginal"
        assert output.results[0].status == "warning"

    async def test_below_min_feature_size(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Feature smaller than min_feature_size should generate violation."""
        inp = CheckToleranceInput(
            artifact_id="art-small",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="tiny_hole",
                    nominal_value=0.3,  # Below CNC min_feature_size of 0.5
                    upper_tolerance=0.1,
                    lower_tolerance=-0.1,
                ),
            ],
            manufacturing_process=cnc_process,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        below_min = [
            v for v in output.violations if v.violation_type == "below_min_feature"
        ]
        assert len(below_min) == 1
        assert below_min[0].severity == "error"

    async def test_mixed_pass_and_fail(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Mix of passing and failing tolerances."""
        inp = CheckToleranceInput(
            artifact_id="art-mixed",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="good_bore",
                    nominal_value=25.0,
                    upper_tolerance=0.1,
                    lower_tolerance=-0.1,
                ),
                ToleranceSpec(
                    dimension_id="D2",
                    feature_name="tight_slot",
                    nominal_value=10.0,
                    upper_tolerance=0.005,
                    lower_tolerance=-0.005,
                ),
            ],
            manufacturing_process=cnc_process,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        assert output.passed >= 1
        assert output.failures >= 1
        assert output.overall_status == "fail"
        assert output.total_dimensions_checked == 2

    async def test_capability_index_calculation(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Verify Cp calculation: Cp = tol_range / (6 * sigma)."""
        # achievable = 0.05, sigma = 0.05/3
        # tol_range = 0.2, Cp = 0.2 / (6 * 0.05/3) = 0.2 / 0.1 = 2.0
        inp = CheckToleranceInput(
            artifact_id="art-cp",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="bore",
                    nominal_value=10.0,
                    upper_tolerance=0.1,
                    lower_tolerance=-0.1,
                ),
            ],
            manufacturing_process=cnc_process,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        assert output.results[0].capability_index == pytest.approx(2.0, abs=0.01)

    async def test_overall_status_pass(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """All passing dimensions should yield 'pass' overall."""
        mock_context.twin.get_artifact.return_value = {
            "id": passing_input.artifact_id
        }
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(passing_input)

        assert output.overall_status == "pass"

    async def test_overall_status_marginal(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Warnings but no failures should yield 'marginal' overall."""
        inp = CheckToleranceInput(
            artifact_id="art-marg",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="marginal_bore",
                    nominal_value=20.0,
                    upper_tolerance=0.06,
                    lower_tolerance=-0.06,
                ),
            ],
            manufacturing_process=cnc_process,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        assert output.overall_status == "marginal"

    async def test_overall_status_fail(
        self,
        mock_context: SkillContext,
        tight_input: CheckToleranceInput,
    ) -> None:
        """Failures should yield 'fail' overall."""
        mock_context.twin.get_artifact.return_value = {
            "id": tight_input.artifact_id
        }
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(tight_input)

        assert output.overall_status == "fail"

    async def test_violations_populated_on_failure(
        self,
        mock_context: SkillContext,
        tight_input: CheckToleranceInput,
    ) -> None:
        """Violations list should be populated when there are failures."""
        mock_context.twin.get_artifact.return_value = {
            "id": tight_input.artifact_id
        }
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(tight_input)

        assert len(output.violations) >= 1
        assert all(v.message != "" for v in output.violations)
        assert all(v.recommendation != "" for v in output.violations)

    async def test_summary_generated(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """Summary string should be generated."""
        mock_context.twin.get_artifact.return_value = {
            "id": passing_input.artifact_id
        }
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(passing_input)

        assert output.summary != ""
        assert "cnc_milling" in output.summary
        assert "PASS" in output.summary

    async def test_empty_tolerances_list(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Empty tolerances list should produce pass with zero dimensions."""
        inp = CheckToleranceInput(
            artifact_id="art-empty",
            tolerances=[],
            manufacturing_process=cnc_process,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        assert output.total_dimensions_checked == 0
        assert output.passed == 0
        assert output.warnings == 0
        assert output.failures == 0
        assert output.overall_status == "pass"
        assert output.results == []
        assert output.violations == []


# ---------------------------------------------------------------------------
# TestCheckTolerancePreconditions
# ---------------------------------------------------------------------------


class TestCheckTolerancePreconditions:
    async def test_precondition_missing_artifact(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """Missing artifact should fail preconditions."""
        mock_context.twin.get_artifact.return_value = None

        handler = CheckToleranceHandler(mock_context)
        errors = await handler.validate_preconditions(passing_input)

        assert len(errors) == 1
        assert "not found in Twin" in errors[0]

    async def test_preconditions_pass(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """Existing artifact should pass preconditions."""
        mock_context.twin.get_artifact.return_value = {
            "id": passing_input.artifact_id
        }

        handler = CheckToleranceHandler(mock_context)
        errors = await handler.validate_preconditions(passing_input)

        assert errors == []


# ---------------------------------------------------------------------------
# TestCheckToleranceRunPipeline
# ---------------------------------------------------------------------------


class TestCheckToleranceRunPipeline:
    async def test_full_run_pipeline_success(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """Full run() pipeline should return SkillResult with success=True."""
        mock_context.twin.get_artifact.return_value = {
            "id": passing_input.artifact_id
        }

        handler = CheckToleranceHandler(mock_context)
        result = await handler.run(passing_input)

        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, CheckToleranceOutput)
        assert result.data.overall_status == "pass"
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_full_run_pipeline_precondition_failure(
        self,
        mock_context: SkillContext,
        passing_input: CheckToleranceInput,
    ) -> None:
        """run() should return failure when preconditions not met."""
        mock_context.twin.get_artifact.return_value = None

        handler = CheckToleranceHandler(mock_context)
        result = await handler.run(passing_input)

        assert result.success is False
        assert len(result.errors) >= 1
        assert result.data is None


# ---------------------------------------------------------------------------
# TestCheckToleranceStackUp
# ---------------------------------------------------------------------------


class TestCheckToleranceStackUp:
    async def test_stack_up_analysis(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Stack-up check with identical tolerances should trigger warning."""
        # 3 identical tolerances: each range = 0.2
        # RSS = sqrt(3 * 0.2^2) = sqrt(0.12) = 0.3464
        # Worst case = 0.6
        # 0.3464 / 0.6 = 57.7% < 75% => no warning
        # Need tolerances closer to worst-case to trigger
        inp = CheckToleranceInput(
            artifact_id="art-stack",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="dim_a",
                    nominal_value=10.0,
                    upper_tolerance=0.1,
                    lower_tolerance=-0.1,
                ),
                ToleranceSpec(
                    dimension_id="D2",
                    feature_name="dim_b",
                    nominal_value=10.0,
                    upper_tolerance=0.1,
                    lower_tolerance=-0.1,
                ),
            ],
            manufacturing_process=cnc_process,
            check_stack_up=True,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        # 2 tolerances: RSS = sqrt(0.04 + 0.04) = 0.2828, WC = 0.4
        # 0.2828 / 0.4 = 70.7% < 75% => no stack-up warning
        stack_violations = [
            v for v in output.violations if v.violation_type == "stack_up_exceeded"
        ]
        assert len(stack_violations) == 0

    async def test_stack_up_triggers_warning(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Stack-up with single dominant tolerance triggers warning (RSS >= 75% WC)."""
        # 2 tolerances: one large (0.5) and one tiny (0.01)
        # RSS = sqrt(0.5^2 + 0.01^2) = sqrt(0.2501) = 0.5001
        # WC = 0.51
        # 0.5001 / 0.51 = 98% > 75% => warning triggered
        inp = CheckToleranceInput(
            artifact_id="art-stack-warn",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="big_dim",
                    nominal_value=50.0,
                    upper_tolerance=0.25,
                    lower_tolerance=-0.25,
                ),
                ToleranceSpec(
                    dimension_id="D2",
                    feature_name="tiny_dim",
                    nominal_value=5.0,
                    upper_tolerance=0.005,
                    lower_tolerance=-0.005,
                ),
            ],
            manufacturing_process=cnc_process,
            check_stack_up=True,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        stack_violations = [
            v for v in output.violations if v.violation_type == "stack_up_exceeded"
        ]
        assert len(stack_violations) == 1
        assert stack_violations[0].dimension_id == "STACK_UP"

    async def test_no_stack_up_when_disabled(
        self,
        mock_context: SkillContext,
        cnc_process: ManufacturingProcess,
    ) -> None:
        """Stack-up check should not run when check_stack_up=False."""
        inp = CheckToleranceInput(
            artifact_id="art-no-stack",
            tolerances=[
                ToleranceSpec(
                    dimension_id="D1",
                    feature_name="big_dim",
                    nominal_value=50.0,
                    upper_tolerance=0.25,
                    lower_tolerance=-0.25,
                ),
                ToleranceSpec(
                    dimension_id="D2",
                    feature_name="tiny_dim",
                    nominal_value=5.0,
                    upper_tolerance=0.005,
                    lower_tolerance=-0.005,
                ),
            ],
            manufacturing_process=cnc_process,
            check_stack_up=False,
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}

        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(inp)

        stack_violations = [
            v for v in output.violations if v.violation_type == "stack_up_exceeded"
        ]
        assert len(stack_violations) == 0


# ---------------------------------------------------------------------------
# TestCheckToleranceWithAgent
# ---------------------------------------------------------------------------


class TestCheckToleranceWithAgent:
    async def test_agent_routes_check_tolerances_task(self) -> None:
        """Agent should route check_tolerances task to the tolerance handler."""
        twin = AsyncMock()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        artifact_id = uuid4()
        twin.get_artifact.return_value = {"id": str(artifact_id)}

        request = TaskRequest(
            task_type="check_tolerances",
            artifact_id=artifact_id,
            parameters={
                "tolerances": [
                    {
                        "dimension_id": "D1",
                        "feature_name": "bore_diameter",
                        "nominal_value": 25.0,
                        "upper_tolerance": 0.1,
                        "lower_tolerance": -0.1,
                    },
                ],
                "manufacturing_process": {
                    "process_type": "cnc_milling",
                    "achievable_tolerance": 0.05,
                },
            },
        )

        result = await agent.run_task(request)

        assert result.task_type == "check_tolerances"
        assert result.success is True
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "check_tolerance"
        assert result.skill_results[0]["overall_status"] == "pass"

    async def test_agent_check_tolerances_missing_process(self) -> None:
        """Agent should return error when manufacturing_process is missing."""
        twin = AsyncMock()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        artifact_id = uuid4()
        twin.get_artifact.return_value = {"id": str(artifact_id)}

        request = TaskRequest(
            task_type="check_tolerances",
            artifact_id=artifact_id,
            parameters={
                "tolerances": [
                    {
                        "dimension_id": "D1",
                        "feature_name": "bore",
                        "nominal_value": 10.0,
                        "upper_tolerance": 0.05,
                        "lower_tolerance": -0.05,
                    },
                ],
            },
        )

        result = await agent.run_task(request)

        assert result.success is False
        assert any("manufacturing_process" in e for e in result.errors)

    async def test_agent_check_tolerances_artifact_not_found(self) -> None:
        """Agent should return error when artifact does not exist."""
        twin = AsyncMock()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)

        artifact_id = uuid4()
        twin.get_artifact.return_value = None

        request = TaskRequest(
            task_type="check_tolerances",
            artifact_id=artifact_id,
            parameters={
                "tolerances": [],
                "manufacturing_process": {
                    "process_type": "cnc_milling",
                    "achievable_tolerance": 0.05,
                },
            },
        )

        result = await agent.run_task(request)

        assert result.success is False
        assert any("not found" in e for e in result.errors)
