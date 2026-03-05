"""Simulation engineering domain agent.

Orchestrates skill execution for simulation and validation:
SPICE circuit simulation, FEA structural analysis, and CFD thermal/flow analysis.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel

from domain_agents.simulation.skills.run_cfd.handler import RunCfdHandler
from domain_agents.simulation.skills.run_cfd.schema import RunCfdInput
from domain_agents.simulation.skills.run_fea.handler import RunFeaHandler
from domain_agents.simulation.skills.run_fea.schema import RunFeaInput
from domain_agents.simulation.skills.run_spice.handler import RunSpiceHandler
from domain_agents.simulation.skills.run_spice.schema import RunSpiceInput
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger()


class TaskRequest(BaseModel):
    """A request for the simulation agent to perform a task."""

    task_type: str  # "run_spice", "run_fea", "run_cfd", "full_simulation"
    artifact_id: UUID
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of a simulation agent task."""

    task_type: str
    artifact_id: UUID
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


class SimulationAgent:
    """Simulation engineering domain agent.

    Orchestrates skill execution for simulation and validation:
    SPICE circuit simulation, FEA structural analysis, and CFD flow analysis.

    The agent is stateless -- all state lives in the Digital Twin.
    Skills invoke external tools via MCP bridge.

    Usage:
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = SimulationAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(TaskRequest(
            task_type="run_spice",
            artifact_id=artifact.id,
            parameters={"netlist_path": "sim/power_supply.cir", ...},
        ))
    """

    SUPPORTED_TASKS = {"run_spice", "run_fea", "run_cfd", "full_simulation"}

    def __init__(
        self,
        twin: Any,  # TwinAPI -- avoid circular import at module level
        mcp: McpBridge,
        session_id: UUID | None = None,
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.session_id = session_id or uuid4()
        self.logger = logger.bind(agent="simulation", session_id=str(self.session_id))

    async def run_task(self, request: TaskRequest) -> TaskResult:
        """Execute a simulation task.

        Routes to the appropriate handler based on task_type.
        """
        self.logger.info(
            "Running task",
            task_type=request.task_type,
            artifact_id=str(request.artifact_id),
        )

        if request.task_type not in self.SUPPORTED_TASKS:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    f"Unsupported task type: {request.task_type}. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                ],
            )

        # Verify artifact exists
        artifact = await self.twin.get_artifact(request.artifact_id, branch=request.branch)
        if artifact is None:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    f"Artifact {request.artifact_id} not found on branch '{request.branch}'"
                ],
            )

        # Route to handler
        handler = self._get_handler(request.task_type)
        return await handler(request)

    def _get_handler(
        self, task_type: str
    ) -> Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]:
        """Return the handler coroutine function for the given task type."""
        handlers: dict[str, Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]] = {
            "run_spice": self._run_spice,
            "run_fea": self._run_fea,
            "run_cfd": self._run_cfd,
            "full_simulation": self._run_full_simulation,
        }
        return handlers[task_type]

    async def _run_spice(self, request: TaskRequest) -> TaskResult:
        """Run SPICE circuit simulation.

        Requires 'netlist_path' in request.parameters.
        Delegates to the RunSpiceHandler skill via MCP bridge.
        """
        netlist_path: str = request.parameters.get("netlist_path", "")
        if not netlist_path:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: netlist_path"],
            )

        self.logger.info("SPICE simulation requested", netlist_path=netlist_path)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunSpiceInput(
            artifact_id=str(request.artifact_id),
            netlist_path=netlist_path,
            analysis_type=request.parameters.get("analysis_type", "dc"),
            params=request.parameters.get("params", {}),
        )

        handler = RunSpiceHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=output.convergence,
            skill_results=[
                {
                    "skill": "run_spice",
                    "results": output.results,
                    "waveforms": output.waveforms,
                    "convergence": output.convergence,
                    "sim_time_s": output.sim_time_s,
                }
            ],
            warnings=[] if output.convergence else ["SPICE simulation did not converge"],
        )

    async def _run_fea(self, request: TaskRequest) -> TaskResult:
        """Run FEA structural analysis.

        Requires 'mesh_file' in request.parameters.
        Delegates to the RunFeaHandler skill via MCP bridge.
        """
        mesh_file: str = request.parameters.get("mesh_file", "")
        if not mesh_file:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: mesh_file"],
            )

        self.logger.info("FEA simulation requested", mesh_file=mesh_file)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunFeaInput(
            artifact_id=str(request.artifact_id),
            mesh_file=mesh_file,
            load_cases=request.parameters.get("load_cases", []),
            analysis_type=request.parameters.get("analysis_type", "static"),
            material=request.parameters.get("material", "steel_1018"),
        )

        handler = RunFeaHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        passed = output.safety_factor >= 1.0
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=passed,
            skill_results=[
                {
                    "skill": "run_fea",
                    "max_stress_mpa": output.max_stress_mpa,
                    "max_displacement_mm": output.max_displacement_mm,
                    "safety_factor": output.safety_factor,
                    "solver_time_s": output.solver_time_s,
                }
            ],
            warnings=(
                [f"Safety factor {output.safety_factor:.2f} is below 1.0"]
                if not passed
                else []
            ),
        )

    async def _run_cfd(self, request: TaskRequest) -> TaskResult:
        """Run CFD thermal/flow analysis.

        Requires 'geometry_file' in request.parameters.
        Delegates to the RunCfdHandler skill via MCP bridge.
        """
        geometry_file: str = request.parameters.get("geometry_file", "")
        if not geometry_file:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: geometry_file"],
            )

        self.logger.info("CFD simulation requested", geometry_file=geometry_file)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunCfdInput(
            artifact_id=str(request.artifact_id),
            geometry_file=geometry_file,
            fluid_properties=request.parameters.get("fluid_properties", {}),
            boundary_conditions=request.parameters.get("boundary_conditions", {}),
            mesh_resolution=request.parameters.get("mesh_resolution", "medium"),
        )

        handler = RunCfdHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        converged = output.convergence_residual < 1e-3
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=converged,
            skill_results=[
                {
                    "skill": "run_cfd",
                    "max_velocity_ms": output.max_velocity_ms,
                    "pressure_drop_pa": output.pressure_drop_pa,
                    "max_temperature_c": output.max_temperature_c,
                    "convergence_residual": output.convergence_residual,
                }
            ],
            warnings=(
                [f"CFD residual {output.convergence_residual:.2e} exceeds threshold"]
                if not converged
                else []
            ),
        )

    async def _run_full_simulation(self, request: TaskRequest) -> TaskResult:
        """Run all applicable simulations and aggregate results.

        Runs SPICE, FEA, and/or CFD based on available parameters.
        """
        all_results: list[dict[str, Any]] = []
        all_errors: list[str] = []
        all_warnings: list[str] = []
        overall_success = True
        sims_run = 0

        # Run SPICE if netlist_path is provided
        if request.parameters.get("netlist_path"):
            spice_result = await self._run_spice(request)
            all_results.extend(spice_result.skill_results)
            all_errors.extend(spice_result.errors)
            all_warnings.extend(spice_result.warnings)
            if not spice_result.success:
                overall_success = False
            sims_run += 1

        # Run FEA if mesh_file is provided
        if request.parameters.get("mesh_file"):
            fea_result = await self._run_fea(request)
            all_results.extend(fea_result.skill_results)
            all_errors.extend(fea_result.errors)
            all_warnings.extend(fea_result.warnings)
            if not fea_result.success:
                overall_success = False
            sims_run += 1

        # Run CFD if geometry_file is provided
        if request.parameters.get("geometry_file"):
            cfd_result = await self._run_cfd(request)
            all_results.extend(cfd_result.skill_results)
            all_errors.extend(cfd_result.errors)
            all_warnings.extend(cfd_result.warnings)
            if not cfd_result.success:
                overall_success = False
            sims_run += 1

        if sims_run == 0:
            return TaskResult(
                task_type="full_simulation",
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    "No simulations could be run. "
                    "Provide at least one of: netlist_path, mesh_file, geometry_file"
                ],
            )

        return TaskResult(
            task_type="full_simulation",
            artifact_id=request.artifact_id,
            success=overall_success,
            skill_results=all_results,
            errors=all_errors,
            warnings=all_warnings,
        )

    def _create_skill_context(self, branch: str = "main") -> SkillContext:
        """Create a SkillContext for skill execution."""
        return SkillContext(
            twin=self.twin,
            mcp=self.mcp,
            logger=self.logger,
            session_id=self.session_id,
            branch=branch,
        )
