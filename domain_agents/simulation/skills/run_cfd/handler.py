"""Handler for the run_cfd skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunCfdInput, RunCfdOutput

NO_ANALYSIS = (
    "Pass one analysis: `conduction` (steady conduction to a fixed-temperature sink, "
    "solved with calculix.run_thermal) or `internal_flow` (steady internal flow, solved "
    "with openfoam.run_internal_flow). Convection, external flow, compressible flow and "
    "heat transfer to a moving fluid are not solved by MetaForge."
)
FREE_FORM_FLOW = (
    "fluid_properties and boundary_conditions are free-form and are not used. Give the "
    "flow as `internal_flow`, which takes the fluid, the inlet velocity, the turbulence "
    "model and the surfaces as typed fields. " + NO_ANALYSIS
)
ONE_ANALYSIS = "Pass `conduction` or `internal_flow`, not both: one analysis per call."

CONDUCTION_TOOL = "calculix.run_thermal"
FLOW_TOOL = "openfoam.run_internal_flow"


class RunCfdHandler(SkillBase[RunCfdInput, RunCfdOutput]):
    """Steady conduction (CalculiX) or steady internal flow (OpenFOAM).

    FORGE-543 made the skill honest: it used to call ``calculix.run_thermal``
    with arguments that tool does not take and read velocity and pressure keys
    it never returns, so any reply would have been zeros. FORGE-557 adds the
    flow solver. A flow run that fails raises with the stage and OpenFOAM's own
    reason, and one that does not converge says so in the output; neither is
    reported as an answer.
    """

    input_type = RunCfdInput
    output_type = RunCfdOutput

    async def validate_preconditions(self, input_data: RunCfdInput) -> list[str]:
        """Check that the work_product exists and the tool for this analysis is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        tool = FLOW_TOOL if input_data.internal_flow is not None else CONDUCTION_TOOL
        if not await self.context.mcp.is_available(tool):
            errors.append(f"{tool} is not available")

        return errors

    async def execute(self, input_data: RunCfdInput) -> RunCfdOutput:
        """Run one analysis, or refuse a request that names none MetaForge solves."""
        self.logger.info(
            "Running run_cfd",
            work_product_id=input_data.work_product_id,
            geometry_file=input_data.geometry_file,
            analysis="internal_flow" if input_data.internal_flow else "conduction",
        )
        if input_data.fluid_properties or input_data.boundary_conditions:
            raise ValueError(FREE_FORM_FLOW)
        if input_data.conduction is not None and input_data.internal_flow is not None:
            raise ValueError(ONE_ANALYSIS)
        if input_data.internal_flow is not None:
            return await self._internal_flow(input_data)
        if input_data.conduction is not None:
            return await self._conduction(input_data)
        raise ValueError(NO_ANALYSIS)

    async def _conduction(self, input_data: RunCfdInput) -> RunCfdOutput:
        assert input_data.conduction is not None
        result: dict[str, Any] = await self.context.mcp.invoke(
            CONDUCTION_TOOL,
            {"analysis_mode": "steady_state", **input_data.conduction.model_dump()},
            timeout=600,
        )
        if "max_temperature_c" not in result:
            raise ValueError("calculix.run_thermal returned no max_temperature_c")

        return RunCfdOutput(
            work_product_id=input_data.work_product_id,
            analysis="conduction_only",
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

    async def _internal_flow(self, input_data: RunCfdInput) -> RunCfdOutput:
        assert input_data.internal_flow is not None
        result: dict[str, Any] = await self.context.mcp.invoke(
            FLOW_TOOL,
            input_data.internal_flow.model_dump(exclude_none=True),
            timeout=3900,
        )
        if not result.get("completed"):
            stage = result.get("stage_failed") or "unknown stage"
            raise ValueError(
                f"openfoam did not finish ({stage}): {result.get('error', 'no reason given')}"
            )
        if result.get("pressure_drop_pa") is None:
            raise ValueError("openfoam finished but wrote no pressure drop")

        converged = bool(result.get("converged"))
        warnings = [str(w) for w in result.get("warnings", [])]
        if not converged:
            warnings.insert(
                0,
                "not converged: the residual controls were not met"
                + (
                    "; the pressure drop had stopped moving (solution_stable)"
                    if result.get("solution_stable")
                    else "; the pressure drop was still moving, so treat it as unsettled"
                ),
            )
        return RunCfdOutput(
            work_product_id=input_data.work_product_id,
            analysis="internal_flow",
            pressure_drop_pa=float(result["pressure_drop_pa"]),
            volumetric_flow_m3_s=(result.get("inlet") or {}).get("volumetric_flow_m3_s"),
            converged=converged,
            solution_stable=result.get("solution_stable"),
            mesh_cells=(result.get("mesh") or {}).get("cells"),
            case_dir=result.get("case_dir"),
            solver=FLOW_TOOL,
            warnings=warnings,
        )
