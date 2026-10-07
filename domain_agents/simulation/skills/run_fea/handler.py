"""Handler for the run_fea skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunFeaInput, RunFeaOutput


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

        # FORGE-561: calculix.run_fea's own arguments; it used to get
        # load_cases / "static" / a material name and refuse every call.
        arguments: dict[str, Any] = {
            "mesh_file": input_data.mesh_file,
            "load_case": input_data.load_case,
            "analysis_type": input_data.analysis_type,
            "material": input_data.material,
            "fixed_node_set": input_data.fixed_node_set,
        }
        if input_data.analysis_type == "static_stress":
            arguments["load_node_set"] = input_data.load_node_set
            arguments["load_force_n"] = list(input_data.load_force_n or [])
        else:
            arguments["num_modes"] = input_data.num_modes
        fea_result: dict[str, Any] = await self.context.mcp.invoke(
            "calculix.run_fea", arguments, timeout=300
        )

        # The tool reports max_von_mises.global and displacement.max; it has
        # no yield data, so a safety factor exists only when one was given.
        stress_raw = (fea_result.get("max_von_mises") or {}).get("global")
        stress = float(stress_raw) if stress_raw is not None else None
        disp_raw = (fea_result.get("displacement") or {}).get("max")
        safety = (
            round(input_data.yield_strength_mpa / stress, 3)
            if input_data.yield_strength_mpa and stress
            else None
        )
        return RunFeaOutput(
            work_product_id=input_data.work_product_id,
            analysis_type=input_data.analysis_type,
            max_stress_mpa=stress if input_data.analysis_type == "static_stress" else None,
            max_displacement_mm=float(disp_raw) if disp_raw is not None else None,
            safety_factor=safety,
            frequencies_hz=[float(f) for f in fea_result.get("frequencies_hz") or []],
            frd_path=str(fea_result.get("frd_path", "")),
            solver_time_s=float(fea_result.get("solver_time", 0.0)),
        )

    async def validate_output(self, output: RunFeaOutput) -> list[str]:
        """A solve that reports no result is a failure, not a zero."""
        if output.analysis_type == "static_stress" and output.max_stress_mpa is None:
            return ["calculix.run_fea returned no von Mises stress"]
        if output.analysis_type == "modal" and not output.frequencies_hz:
            return ["calculix.run_fea returned no frequencies"]
        return []
