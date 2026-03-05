"""Handler for the run_cfd skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunCfdInput, RunCfdOutput

SUPPORTED_MESH_RESOLUTIONS = {"coarse", "medium", "fine"}


class RunCfdHandler(SkillBase[RunCfdInput, RunCfdOutput]):
    """Runs CFD thermal/flow analysis via the MCP bridge.

    Invokes the ``calculix.run_thermal`` tool through the MCP bridge
    for computational fluid dynamics analysis, returning velocity,
    pressure, and temperature data.
    """

    input_type = RunCfdInput
    output_type = RunCfdOutput

    async def validate_preconditions(self, input_data: RunCfdInput) -> list[str]:
        """Check that the artifact exists and CFD tool is available."""
        errors: list[str] = []

        artifact = await self.context.twin.get_artifact(
            input_data.artifact_id, branch=self.context.branch
        )
        if artifact is None:
            errors.append(f"Artifact {input_data.artifact_id} not found in Twin")

        if not await self.context.mcp.is_available("calculix.run_thermal"):
            errors.append("CalculiX thermal/CFD tool is not available")

        return errors

    async def execute(self, input_data: RunCfdInput) -> RunCfdOutput:
        """Run CFD via CalculiX MCP tool and return structured results."""
        self.logger.info(
            "Running CFD",
            artifact_id=input_data.artifact_id,
            geometry_file=input_data.geometry_file,
            mesh_resolution=input_data.mesh_resolution,
        )

        if input_data.mesh_resolution not in SUPPORTED_MESH_RESOLUTIONS:
            raise ValueError(
                f"Unsupported mesh resolution '{input_data.mesh_resolution}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_MESH_RESOLUTIONS))}"
            )

        # Invoke CalculiX thermal/CFD via MCP
        cfd_result: dict[str, Any] = await self.context.mcp.invoke(
            "calculix.run_thermal",
            {
                "geometry_file": input_data.geometry_file,
                "fluid_properties": input_data.fluid_properties,
                "boundary_conditions": input_data.boundary_conditions,
                "mesh_resolution": input_data.mesh_resolution,
            },
            timeout=600,
        )

        return RunCfdOutput(
            artifact_id=input_data.artifact_id,
            max_velocity_ms=float(cfd_result.get("max_velocity_ms", 0.0)),
            pressure_drop_pa=float(cfd_result.get("pressure_drop_pa", 0.0)),
            max_temperature_c=float(cfd_result.get("max_temperature_c", 0.0)),
            convergence_residual=float(cfd_result.get("convergence_residual", 1.0)),
        )

    async def validate_output(self, output: RunCfdOutput) -> list[str]:
        """Verify output consistency."""
        errors: list[str] = []
        if output.convergence_residual >= 1.0:
            errors.append(
                f"CFD residual {output.convergence_residual:.2e} indicates no convergence"
            )
        return errors
