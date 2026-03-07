"""Tests for the run_drc skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.electronics.skills.run_drc.handler import RunDrcHandler
from domain_agents.electronics.skills.run_drc.schema import (
    DrcViolation,
    RunDrcInput,
    RunDrcOutput,
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
def sample_input() -> RunDrcInput:
    return RunDrcInput(
        artifact_id=str(uuid4()),
        pcb_file="eda/kicad/main.kicad_pcb",
        severity_filter="all",
    )


def _make_drc_response(
    violations: list[dict] | None = None,
    passed: bool = True,
) -> dict:
    """Build a mock DRC tool response."""
    viols = violations or []
    return {
        "pcb_file": "eda/kicad/main.kicad_pcb",
        "total_violations": len(viols),
        "errors": sum(1 for v in viols if v.get("severity") == "error"),
        "warnings": sum(1 for v in viols if v.get("severity") == "warning"),
        "violations": viols,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# TestDrcSchemas
# ---------------------------------------------------------------------------


class TestDrcSchemas:
    def test_valid_input(self) -> None:
        inp = RunDrcInput(
            artifact_id=str(uuid4()),
            pcb_file="eda/kicad/test.kicad_pcb",
            severity_filter="all",
        )
        assert inp.pcb_file == "eda/kicad/test.kicad_pcb"
        assert inp.severity_filter == "all"

    def test_input_default_severity_filter(self) -> None:
        inp = RunDrcInput(
            artifact_id=str(uuid4()),
            pcb_file="eda/kicad/test.kicad_pcb",
        )
        assert inp.severity_filter == "all"

    def test_input_requires_pcb_file(self) -> None:
        with pytest.raises(ValidationError):
            RunDrcInput(
                artifact_id=str(uuid4()),
                pcb_file="",
            )

    def test_violation_model(self) -> None:
        v = DrcViolation(
            rule_id="DRC001",
            severity="error",
            message="Clearance violation between pad and track",
            layer="F.Cu",
            location="(50, 75)",
            items=["U1-pad1", "net-GND"],
        )
        assert v.rule_id == "DRC001"
        assert v.severity == "error"
        assert v.layer == "F.Cu"
        assert len(v.items) == 2

    def test_violation_defaults(self) -> None:
        v = DrcViolation(
            rule_id="DRC002",
            severity="warning",
            message="Track width below minimum",
        )
        assert v.layer == ""
        assert v.location == ""
        assert v.items == []

    def test_output_model(self) -> None:
        aid = str(uuid4())
        output = RunDrcOutput(
            artifact_id=aid,
            pcb_file="eda/kicad/main.kicad_pcb",
            violations=[],
            total_violations=0,
            total_errors=0,
            total_warnings=0,
            passed=True,
            summary="DRC PASSED",
        )
        assert output.passed is True
        assert output.total_violations == 0

    def test_output_non_negative_counts(self) -> None:
        with pytest.raises(ValidationError):
            RunDrcOutput(
                artifact_id=str(uuid4()),
                pcb_file="test.kicad_pcb",
                total_violations=-1,
                total_errors=0,
                total_warnings=0,
                passed=True,
            )


# ---------------------------------------------------------------------------
# TestRunDrcHandler
# ---------------------------------------------------------------------------


class TestRunDrcHandler:
    async def test_drc_passes_no_violations(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """DRC with no violations should pass."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc", _make_drc_response(violations=[], passed=True)
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is True
        assert output.total_violations == 0
        assert output.total_errors == 0
        assert output.total_warnings == 0
        assert len(output.violations) == 0
        assert "PASSED" in output.summary

    async def test_drc_fails_with_errors(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """DRC with error-severity violations should fail."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {
                        "rule_id": "DRC001",
                        "severity": "error",
                        "message": "Clearance violation",
                        "layer": "F.Cu",
                        "items": ["U1-pad1", "track-segment-42"],
                    },
                ],
                passed=False,
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is False
        assert output.total_errors == 1
        assert output.total_violations == 1
        assert "FAILED" in output.summary

    async def test_drc_passes_with_warnings_only(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """DRC with only warnings (no errors) should pass."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {
                        "rule_id": "DRC010",
                        "severity": "warning",
                        "message": "Track width near minimum",
                    },
                    {
                        "rule_id": "DRC011",
                        "severity": "warning",
                        "message": "Silkscreen overlaps courtyard",
                    },
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is True
        assert output.total_errors == 0
        assert output.total_warnings == 2
        assert output.total_violations == 2

    async def test_mixed_errors_and_warnings(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """DRC with both errors and warnings should fail (errors present)."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {"rule_id": "DRC001", "severity": "error", "message": "Err 1"},
                    {"rule_id": "DRC002", "severity": "warning", "message": "Warn 1"},
                    {"rule_id": "DRC003", "severity": "error", "message": "Err 2"},
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.passed is False
        assert output.total_errors == 2
        assert output.total_warnings == 1
        assert output.total_violations == 3

    async def test_severity_filter_errors_only(
        self, mock_context: SkillContext
    ) -> None:
        """Severity filter 'error' should exclude warnings."""
        inp = RunDrcInput(
            artifact_id=str(uuid4()),
            pcb_file="eda/kicad/main.kicad_pcb",
            severity_filter="error",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {"rule_id": "DRC001", "severity": "error", "message": "Err 1"},
                    {"rule_id": "DRC002", "severity": "warning", "message": "Warn 1"},
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(inp)

        assert output.total_violations == 1
        assert output.total_errors == 1
        assert output.total_warnings == 0
        assert output.violations[0].severity == "error"

    async def test_severity_filter_warnings_only(
        self, mock_context: SkillContext
    ) -> None:
        """Severity filter 'warning' should exclude errors."""
        inp = RunDrcInput(
            artifact_id=str(uuid4()),
            pcb_file="eda/kicad/main.kicad_pcb",
            severity_filter="warning",
        )
        mock_context.twin.get_artifact.return_value = {"id": inp.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {"rule_id": "DRC001", "severity": "error", "message": "Err 1"},
                    {"rule_id": "DRC002", "severity": "warning", "message": "Warn 1"},
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(inp)

        assert output.total_violations == 1
        assert output.total_errors == 0
        assert output.total_warnings == 1
        assert output.violations[0].severity == "warning"

    async def test_violation_fields_parsed(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Violation fields should be correctly parsed from raw data."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {
                        "rule_id": "DRC005",
                        "severity": "error",
                        "message": "Via annular ring too small",
                        "layer": "B.Cu",
                        "location": "(200, 150)",
                        "items": ["via-1", "net-VCC"],
                    },
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        v = output.violations[0]
        assert v.rule_id == "DRC005"
        assert v.severity == "error"
        assert v.message == "Via annular ring too small"
        assert v.layer == "B.Cu"
        assert v.location == "(200, 150)"
        assert v.items == ["via-1", "net-VCC"]

    async def test_missing_violation_fields_default(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Missing optional violation fields should get default values."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {"severity": "error"},  # Minimal violation
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        v = output.violations[0]
        assert v.rule_id == "DRC_UNKNOWN"
        assert v.message == "Unknown DRC violation"
        assert v.layer == ""
        assert v.location == ""
        assert v.items == []

    async def test_pcb_file_in_output(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Output should include the PCB file path."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc", _make_drc_response()
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.pcb_file == "eda/kicad/main.kicad_pcb"

    async def test_artifact_id_in_output(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Output should include the artifact ID."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc", _make_drc_response()
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.artifact_id == sample_input.artifact_id

    async def test_summary_no_violations(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Summary for zero violations should say 'No violations found'."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc", _make_drc_response()
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert "No violations found" in output.summary

    async def test_summary_with_violations(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Summary with violations should mention counts."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {"rule_id": "DRC001", "severity": "error", "message": "Err"},
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        output = await handler.execute(sample_input)

        assert "1 violation(s)" in output.summary
        assert "1 error(s)" in output.summary
        assert "0 warning(s)" in output.summary


# ---------------------------------------------------------------------------
# TestPreconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    async def test_precondition_missing_artifact(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Missing artifact should fail preconditions."""
        mock_context.twin.get_artifact.return_value = None
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")

        handler = RunDrcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not found in Twin" in errors[0]

    async def test_precondition_tool_unavailable(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Unavailable kicad.run_drc tool should fail preconditions."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        # Don't register the tool => not available

        handler = RunDrcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 1
        assert "not available" in errors[0]

    async def test_precondition_both_missing(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Both missing artifact and tool should produce two errors."""
        mock_context.twin.get_artifact.return_value = None
        # Don't register the tool

        handler = RunDrcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert len(errors) == 2

    async def test_preconditions_pass(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """All preconditions met should return empty errors."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")

        handler = RunDrcHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert errors == []


# ---------------------------------------------------------------------------
# TestSkillRunPipeline
# ---------------------------------------------------------------------------


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """Full run() pipeline should return SkillResult with success=True."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc", _make_drc_response()
        )

        handler = RunDrcHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, RunDrcOutput)
        assert result.data.passed is True
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """run() should return failure when preconditions are not met."""
        mock_context.twin.get_artifact.return_value = None
        # Don't register the tool

        handler = RunDrcHandler(mock_context)
        result = await handler.run(sample_input)

        assert result.success is False
        assert len(result.errors) >= 1
        assert result.data is None

    async def test_full_run_pipeline_with_violations(
        self, mock_context: SkillContext, sample_input: RunDrcInput
    ) -> None:
        """run() with violations should succeed (SkillResult) but output.passed may be False."""
        mock_context.twin.get_artifact.return_value = {"id": sample_input.artifact_id}
        mock_context.mcp.register_tool("kicad.run_drc", "drc_validation")
        mock_context.mcp.register_tool_response(
            "kicad.run_drc",
            _make_drc_response(
                violations=[
                    {"rule_id": "DRC001", "severity": "error", "message": "Err"},
                ],
            ),
        )

        handler = RunDrcHandler(mock_context)
        result = await handler.run(sample_input)

        # Skill execution succeeded (no crash)
        assert result.success is True
        # But the DRC itself failed
        assert result.data.passed is False
        assert result.data.total_errors == 1
