"""Handler for the run_spice skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunSpiceInput, RunSpiceOutput

SUPPORTED_ANALYSIS_TYPES = {"op", "dc", "ac", "transient"}


class RunSpiceHandler(SkillBase[RunSpiceInput, RunSpiceOutput]):
    """Runs SPICE circuit simulation via the MCP bridge.

    Invokes the ``spice.run_simulation`` tool through the MCP bridge,
    parses the structured results, and returns a ``RunSpiceOutput``
    with simulation data and convergence status.
    """

    input_type = RunSpiceInput
    output_type = RunSpiceOutput

    async def validate_preconditions(self, input_data: RunSpiceInput) -> list[str]:
        """Check that the work_product exists and SPICE tool is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if not await self.context.mcp.is_available("spice.run_simulation"):
            errors.append("SPICE simulation tool is not available")

        return errors

    async def execute(self, input_data: RunSpiceInput) -> RunSpiceOutput:
        """Run SPICE simulation via MCP tool and return structured results."""
        self.logger.info(
            "Running SPICE simulation",
            work_product_id=input_data.work_product_id,
            netlist_path=input_data.netlist_path,
            analysis_type=input_data.analysis_type,
        )

        if input_data.analysis_type not in SUPPORTED_ANALYSIS_TYPES:
            raise ValueError(
                f"Unsupported analysis type '{input_data.analysis_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_ANALYSIS_TYPES))}"
            )

        # Invoke SPICE simulator via MCP
        arguments: dict[str, Any] = {
            "analysis_type": input_data.analysis_type,
            "params": input_data.params,
            "probes": input_data.probes,
        }
        if input_data.netlist:
            arguments["netlist"] = input_data.netlist
        else:
            arguments["netlist_path"] = input_data.netlist_path
        sim_result: dict[str, Any] = await self.context.mcp.invoke(
            "spice.run_simulation", arguments, timeout=180
        )

        return RunSpiceOutput(
            work_product_id=input_data.work_product_id,
            results=sim_result.get("results", {}),
            waveform_data=sim_result.get("waveform_data", {}),
            scale=sim_result.get("scale"),
            waveforms=sim_result.get("waveforms", []),
            convergence=bool(sim_result.get("convergence", False)),
            sim_time_s=float(sim_result.get("sim_time_s", 0.0)),
            log=str(sim_result.get("log", "")),
        )

    async def validate_output(self, output: RunSpiceOutput) -> list[str]:
        """Verify output consistency."""
        errors: list[str] = []
        if not output.convergence and not output.results:
            errors.append("Simulation did not converge and produced no results")
        return errors
