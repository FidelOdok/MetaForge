"""Handler for the run_drc skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import DrcViolation, RunDrcInput, RunDrcOutput


def _location_text(location: Any) -> str:
    """kicad.run_drc gives ``{"x", "y", "layer"}``; the skill keeps a string.

    Passing the dict straight into the ``str`` field failed validation on
    every board with a located violation (FORGE-551).
    """
    if isinstance(location, dict):
        if "x" in location or "y" in location:
            return f"({location.get('x', 0)}, {location.get('y', 0)}) mm"
        return ""
    return str(location or "")


def _location_layer(location: Any) -> str:
    return str(location.get("layer", "")) if isinstance(location, dict) else ""


class RunDrcHandler(SkillBase[RunDrcInput, RunDrcOutput]):
    """Runs Design Rules Check on a KiCad PCB layout via MCP bridge.

    Invokes the ``kicad.run_drc`` tool through the MCP bridge,
    parses the structured results, and returns a ``RunDrcOutput``
    with categorised violations and pass/fail status.
    """

    input_type = RunDrcInput
    output_type = RunDrcOutput

    async def validate_preconditions(self, input_data: RunDrcInput) -> list[str]:
        """Check that the work_product exists and kicad.run_drc is available."""
        errors: list[str] = []

        # Check work_product exists in the Twin
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        # Check KiCad DRC tool is available
        if not await self.context.mcp.is_available("kicad.run_drc"):
            errors.append("KiCad DRC tool is not available")

        return errors

    async def execute(self, input_data: RunDrcInput) -> RunDrcOutput:
        """Run DRC via KiCad MCP tool and return structured results."""
        self.logger.info(
            "Running DRC",
            work_product_id=input_data.work_product_id,
            pcb_file=input_data.pcb_file,
            severity_filter=input_data.severity_filter,
        )

        # Invoke KiCad DRC via MCP. The tool is asked for every severity so the
        # verdict below can count all errors; the skill applies its own filter
        # to the listed violations (FORGE-551).
        arguments: dict[str, Any] = {"pcb_file": input_data.pcb_file, "severity_filter": "all"}
        if input_data.rule_set:
            arguments["rule_set"] = input_data.rule_set
        drc_result = await self.context.mcp.invoke("kicad.run_drc", arguments, timeout=120)

        raw_violations = drc_result.get("violations", [])
        violations = self._parse_violations(raw_violations, input_data.severity_filter)

        total_errors = sum(1 for v in violations if v.severity == "error")
        total_warnings = sum(1 for v in violations if v.severity == "warning")
        total_violations = len(violations)
        unconnected = int(drc_result.get("unconnected_items", 0) or 0)

        # FORGE-551: passed was "no errors among the filtered violations", so
        # filtering to warnings passed any board, and unrouted nets (KiCad's
        # unconnected_items) were never counted. Warnings are still allowed.
        all_errors = sum(1 for raw in raw_violations if raw.get("severity", "error") == "error")
        passed = all_errors == 0 and unconnected == 0

        summary = self._build_summary(
            input_data.pcb_file,
            total_violations,
            total_errors,
            total_warnings,
            passed,
        )
        if unconnected:
            summary += f" {unconnected} unconnected item(s)."

        return RunDrcOutput(
            work_product_id=input_data.work_product_id,
            pcb_file=input_data.pcb_file,
            violations=violations,
            total_violations=total_violations,
            total_errors=total_errors,
            total_warnings=total_warnings,
            unconnected_items=unconnected,
            rule_set_applied=bool(drc_result.get("rule_set_applied", False)),
            passed=passed,
            summary=summary,
        )

    async def validate_output(self, output: RunDrcOutput) -> list[str]:
        """Verify output consistency."""
        errors: list[str] = []
        expected_total = output.total_errors + output.total_warnings
        if output.total_violations != expected_total:
            errors.append(
                f"Total violations ({output.total_violations}) does not equal "
                f"errors ({output.total_errors}) + warnings ({output.total_warnings}) "
                f"= {expected_total}"
            )
        return errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_violations(
        raw_violations: list[dict[str, Any]],
        severity_filter: str,
    ) -> list[DrcViolation]:
        """Parse raw violation dicts into typed DrcViolation models."""
        violations: list[DrcViolation] = []
        for raw in raw_violations:
            severity = raw.get("severity", "error")
            # Apply severity filter
            if severity_filter == "error" and severity != "error":
                continue
            if severity_filter == "warning" and severity != "warning":
                continue

            violations.append(
                DrcViolation(
                    rule_id=raw.get("rule_id", "DRC_UNKNOWN"),
                    severity=severity,
                    message=raw.get("message", "Unknown DRC violation"),
                    layer=raw.get("layer", "") or _location_layer(raw.get("location")),
                    location=_location_text(raw.get("location")),
                    items=raw.get("items", []),
                )
            )
        return violations

    @staticmethod
    def _build_summary(
        pcb_file: str,
        total_violations: int,
        total_errors: int,
        total_warnings: int,
        passed: bool,
    ) -> str:
        """Build a human-readable summary string."""
        status = "PASSED" if passed else "FAILED"
        if total_violations == 0:
            return f"DRC {status}: No violations found in {pcb_file}."
        return (
            f"DRC {status}: {total_violations} violation(s) found in {pcb_file} "
            f"({total_errors} error(s), {total_warnings} warning(s))."
        )
