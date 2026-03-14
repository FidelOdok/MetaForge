"""Handler for the validate_stress skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import StressResult, ValidateStressInput, ValidateStressOutput


class ValidateStressHandler(SkillBase[ValidateStressInput, ValidateStressOutput]):
    """Validates stress analysis results against design constraints using CalculiX FEA."""

    input_type = ValidateStressInput
    output_type = ValidateStressOutput

    async def validate_preconditions(self, input_data: ValidateStressInput) -> list[str]:
        """Check that the work_product exists and CalculiX is available."""
        errors: list[str] = []

        # Check work_product exists in the Twin
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        # Check CalculiX tool is available
        if not await self.context.mcp.is_available("calculix.run_fea"):
            errors.append("CalculiX FEA tool is not available")

        return errors

    async def execute(self, input_data: ValidateStressInput) -> ValidateStressOutput:
        """Run FEA via CalculiX and validate stress against constraints."""
        self.logger.info(
            "Running stress validation",
            work_product_id=str(input_data.work_product_id),
            load_case=input_data.load_case,
        )

        # Invoke CalculiX FEA via MCP
        fea_result = await self.context.mcp.invoke(
            "calculix.run_fea",
            {
                "mesh_file": input_data.mesh_file_path,
                "load_case": input_data.load_case,
                "analysis_type": "static_stress",
            },
            timeout=300,
        )

        # Process results against constraints
        results: list[StressResult] = []
        max_stress = 0.0
        critical_region = ""

        stress_data: dict[str, Any] = fea_result.get("max_von_mises", {})

        for constraint in input_data.constraints:
            allowable = constraint.max_von_mises_mpa / constraint.safety_factor

            for region, stress_val_raw in stress_data.items():
                stress_val = float(stress_val_raw)
                sf_achieved = (
                    constraint.max_von_mises_mpa / stress_val if stress_val > 0 else float("inf")
                )
                passed = stress_val <= allowable

                results.append(
                    StressResult(
                        region=region,
                        max_von_mises_mpa=stress_val,
                        allowable_mpa=allowable,
                        safety_factor_achieved=round(sf_achieved, 4),
                        passed=passed,
                    )
                )

                if stress_val > max_stress:
                    max_stress = stress_val
                    critical_region = region

        overall_passed = all(r.passed for r in results)

        return ValidateStressOutput(
            work_product_id=input_data.work_product_id,
            overall_passed=overall_passed,
            results=results,
            max_stress_mpa=max_stress,
            critical_region=critical_region,
            solver_time_seconds=fea_result.get("solver_time", 0.0),
            mesh_elements=fea_result.get("mesh_elements", 0),
        )
