"""Simulation engineering domain agent.

Orchestrates skill execution for simulation and validation:
SPICE circuit simulation, FEA structural analysis, and CFD thermal/flow analysis.

Supports two modes:
- **LLM mode**: PydanticAI Agent() with LLM-driven tool selection
- **Hardcoded mode**: Deterministic dispatch by task_type (fallback)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

try:
    from pydantic_ai import RunContext
except ImportError:
    RunContext = None  # type: ignore[assignment,misc]

import structlog
from pydantic import BaseModel, Field

from domain_agents.base_agent import (
    AgentDependencies,
    AgentResult,
    get_llm_model,
    is_llm_available,
)
from domain_agents.simulation.skills.run_cfd.handler import RunCfdHandler
from domain_agents.simulation.skills.run_cfd.schema import RunCfdInput
from domain_agents.simulation.skills.run_fea.handler import RunFeaHandler
from domain_agents.simulation.skills.run_fea.schema import RunFeaInput
from domain_agents.simulation.skills.run_spice.handler import RunSpiceHandler
from domain_agents.simulation.skills.run_spice.schema import RunSpiceInput
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.simulation")


# ---------------------------------------------------------------------------
# Domain-specific result model for PydanticAI structured output
# ---------------------------------------------------------------------------


class SimulationResult(AgentResult):
    """Structured output from the simulation agent's PydanticAI run."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all simulations passed",
    )
    convergence_achieved: bool = Field(
        default=True,
        description="Whether all simulations converged",
    )


# ---------------------------------------------------------------------------
# Backward-compatible request/result models
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    """A request for the simulation agent to perform a task."""

    task_type: str  # "run_spice", "run_fea", "run_cfd", "full_simulation"
    work_product_id: UUID
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of a simulation agent task."""

    task_type: str
    work_product_id: UUID
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# PydanticAI agent factory (lazy, created once per process)
# ---------------------------------------------------------------------------

_pydantic_agent: Any | None = None

SIMULATION_SYSTEM_PROMPT = """\
You are an expert simulation engineer working within the MetaForge design \
validation platform. You have deep knowledge of circuit simulation (SPICE), \
finite element analysis (CalculiX), computational fluid dynamics, and \
multi-physics simulation.

You have access to the following tools:

- **run_fea**: Run FEA with CalculiX. Provide mesh_file, load_case, material, \
fixed_node_set, and for static_stress load_node_set and load_force_n [Fx, Fy, Fz] \
(N); modal takes num_modes. Give a cited yield_strength_mpa for a safety factor.
- **run_spice**: Run SPICE circuit simulation. Provide netlist_path, \
analysis_type (dc/ac/transient), and optional params.
- **run_cfd**: Steady conduction to a fixed-temperature sink (CalculiX). \
Provide conduction (mesh_file, material, heat_source_node_set, \
power_dissipation_w, sink_node_set, sink_temp_c). There is no flow solver: \
velocity, pressure drop and convection cannot be computed.

