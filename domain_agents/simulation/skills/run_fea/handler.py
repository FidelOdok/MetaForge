"""Handler for the run_fea skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunFeaInput, RunFeaOutput

SUPPORTED_ANALYSIS_TYPES = {"static", "modal", "thermal"}

#: The skill speaks in engineering terms ("static"); the CalculiX adapter's
#: tool enum uses "static_stress". Mapping here rather than at the call site
#: keeps the skill's public schema stable.
_ADAPTER_ANALYSIS = {"static": "static_stress", "modal": "modal"}

#: Tool invoked per analysis type. Thermal is a different solve -- a
#: ``*HEAT TRANSFER`` step -- and lives behind its own tool.
_THERMAL_TOOL = "calculix.run_thermal"
_FEA_TOOL = "calculix.run_fea"


class RunFeaHandler(SkillBase[RunFeaInput, RunFeaOutput]):
    """Runs FEA structural analysis via the MCP bridge.

    Invokes the ``calculix.run_fea`` tool through the MCP bridge,
    parses the structured results, and returns a ``RunFeaOutput``
    with stress, displacement, and safety factor data.

    ``load_cases`` carries the physics -- constraints and loads -- that the
    adapter turns into a complete CalculiX input deck. A run with no load cases
    solves whatever the mesh file already contains, which for a mesher's
    geometry-only output is nothing.
    """

    input_type = RunFeaInput
    output_type = RunFeaOutput

    async def validate_preconditions(self, input_data: RunFeaInput) -> list[str]:
        """Check that the work_product exists and the required tool is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        tool_id = _THERMAL_TOOL if input_data.analysis_type == "thermal" else _FEA_TOOL
        if not await self.context.mcp.is_available(tool_id):
            errors.append(f"CalculiX FEA tool is not available ({tool_id})")

        return errors

    async def execute(self, input_data: RunFeaInput) -> RunFeaOutput:
        """Run FEA via CalculiX MCP tool and return structured results."""
        self.logger.info(
            "Running FEA",
            work_product_id=input_data.work_product_id,
            mesh_file=input_data.mesh_file,
            analysis_type=input_data.analysis_type,
            material=input_data.material,
            load_cases=len(input_data.load_cases),
        )

        if input_data.analysis_type not in SUPPORTED_ANALYSIS_TYPES:
            raise ValueError(
                f"Unsupported analysis type '{input_data.analysis_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_ANALYSIS_TYPES))}"
            )

        if not input_data.load_cases:
            self.logger.warning(
                "No load cases supplied; the mesh will be solved as authored",
                mesh_file=input_data.mesh_file,
                remedy=(
                    "Supply load_cases with constraints and loads so a complete deck is generated"
                ),
            )

        if input_data.analysis_type == "thermal":
            result = await self._invoke_thermal(input_data)
        else:
            result = await self._invoke_structural(input_data)

        return self._build_output(input_data, result)

    async def _invoke_structural(self, input_data: RunFeaInput) -> dict[str, Any]:
        """Invoke the structural (static or modal) FEA tool."""
        result: dict[str, Any] = await self.context.mcp.invoke(
            _FEA_TOOL,
            {
                "mesh_file": input_data.mesh_file,
                "load_case": input_data.analysis_type,
                "load_cases": input_data.load_cases,
                "analysis_type": _ADAPTER_ANALYSIS[input_data.analysis_type],
                "material": input_data.material,
            },
            timeout=300,
        )
        return result

    async def _invoke_thermal(self, input_data: RunFeaInput) -> dict[str, Any]:
        """Invoke the thermal tool, treating the first load case as its BCs."""
        boundary_conditions = input_data.load_cases[0] if input_data.load_cases else {}
        result: dict[str, Any] = await self.context.mcp.invoke(
            _THERMAL_TOOL,
            {
                "mesh_file": input_data.mesh_file,
                "boundary_conditions": boundary_conditions,
                "analysis_mode": "steady_state",
                "material": input_data.material,
            },
            timeout=600,
        )
        return result

    def _build_output(self, input_data: RunFeaInput, result: dict[str, Any]) -> RunFeaOutput:
        """Map an adapter response onto the skill's output schema.

        Each field is read under the adapter's current key with the historical
        key as a fallback, so a response from either shape is understood rather
        than silently defaulting to zero.
        """
        safety_factor = _as_float(result.get("safety_factor"))

        return RunFeaOutput(
            work_product_id=input_data.work_product_id,
            max_stress_mpa=_first_float(result, "max_von_mises_mpa", "max_stress_mpa"),
            max_displacement_mm=_first_float(result, "max_displacement_mm", "max_displacement"),
            # An unloaded region has infinite margin, which is not representable
            # in the output schema; report it as 0.0 and let validate_output
            # flag the run as producing nothing meaningful.
            safety_factor=0.0 if safety_factor == float("inf") else safety_factor,
            solver_time_s=_first_float(result, "solver_time", "solver_time_s"),
            governing_load_case=result.get("governing_load_case"),
            natural_frequencies_hz=[
                float(f) for f in result.get("natural_frequencies_hz", []) or []
            ],
            material=str(result.get("material", input_data.material)),
        )

    async def validate_output(self, output: RunFeaOutput) -> list[str]:
        """Verify output consistency."""
        errors: list[str] = []
        if (
            output.max_stress_mpa <= 0
            and output.safety_factor <= 0
            and not output.natural_frequencies_hz
        ):
            errors.append("FEA produced no meaningful stress or safety factor results")
        return errors


def _as_float(value: Any) -> float:
    """Coerce a response value to float, treating missing or unparseable as 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_float(result: dict[str, Any], *keys: str) -> float:
    """Return the first key present in the response, as a float."""
    for key in keys:
        if key in result:
            return _as_float(result[key])
    return 0.0
