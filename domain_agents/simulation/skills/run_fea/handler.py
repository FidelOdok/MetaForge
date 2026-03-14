"""Handler for the run_fea skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunFeaInput, RunFeaOutput

SUPPORTED_ANALYSIS_TYPES = {"static", "modal", "thermal"}


class RunFeaHandler(SkillBase[RunFeaInput, RunFeaOutput]):
    """Runs FEA structural analysis via the MCP bridge.

    Invokes the ``calculix.run_fea`` tool through the MCP bridge,
    parses the structured results, and returns a ``RunFeaOutput``
    with stress, displacement, and safety factor data.
    """

    input_type = RunFeaInput
    output_type = RunFeaOutput

    async def validate_preconditions(self, input_data: RunFeaInput) -> list[str]:
        """Check that the work_product exists and CalculiX tool is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if not await self.context.mcp.is_available("calculix.run_fea"):
            errors.append("CalculiX FEA tool is not available")

        return errors

    async def execute(self, input_data: RunFeaInput) -> RunFeaOutput:
        """Run FEA via CalculiX MCP tool and return structured results."""
        self.logger.info(
            "Running FEA",
            work_product_id=input_data.work_product_id,
            mesh_file=input_data.mesh_file,
            analysis_type=input_data.analysis_type,
            material=input_data.material,
        )

        if input_data.analysis_type not in SUPPORTED_ANALYSIS_TYPES:
            raise ValueError(
                f"Unsupported analysis type '{input_data.analysis_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_ANALYSIS_TYPES))}"
            )

        # Invoke CalculiX FEA via MCP
        fea_result: dict[str, Any] = await self.context.mcp.invoke(
            "calculix.run_fea",
            {
                "mesh_file": input_data.mesh_file,
                "load_cases": input_data.load_cases,
                "analysis_type": input_data.analysis_type,
                "material": input_data.material,
            },
            timeout=300,
        )

        return RunFeaOutput(
            work_product_id=input_data.work_product_id,
            max_stress_mpa=float(fea_result.get("max_stress_mpa", 0.0)),
            max_displacement_mm=float(fea_result.get("max_displacement_mm", 0.0)),
            safety_factor=float(fea_result.get("safety_factor", 0.0)),
            solver_time_s=float(fea_result.get("solver_time_s", 0.0)),
        )

    async def validate_output(self, output: RunFeaOutput) -> list[str]:
        """Verify output consistency."""
        errors: list[str] = []
        if output.max_stress_mpa <= 0 and output.safety_factor <= 0:
            errors.append("FEA produced no meaningful stress or safety factor results")
        return errors
