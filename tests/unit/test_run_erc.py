"""Tests for the run_erc skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.electronics.skills.run_erc.handler import RunErcHandler
from domain_agents.electronics.skills.run_erc.schema import (
    ErcViolation,
    RunErcInput,
    RunErcOutput,
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
def sample_input() -> RunErcInput:
    return RunErcInput(
        artifact_id=str(uuid4()),
        schematic_file="eda/kicad/main.kicad_sch",
        severity_filter="all",
    )


def _make_erc_response(
    violations: list[dict] | None = None,
    passed: bool = True,
) -> dict:
    """Build a mock ERC tool response."""
    viols = violations or []
    return {
        "schematic_file": "eda/kicad/main.kicad_sch",
        "total_violations": len(viols),
        "errors": sum(1 for v in viols if v.get("severity") == "error"),
        "warnings": sum(1 for v in viols if v.get("severity") == "warning"),
        "violations": viols,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# TestErcSchemas
# ---------------------------------------------------------------------------


class TestErcSchemas:
    def test_valid_input(self) -> None:
        inp = RunErcInput(
            artifact_id=str(uuid4()),
            schematic_file="eda/kicad/test.kicad_sch",
            severity_filter="all",
        )
        assert inp.schematic_file == "eda/kicad/test.kicad_sch"
        assert inp.severity_filter == "all"

    def test_input_default_severity_filter(self) -> None:
        inp = RunErcInput(
            artifact_id=str(uuid4()),
            schematic_file="eda/kicad/test.kicad_sch",
        )
        assert inp.severity_filter == "all"

    def test_input_requires_schematic_file(self) -> None:
        with pytest.raises(ValidationError):
            RunErcInput(
                artifact_id=str(uuid4()),
                schematic_file="",
            )

    def test_violation_model(self) -> None:
        v = ErcViolation(
            rule_id="ERC001",
            severity="error",
            message="Pin unconnected",
            sheet="main",
            component="U1",
            pin="VCC",
            location="(100, 200)",
        )
        assert v.rule_id == "ERC001"
        assert v.severity == "error"

    def test_violation_defaults(self) -> None:
        v = ErcViolation(
            rule_id="ERC002",
            severity="warning",
            message="Unused net",
        )
        assert v.sheet == ""
        assert v.component == ""
        assert v.pin == ""
        assert v.location == ""

    def test_output_model(self) -> None:
        aid = str(uuid4())
        output = RunErcOutput(
            artifact_id=aid,
            schematic_file="eda/kicad/main.kicad_sch",
            violations=[],
            total_violations=0,
            total_errors=0,
            total_warnings=0,
            passed=True,
            summary="ERC PASSED",
        )
        assert output.passed is True
        assert output.total_violations == 0

    def test_output_non_negative_counts(self) -> None:
        with pytest.raises(ValidationError):
            RunErcOutput(
                artifact_id=str(uuid4()),
                schematic_file="test.kicad_sch",
                total_violations=-1,
                total_errors=0,
                total_warnings=0,
                passed=True,
            )


# ---------------------------------------------------------------------------
# TestRunErcHandler
# ---------------------------------------------------------------------------


class TestRunErcHandler:
    async def test_erc_passes_no_violations(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """ERC with no violations should pass."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc", _make_erc_response(violations=[], passed=True)
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is True
        assert output.total_violations == 0
        assert output.total_errors == 0
        assert output.total_warnings == 0
        assert len(output.violations) == 0
        assert "PASSED" in output.summary

    async def test_erc_fails_with_errors(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """ERC with error-severity violations should fail."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {
                        "rule_id": "ERC001",
                        "severity": "error",
                        "message": "Pin U1:VCC unconnected",
                        "component": "U1",
                        "pin": "VCC",
                    },
                ],
                passed=False,
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is False
        assert output.total_errors == 1
        assert output.total_violations == 1
        assert "FAILED" in output.summary

    async def test_erc_passes_with_warnings_only(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """ERC with only warnings (no errors) should pass."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {
                        "rule_id": "ERC010",
                        "severity": "warning",
                        "message": "Unused net GND_AUX",
                    },
                    {
                        "rule_id": "ERC011",
                        "severity": "warning",
                        "message": "Power pin not driven",
                    },
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is True
        assert output.total_errors == 0
        assert output.total_warnings == 2
        assert output.total_violations == 2

    async def test_mixed_errors_and_warnings(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """ERC with both errors and warnings should fail (errors present)."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {"rule_id": "ERC001", "severity": "error", "message": "Err 1"},
                    {"rule_id": "ERC002", "severity": "warning", "message": "Warn 1"},
                    {"rule_id": "ERC003", "severity": "error", "message": "Err 2"},
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is False
        assert output.total_errors == 2
        assert output.total_warnings == 1
        assert output.total_violations == 3

    async def test_severity_filter_errors_only(
        self, mock_context: SkillContext
    ) -> None:
        """Severity filter 'error' should exclude warnings."""
        inp = RunErcInput(
            artifact_id=str(uuid4()),
            schematic_file="eda/kicad/main.kicad_sch",
            severity_filter="error",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {"rule_id": "ERC001", "severity": "error", "message": "Err 1"},
                    {"rule_id": "ERC002", "severity": "warning", "message": "Warn 1"},
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(inp)

        # Only errors should be included
        assert output.total_violations == 1
        assert output.total_errors == 1
        assert output.total_warnings == 0
        assert output.violations[0].severity == "error"

    async def test_severity_filter_warnings_only(
        self, mock_context: SkillContext
    ) -> None:
        """Severity filter 'warning' should exclude errors."""
        inp = RunErcInput(
            artifact_id=str(uuid4()),
            schematic_file="eda/kicad/main.kicad_sch",
            severity_filter="warning",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {"rule_id": "ERC001", "severity": "error", "message": "Err 1"},
                    {"rule_id": "ERC002", "severity": "warning", "message": "Warn 1"},
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(inp)

        assert output.total_violations == 1
        assert output.total_errors == 0
        assert output.total_warnings == 1
        assert output.violations[0].severity == "warning"

    async def test_violation_fields_parsed(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Violation fields should be correctly parsed from raw data."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {
                        "rule_id": "ERC005",
                        "severity": "error",
                        "message": "Conflicting net labels",
                        "sheet": "power_supply",
                        "component": "U3",
                        "pin": "GND",
                        "location": "(150, 300)",
                    },
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        v = output.violations[0]
        assert v.rule_id == "ERC005"
        assert v.severity == "error"
        assert v.message == "Conflicting net labels"
        assert v.sheet == "power_supply"
        assert v.component == "U3"
        assert v.pin == "GND"
        assert v.location == "(150, 300)"

    async def test_missing_violation_fields_default(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Missing optional violation fields should get default values."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {"severity": "error"},  # Minimal violation
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        v = output.violations[0]
        assert v.rule_id == "ERC_UNKNOWN"
        assert v.message == "Unknown ERC violation"
        assert v.sheet == ""
        assert v.component == ""

    async def test_schematic_file_in_output(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Output should include the schematic file path."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc", _make_erc_response()
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.schematic_file == "eda/kicad/main.kicad_sch"

    async def test_artifact_id_in_output(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Output should include the artifact ID."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc", _make_erc_response()
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.artifact_id == sample_input.artifact_id

    async def test_summary_no_violations(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Summary for zero violations should say 'No violations found'."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc", _make_erc_response()
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert "No violations found" in output.summary

    async def test_summary_with_violations(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Summary with violations should mention counts."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {"rule_id": "ERC001", "severity": "error", "message": "Err"},
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert "1 violation(s)" in output.summary
        assert "1 error(s)" in output.summary
        assert "0 warning(s)" in output.summary


# ---------------------------------------------------------------------------
# TestPreconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    async def test_precondition_missing_artifact(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Missing artifact should fail preconditions."""
        mock_context.twin.get_artifact.return_value = None
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")

        handler = RunErcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not found in Twin" in errors[0]

    async def test_precondition_tool_unavailable(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Unavailable kicad.run_erc tool should fail preconditions."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        # Don't register the tool => not available

        handler = RunErcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not available" in errors[0]

    async def test_precondition_both_missing(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Both missing artifact and tool should produce two errors."""
        mock_context.twin.get_artifact.return_value = None
        # Don't register the tool

        handler = RunErcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 2

    async def test_preconditions_pass(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """All preconditions met should return empty errors."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")

        handler = RunErcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert errors == []


# ---------------------------------------------------------------------------
# TestSkillRunPipeline
# ---------------------------------------------------------------------------


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """Full run() pipeline should return SkillResult with success=True."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc", _make_erc_response()
        )

        handler = RunErcHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, RunErcOutput)
        assert result.data.passed is True
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """run() should return failure when preconditions are not met."""
        mock_context.twin.get_artifact.return_value = None
        # Don't register the tool

        handler = RunErcHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is False
        assert len(result.errors) >= 1
        assert result.data is None

    async def test_full_run_pipeline_with_violations(
        self, mock_context: SkillContext, sample_input: RunErcInput
    ) -> None:
        """run() with violations should succeed (SkillResult) but output.passed may be False."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_erc", "erc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_erc",
            _make_erc_response(
                violations=[
                    {"rule_id": "ERC001", "severity": "error", "message": "Err"},
                ],
            ),
        )

        handler = RunErcHandler(mock_context)
        result = await handler.run(sample_input)

        # Skill execution succeeded (no crash)
        assert result.success is True
        # But the ERC itself failed
        assert result.data.passed is False
        assert result.data.total_errors == 1
