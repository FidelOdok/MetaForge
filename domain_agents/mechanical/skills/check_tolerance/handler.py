"""Handler for the check_tolerance skill."""

from __future__ import annotations

import math

from skill_registry.skill_base import SkillBase

from .schema import (
    CheckToleranceInput,
    CheckToleranceOutput,
    ToleranceResult,
    ToleranceSpec,
    ToleranceViolation,
)


class CheckToleranceHandler(SkillBase[CheckToleranceInput, CheckToleranceOutput]):
    """Analyzes dimensional tolerances against manufacturing process capabilities.

    This is a pure computation skill -- it does not invoke any MCP tools.
    It validates tolerances against DFM (Design for Manufacturability) rules
    and returns a compliance report with flagged violations.
    """

    input_type = CheckToleranceInput
    output_type = CheckToleranceOutput

    async def validate_preconditions(self, input_data: CheckToleranceInput) -> list[str]:
        """Check that the artifact exists in the Twin."""
        errors: list[str] = []

        artifact = await self.context.twin.get_artifact(
            input_data.artifact_id, branch=self.context.branch
        )
        if artifact is None:
            errors.append(f"Artifact {input_data.artifact_id} not found in Twin")

        return errors

    async def execute(self, input_data: CheckToleranceInput) -> CheckToleranceOutput:
        """Analyze tolerances against manufacturing process capabilities."""
        self.logger.info(
            "Running tolerance check",
            artifact_id=input_data.artifact_id,
            process=input_data.manufacturing_process.process_type,
            num_tolerances=len(input_data.tolerances),
        )

        process = input_data.manufacturing_process
        # Process sigma: achievable_tolerance represents ~3-sigma capability
        process_sigma = process.achievable_tolerance / 3.0

        results: list[ToleranceResult] = []
        violations: list[ToleranceViolation] = []

        for spec in input_data.tolerances:
            tol_range = spec.upper_tolerance - spec.lower_tolerance

            # Calculate capability index: Cp = tolerance_range / (6 * sigma)
            cp = tol_range / (6.0 * process_sigma) if process_sigma > 0 else 0.0

            # Classify based on Cp
            if cp >= 1.33:
                status = "pass"
            elif cp >= 1.0:
                status = "warning"
            else:
                status = "fail"

            message = self._build_result_message(spec, tol_range, cp, status, process)
            results.append(
                ToleranceResult(
                    dimension_id=spec.dimension_id,
                    feature_name=spec.feature_name,
                    nominal_value=spec.nominal_value,
                    tolerance_range=round(tol_range, 6),
                    status=status,
                    capability_index=round(cp, 4),
                    message=message,
                )
            )

            # Check for too-tight tolerance violation
            if tol_range < process.achievable_tolerance:
                violations.append(
                    ToleranceViolation(
                        dimension_id=spec.dimension_id,
                        feature_name=spec.feature_name,
                        violation_type="too_tight",
                        severity="error",
                        specified_tolerance=round(tol_range, 6),
                        achievable_tolerance=process.achievable_tolerance,
                        message=(
                            f"Tolerance band {tol_range:.4f} mm is tighter than "
                            f"process capability {process.achievable_tolerance:.4f} mm"
                        ),
                        recommendation=(
                            f"Widen tolerance to at least "
                            f"{process.achievable_tolerance:.4f} mm or use a "
                            f"more precise manufacturing process"
                        ),
                    )
                )

            # Check minimum feature size
            if process.min_feature_size > 0 and spec.nominal_value < process.min_feature_size:
                violations.append(
                    ToleranceViolation(
                        dimension_id=spec.dimension_id,
                        feature_name=spec.feature_name,
                        violation_type="below_min_feature",
                        severity="error",
                        specified_tolerance=round(tol_range, 6),
                        achievable_tolerance=process.achievable_tolerance,
                        message=(
                            f"Nominal value {spec.nominal_value:.4f} mm is below "
                            f"minimum feature size {process.min_feature_size:.4f} mm "
                            f"for {process.process_type}"
                        ),
                        recommendation=(
                            f"Increase feature size to at least "
                            f"{process.min_feature_size:.4f} mm or choose a "
                            f"process with finer resolution"
                        ),
                    )
                )

            # Check aspect ratio (use nominal_value as proxy for depth)
            if process.max_aspect_ratio > 0 and spec.nominal_value > 0:
                # Estimate aspect ratio as nominal_value / tolerance_range
                # This is a simplified proxy: deep narrow features have high aspect ratio
                estimated_ar = spec.nominal_value / tol_range if tol_range > 0 else 0.0
                if estimated_ar > process.max_aspect_ratio:
                    violations.append(
                        ToleranceViolation(
                            dimension_id=spec.dimension_id,
                            feature_name=spec.feature_name,
                            violation_type="aspect_ratio_exceeded",
                            severity="warning",
                            specified_tolerance=round(tol_range, 6),
                            achievable_tolerance=process.achievable_tolerance,
                            message=(
                                f"Estimated aspect ratio {estimated_ar:.1f} exceeds "
                                f"process limit {process.max_aspect_ratio:.1f}"
                            ),
                            recommendation=(
                                "Reduce depth-to-width ratio or use a process "
                                "that supports higher aspect ratios"
                            ),
                        )
                    )

            # Marginal capability warning (Cp between 1.0 and 1.33)
            if 1.0 <= cp < 1.33 and status == "warning":
                # Only add if not already flagged as too_tight
                has_too_tight = any(
                    v.dimension_id == spec.dimension_id and v.violation_type == "too_tight"
                    for v in violations
                )
                if not has_too_tight:
                    violations.append(
                        ToleranceViolation(
                            dimension_id=spec.dimension_id,
                            feature_name=spec.feature_name,
                            violation_type="too_tight",
                            severity="warning",
                            specified_tolerance=round(tol_range, 6),
                            achievable_tolerance=process.achievable_tolerance,
                            message=(
                                f"Capability index Cp={cp:.2f} is marginal (recommended >= 1.33)"
                            ),
                            recommendation=(
                                "Consider widening tolerance or upgrading process "
                                "to improve capability index"
                            ),
                        )
                    )

        # Optional: tolerance stack-up analysis using RSS method
        if input_data.check_stack_up and len(input_data.tolerances) > 1:
            self._check_stack_up(input_data, process, violations)

        # Compute summary counts
        num_passed = sum(1 for r in results if r.status == "pass")
        num_warnings = sum(1 for r in results if r.status == "warning")
        num_failures = sum(1 for r in results if r.status == "fail")

        # Determine overall status
        if num_failures > 0:
            overall_status = "fail"
        elif num_warnings > 0:
            overall_status = "marginal"
        else:
            overall_status = "pass"

        summary = self._build_summary(
            len(results),
            num_passed,
            num_warnings,
            num_failures,
            overall_status,
            process.process_type,
        )

        return CheckToleranceOutput(
            artifact_id=input_data.artifact_id,
            process_type=process.process_type,
            total_dimensions_checked=len(results),
            passed=num_passed,
            warnings=num_warnings,
            failures=num_failures,
            overall_status=overall_status,
            results=results,
            violations=violations,
            summary=summary,
        )

    async def validate_output(self, output: CheckToleranceOutput) -> list[str]:
        """Verify output consistency."""
        errors: list[str] = []
        expected_total = output.passed + output.warnings + output.failures
        if output.total_dimensions_checked != expected_total:
            errors.append(
                f"Total dimensions ({output.total_dimensions_checked}) does not equal "
                f"passed ({output.passed}) + warnings ({output.warnings}) "
                f"+ failures ({output.failures}) = {expected_total}"
            )
        return errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_result_message(
        spec: ToleranceSpec,
        tol_range: float,
        cp: float,
        status: str,
        process: object,
    ) -> str:
        """Build a human-readable message for a single tolerance result."""
        if status == "pass":
            return (
                f"{spec.feature_name} ({spec.dimension_id}): tolerance band "
                f"{tol_range:.4f} mm, Cp={cp:.2f} -- PASS"
            )
        if status == "warning":
            return (
                f"{spec.feature_name} ({spec.dimension_id}): tolerance band "
                f"{tol_range:.4f} mm, Cp={cp:.2f} -- MARGINAL (Cp < 1.33)"
            )
        return (
            f"{spec.feature_name} ({spec.dimension_id}): tolerance band "
            f"{tol_range:.4f} mm, Cp={cp:.2f} -- FAIL (Cp < 1.0)"
        )

    @staticmethod
    def _check_stack_up(
        input_data: CheckToleranceInput,
        process: object,
        violations: list[ToleranceViolation],
    ) -> None:
        """Perform RSS tolerance stack-up analysis.

        Stack-up tolerance = sqrt(sum(ti^2)) where ti = individual tolerance range.
        Compares against a simple worst-case limit.
        """
        tol_ranges = [spec.upper_tolerance - spec.lower_tolerance for spec in input_data.tolerances]
        rss_stack = math.sqrt(sum(t**2 for t in tol_ranges))
        worst_case = sum(tol_ranges)

        # If RSS stack-up exceeds worst-case / 2 (heuristic threshold), flag it
        if rss_stack > worst_case * 0.75:
            violations.append(
                ToleranceViolation(
                    dimension_id="STACK_UP",
                    feature_name="tolerance_chain",
                    violation_type="stack_up_exceeded",
                    severity="warning",
                    specified_tolerance=round(rss_stack, 6),
                    achievable_tolerance=round(worst_case, 6),
                    message=(
                        f"RSS stack-up {rss_stack:.4f} mm is {rss_stack / worst_case * 100:.0f}% "
                        f"of worst-case {worst_case:.4f} mm"
                    ),
                    recommendation=(
                        "Review tolerance chain for tighter individual tolerances "
                        "or consider datum-based dimensioning"
                    ),
                )
            )

    @staticmethod
    def _build_summary(
        total: int,
        passed: int,
        warnings: int,
        failures: int,
        overall_status: str,
        process_type: str,
    ) -> str:
        """Build a human-readable summary string."""
        if total == 0:
            return f"No dimensions checked for {process_type}."

        return (
            f"Checked {total} dimension(s) against {process_type} capabilities: "
            f"{passed} passed, {warnings} marginal, {failures} failed. "
            f"Overall status: {overall_status.upper()}."
        )
