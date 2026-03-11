"""Handler for the run_erc skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import ErcViolation, RunErcInput, RunErcOutput


class RunErcHandler(SkillBase[RunErcInput, RunErcOutput]):
    """Runs Electrical Rules Check on a KiCad schematic via MCP bridge.

    Invokes the ``kicad.run_erc`` tool through the MCP bridge,
    parses the structured results, and returns a ``RunErcOutput``
    with categorised violations and pass/fail status.
    """

    input_type = RunErcInput
    output_type = RunErcOutput

    async def validate_preconditions(self, input_data: RunErcInput) -> list[str]:
        """Check that the artifact exists and kicad.run_erc is available."""
        errors: list[str] = []

        # Check artifact exists in the Twin
        artifact = await self.context.twin.get_artifact(
            input_data.artifact_id, branch=self.context.branch
        )
        if artifact is None:
            errors.append(f"Artifact {input_data.artifact_id} not found in Twin")

        # Check KiCad ERC tool is available
        if not await self.context.mcp.is_available("kicad.run_erc"):
            errors.append("KiCad ERC tool is not available")

        return errors

    async def execute(self, input_data: RunErcInput) -> RunErcOutput:
        """Run ERC via KiCad MCP tool and return structured results."""
        self.logger.info(
            "Running ERC",
            artifact_id=input_data.artifact_id,
            schematic_file=input_data.schematic_file,
            severity_filter=input_data.severity_filter,
        )

        # Invoke KiCad ERC via MCP
        erc_result = await self.context.mcp.invoke(
            "kicad.run_erc",
            {
                "schematic_file": input_data.schematic_file,
                "severity_filter": input_data.severity_filter,
            },
            timeout=120,
        )

        # Parse violations from the tool result
        violations = self._parse_violations(
            erc_result.get("violations", []),
            input_data.severity_filter,
        )

        total_errors = sum(1 for v in violations if v.severity == "error")
        total_warnings = sum(1 for v in violations if v.severity == "warning")
        total_violations = len(violations)

        # Passed = no errors (warnings are acceptable)
        passed = total_errors == 0

        summary = self._build_summary(
            input_data.schematic_file,
            total_violations,
            total_errors,
            total_warnings,
            passed,
        )

        return RunErcOutput(
            artifact_id=input_data.artifact_id,
            schematic_file=input_data.schematic_file,
            violations=violations,
            total_violations=total_violations,
            total_errors=total_errors,
            total_warnings=total_warnings,
            passed=passed,
            summary=summary,
        )

    async def validate_output(self, output: RunErcOutput) -> list[str]:
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
    ) -> list[ErcViolation]:
        """Parse raw violation dicts into typed ErcViolation models."""
        violations: list[ErcViolation] = []
        for raw in raw_violations:
            severity = raw.get("severity", "error")
            # Apply severity filter
            if severity_filter == "error" and severity != "error":
                continue
            if severity_filter == "warning" and severity != "warning":
                continue

            violations.append(
                ErcViolation(
                    rule_id=raw.get("rule_id", "ERC_UNKNOWN"),
                    severity=severity,
                    message=raw.get("message", "Unknown ERC violation"),
                    sheet=raw.get("sheet", ""),
                    component=raw.get("component", ""),
                    pin=raw.get("pin", ""),
                    location=raw.get("location", ""),
                )
            )
        return violations

    @staticmethod
    def _build_summary(
        schematic_file: str,
        total_violations: int,
        total_errors: int,
        total_warnings: int,
        passed: bool,
    ) -> str:
        """Build a human-readable summary string."""
        status = "PASSED" if passed else "FAILED"
        if total_violations == 0:
            return f"ERC {status}: No violations found in {schematic_file}."
        return (
            f"ERC {status}: {total_violations} violation(s) found in {schematic_file} "
            f"({total_errors} error(s), {total_warnings} warning(s))."
        )