Given a user request, determine which simulation tools to run. \
Analyze convergence, safety factors, and key results. Provide clear \
engineering recommendations based on simulation outcomes.
"""


def _get_or_create_pydantic_agent() -> Any:
    """Lazily create the PydanticAI Agent for simulation engineering."""
    global _pydantic_agent
    if _pydantic_agent is not None:
        return _pydantic_agent

    try:
        from pydantic_ai import Agent
    except ImportError:
        logger.warning("pydantic_ai_not_installed")
        return None

    model = get_llm_model()
    if model is None:
        return None

    agent = Agent(
        model,
        system_prompt=SIMULATION_SYSTEM_PROMPT,
        output_type=SimulationResult,
        deps_type=AgentDependencies,
    )

    # -- Tool: run_fea --------------------------------------------------------

    @agent.tool
    async def run_fea(
        ctx: RunContext[AgentDependencies],
        mesh_file: str,
        load_case: str,
        material: dict[str, Any],
        fixed_node_set: str,
        analysis_type: str = "static_stress",
        load_node_set: str | None = None,
        load_force_n: list[float] | None = None,
        num_modes: int = 3,
        yield_strength_mpa: float | None = None,
    ) -> dict[str, Any]:
        """Run FEA with CalculiX: calculix.run_fea's own arguments (FORGE-561).

        Args:
            mesh_file: Volume mesh (.inp), e.g. from generate_mesh.
            load_case: Name of the load case.
            material: {'name': ...} or explicit youngs_modulus_mpa/poissons_ratio.
            fixed_node_set: Face held fixed (a surface set from generate_mesh).
            analysis_type: 'static_stress' or 'modal'.
            load_node_set: Loaded face (static_stress).
            load_force_n: [Fx, Fy, Fz] in N (static_stress).
            num_modes: Modes to extract (modal).
            yield_strength_mpa: Cited yield strength; gives a safety factor.
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = RunFeaInput(
            work_product_id=str(UUID("00000000-0000-0000-0000-000000000000")),
            mesh_file=mesh_file,
            load_case=load_case,
            analysis_type=analysis_type,
            material=material,
            fixed_node_set=fixed_node_set,
            load_node_set=load_node_set,
            load_force_n=load_force_n,
            num_modes=num_modes,
            yield_strength_mpa=yield_strength_mpa,
        )

        handler = RunFeaHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_fea", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_fea",
            "success": True,
            "analysis_type": output.analysis_type,
            "max_stress_mpa": output.max_stress_mpa,
            "max_displacement_mm": output.max_displacement_mm,
            "safety_factor": output.safety_factor,
            "frequencies_hz": output.frequencies_hz,
            "frd_path": output.frd_path,
            "solver_time_s": output.solver_time_s,
        }

    # -- Tool: run_spice ------------------------------------------------------

    @agent.tool
    async def run_spice(
        ctx: RunContext[AgentDependencies],
        netlist_path: str,
        analysis_type: str = "dc",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run SPICE circuit simulation.

        Args:
            netlist_path: Path to the SPICE netlist file (.cir).
            analysis_type: Type of analysis ('dc', 'ac', 'transient').
            params: Additional simulation parameters.
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = RunSpiceInput(
            work_product_id=str(UUID("00000000-0000-0000-0000-000000000000")),
            netlist_path=netlist_path,
            analysis_type=analysis_type,
            params=params or {},
        )

        handler = RunSpiceHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_spice", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_spice",
            "success": True,
            "results": output.results,
            "waveforms": output.waveforms,
            "convergence": output.convergence,
            "sim_time_s": output.sim_time_s,
        }

    # -- Tool: run_cfd --------------------------------------------------------

    @agent.tool
    async def run_cfd(
        ctx: RunContext[AgentDependencies],
        conduction: dict[str, Any] | None = None,
        geometry_file: str | None = None,
        fluid_properties: dict[str, Any] | None = None,
        boundary_conditions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Steady conduction to a fixed-temperature sink; flow is refused (FORGE-543).

        Args:
            conduction: calculix.run_thermal's case: mesh_file, material,
                heat_source_node_set, power_dissipation_w, sink_node_set, sink_temp_c.
            geometry_file: Geometry the case came from, for the record.
            fluid_properties: Flow input; refused, there is no flow solver.
            boundary_conditions: Flow input; refused, there is no flow solver.
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = RunCfdInput(
            work_product_id=str(UUID("00000000-0000-0000-0000-000000000000")),
            geometry_file=geometry_file,
            fluid_properties=fluid_properties or {},
            boundary_conditions=boundary_conditions or {},
            conduction=conduction,
        )

        handler = RunCfdHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_cfd", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_cfd",
            "success": True,
            "analysis": output.analysis,
            "max_temperature_c": output.max_temperature_c,
            "min_temperature_c": output.min_temperature_c,
            "warnings": output.warnings,
        }

    _pydantic_agent = agent
    return agent


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class SimulationAgent:
    """Simulation engineering domain agent.

    Orchestrates skill execution for simulation and validation:
    SPICE circuit simulation, FEA structural analysis, and CFD flow analysis.

    Supports two execution modes:
    - PydanticAI mode: LLM-driven tool selection (when METAFORGE_LLM_PROVIDER is set)
    - Hardcoded mode: Deterministic dispatch by task_type (fallback)

    The agent is stateless -- all state lives in the Digital Twin.
    Skills invoke external tools via MCP bridge.

    Usage:
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = SimulationAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(TaskRequest(
            task_type="run_spice",
            work_product_id=work_product.id,
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

        If an LLM is configured, attempts PydanticAI-driven execution.
        Falls back to hardcoded dispatch on LLM unavailability or error.
        """
        with tracer.start_as_current_span("agent.execute") as span:
            span.set_attribute("agent.code", "simulation")
            span.set_attribute("session.id", str(self.session_id))
            span.set_attribute("task.type", request.task_type)

            self.logger.info(
                "Running task",
                task_type=request.task_type,
                work_product_id=str(request.work_product_id),
            )

            # Try PydanticAI path if LLM is available
            if is_llm_available() and request.task_type in self.SUPPORTED_TASKS:
                try:
                    result = await self._run_with_llm(request)
                    span.set_attribute("agent.mode", "llm")
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    self.logger.warning(
                        "LLM execution failed, falling back to hardcoded dispatch",
                        error=str(exc),
                    )

            # Hardcoded dispatch (fallback)
            span.set_attribute("agent.mode", "hardcoded")
            return await self._run_hardcoded(request)

    async def _run_with_llm(self, request: TaskRequest) -> TaskResult:
        """Execute a task using PydanticAI agent with LLM reasoning."""
        agent = _get_or_create_pydantic_agent()
        if agent is None:
            raise RuntimeError("PydanticAI agent could not be created")

        # Verify work_product exists first
        work_product = await self.twin.get_work_product(
            request.work_product_id, branch=request.branch
        )
        if work_product is None:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[
                    f"WorkProduct {request.work_product_id} not found on branch '{request.branch}'"
                ],
            )

        deps = AgentDependencies(
            twin=self.twin,
            mcp_bridge=self.mcp,
            session_id=str(self.session_id),
            branch=request.branch,
        )

        prompt = self._build_prompt(request)

        t0 = time.monotonic()
        result = await agent.run(prompt, deps=deps)
        elapsed = time.monotonic() - t0

        self.logger.info(
            "LLM execution completed",
            task_type=request.task_type,
            elapsed_s=round(elapsed, 3),
        )

        simulation_result: SimulationResult = result.output

        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=simulation_result.overall_passed,
            skill_results=simulation_result.tool_calls
            if simulation_result.tool_calls
            else [simulation_result.analysis],
            warnings=(
                simulation_result.recommendations if not simulation_result.overall_passed else []
            ),
        )

    def _build_prompt(self, request: TaskRequest) -> str:
        """Build a natural language prompt from a structured TaskRequest."""
        parts = [
            f"Perform a '{request.task_type}' simulation on work_product {request.work_product_id}."
        ]
        if request.parameters:
            parts.append(f"Parameters: {request.parameters}")
        return " ".join(parts)

    # --- Hardcoded dispatch (original implementation) ---

    async def _run_hardcoded(self, request: TaskRequest) -> TaskResult:
        """Original hardcoded dispatch path."""
        if request.task_type not in self.SUPPORTED_TASKS:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[
                    f"Unsupported task type: {request.task_type}. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                ],
            )

        # Verify work_product exists
        work_product = await self.twin.get_work_product(
            request.work_product_id, branch=request.branch
        )
        if work_product is None:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[
                    f"WorkProduct {request.work_product_id} not found on branch '{request.branch}'"
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
        """Run SPICE circuit simulation."""
        netlist_path: str = request.parameters.get("netlist_path", "")
        netlist: str = request.parameters.get("netlist", "")
        if not netlist_path and not netlist:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: netlist_path (or netlist)"],
            )

        self.logger.info("SPICE simulation requested", netlist_path=netlist_path)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunSpiceInput(
            work_product_id=str(request.work_product_id),
            netlist_path=netlist_path or None,
            netlist=netlist or None,
            analysis_type=request.parameters.get("analysis_type", "dc"),
            params=request.parameters.get("params", {}),
            probes=request.parameters.get("probes", []),
        )

        handler = RunSpiceHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=output.convergence,
            skill_results=[
                {
                    "skill": "run_spice",
                    "results": output.results,
                    "waveform_data": output.waveform_data,
                    "scale": output.scale,
                    "waveforms": output.waveforms,
                    "convergence": output.convergence,
                    "sim_time_s": output.sim_time_s,
                }
            ],
            warnings=[]
            if output.convergence
            else [f"SPICE simulation did not converge: {output.log}".rstrip(": ")],
        )

    async def _run_fea(self, request: TaskRequest) -> TaskResult:
        """Run FEA with calculix.run_fea's own arguments (FORGE-561)."""
        params = request.parameters
        if not params.get("mesh_file"):
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: mesh_file"],
            )

        self.logger.info("FEA simulation requested", mesh_file=params.get("mesh_file"))

        ctx = self._create_skill_context(request.branch)
        try:
            skill_input = RunFeaInput(
                work_product_id=str(request.work_product_id),
                mesh_file=params["mesh_file"],
                load_case=params.get("load_case", ""),
                analysis_type=params.get("analysis_type", "static_stress"),
                material=params.get("material") or {},
                fixed_node_set=params.get("fixed_node_set", ""),
                load_node_set=params.get("load_node_set"),
                load_force_n=params.get("load_force_n"),
                num_modes=params.get("num_modes", 3),
                yield_strength_mpa=params.get("yield_strength_mpa"),
            )
        except ValueError as exc:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[str(exc)],
            )

        handler = RunFeaHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        warnings: list[str] = []
        passed = True
        if output.safety_factor is not None and output.safety_factor < 1.0:
            passed = False
            warnings.append(f"Safety factor {output.safety_factor:.2f} is below 1.0")
        if output.analysis_type == "static_stress" and output.safety_factor is None:
            warnings.append("No safety factor: give a cited yield_strength_mpa")
        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=passed,
            skill_results=[{"skill": "run_fea", **output.model_dump(mode="json")}],
            warnings=warnings,
        )

    async def _run_cfd(self, request: TaskRequest) -> TaskResult:
        """Steady conduction to a fixed-temperature sink; flow is refused (FORGE-543)."""
        conduction = request.parameters.get("conduction")
        self.logger.info(
            "Thermal (conduction only) requested",
            geometry_file=request.parameters.get("geometry_file"),
            conduction=conduction is not None,
        )

        ctx = self._create_skill_context(request.branch)
        try:
            skill_input = RunCfdInput(
                work_product_id=str(request.work_product_id),
                geometry_file=request.parameters.get("geometry_file"),
                fluid_properties=request.parameters.get("fluid_properties", {}),
                boundary_conditions=request.parameters.get("boundary_conditions", {}),
                conduction=conduction,
            )
        except ValueError as exc:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[str(exc)],
            )

        handler = RunCfdHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=True,
            skill_results=[
                {
                    "skill": "run_cfd",
                    "analysis": output.analysis,
                    "max_temperature_c": output.max_temperature_c,
                    "min_temperature_c": output.min_temperature_c,
                    "solver": output.solver,
                }
            ],
            warnings=output.warnings,
        )

    async def _run_full_simulation(self, request: TaskRequest) -> TaskResult:
        """Run all applicable simulations and aggregate results."""
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

        # Run the thermal case if one is given (flow requests are refused inside)
        if request.parameters.get("conduction") or request.parameters.get("geometry_file"):
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
                work_product_id=request.work_product_id,
                success=False,
                errors=[
                    "No simulations could be run. "
                    "Provide at least one of: netlist_path, mesh_file, conduction"
                ],
            )

        return TaskResult(
            task_type="full_simulation",
            work_product_id=request.work_product_id,
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
