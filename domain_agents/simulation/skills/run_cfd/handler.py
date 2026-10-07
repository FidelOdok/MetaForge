"""Handler for the run_cfd skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunCfdInput, RunCfdOutput

NO_FLOW_SOLVER = (
    "MetaForge has no flow solver, so velocity, pressure drop and convection "
    "cannot be computed. For heat conducted through the part to a fixed-"
    "temperature sink, pass `conduction` (solved with calculix.run_thermal)."
)


class RunCfdHandler(SkillBase[RunCfdInput, RunCfdOutput]):
    """Answers the conduction part of a thermal question; refuses flow (FORGE-543).

    It used to call ``calculix.run_thermal`` with arguments that tool does
    not take (geometry_file, fluid_properties, ...), and read velocity and
    pressure keys it never returns, so the skill could not succeed and any
    reply would have been zeros. CalculiX solves steady conduction to a
    fixed-temperature sink; that is what this skill runs, and its result is
    labelled ``conduction_only``.
    """

    input_type = RunCfdInput
    output_type = RunCfdOutput

    async def validate_preconditions(self, input_data: RunCfdInput) -> list[str]:
        """Check that the work_product exists and the thermal tool is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if not await self.context.mcp.is_available("calculix.run_thermal"):
            errors.append("calculix.run_thermal is not available")

        return errors

    async def execute(self, input_data: RunCfdInput) -> RunCfdOutput:
        """Run the conduction case, or refuse a flow question."""
        self.logger.info(
            "Running thermal (conduction only)",
            work_product_id=input_data.work_product_id,
            geometry_file=input_data.geometry_file,
            flow_requested=bool(input_data.fluid_properties or input_data.boundary_conditions),
        )
        if input_data.fluid_properties or input_data.boundary_conditions:
            raise ValueError(NO_FLOW_SOLVER)
        if input_data.conduction is None:
            raise ValueError(NO_FLOW_SOLVER)

        result: dict[str, Any] = await self.context.mcp.invoke(
            "calculix.run_thermal",
            {"analysis_mode": "steady_state", **input_data.conduction.model_dump()},
            timeout=600,
        )
        if "max_temperature_c" not in result:
            raise ValueError("calculix.run_thermal returned no max_temperature_c")

        return RunCfdOutput(
            work_product_id=input_data.work_product_id,
            max_temperature_c=float(result["max_temperature_c"]),
            min_temperature_c=(
                float(result["min_temperature_c"])
                if result.get("min_temperature_c") is not None
                else None
            ),
            warnings=[
                "conduction only: no convection to air and no flow field",
                *[str(w) for w in result.get("warnings", [])],
            ],
        )
