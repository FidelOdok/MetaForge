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

- **run_fea**: Run FEA structural analysis using CalculiX. Provide mesh_file, \
load_cases, analysis_type (static/modal/thermal), and material.
- **run_spice**: Run SPICE circuit simulation. Provide netlist_path, \
analysis_type (dc/ac/transient), and optional params.
- **run_cfd**: Run CFD thermal/flow analysis. Provide geometry_file, \
fluid_properties, boundary_conditions, and mesh_resolution.

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
        load_cases: list[dict[str, Any]] | None = None,
        analysis_type: str = "static",
        material: str = "steel_1018",
    ) -> dict[str, Any]:
        """Run FEA structural analysis using CalculiX.

        Args:
            mesh_file: Path to the mesh file (.inp format).
            load_cases: List of load case definitions.
            analysis_type: Type of analysis ('static', 'modal', 'thermal').
            material: Material identifier.
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
            load_cases=load_cases or [],
            analysis_type=analysis_type,
            material=material,
        )

        handler = RunFeaHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_fea", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_fea",
            "success": True,
            "max_stress_mpa": output.max_stress_mpa,
            "max_displacement_mm": output.max_displacement_mm,
            "safety_factor": output.safety_factor,
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
        geometry_file: str,
        fluid_properties: dict[str, Any] | None = None,
        boundary_conditions: dict[str, Any] | None = None,
        mesh_resolution: str = "medium",
    ) -> dict[str, Any]:
        """Run CFD thermal/flow analysis.

        Args:
            geometry_file: Path to the geometry file (.step, .stl).
            fluid_properties: Fluid properties (density, viscosity).
            boundary_conditions: Boundary conditions (inlet velocity, etc.).
            mesh_resolution: Mesh resolution ('coarse', 'medium', 'fine').
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
            mesh_resolution=mesh_resolution,
        )

        handler = RunCfdHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_cfd", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_cfd",
            "success": True,
            "max_velocity_ms": output.max_velocity_ms,
            "pressure_drop_pa": output.pressure_drop_pa,
            "max_temperature_c": output.max_temperature_c,
            "convergence_residual": output.convergence_residual,
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
        if not netlist_path:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: netlist_path"],
            )

        self.logger.info("SPICE simulation requested", netlist_path=netlist_path)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunSpiceInput(
            work_product_id=str(request.work_product_id),
            netlist_path=netlist_path,
            analysis_type=request.parameters.get("analysis_type", "dc"),
            params=request.parameters.get("params", {}),
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
                    "waveforms": output.waveforms,
                    "convergence": output.convergence,
                    "sim_time_s": output.sim_time_s,
                }
            ],
            warnings=[] if output.convergence else ["SPICE simulation did not converge"],
        )

    async def _run_fea(self, request: TaskRequest) -> TaskResult:
        """Run FEA structural analysis."""
        mesh_file: str = request.parameters.get("mesh_file", "")
        if not mesh_file:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: mesh_file"],
            )

        self.logger.info("FEA simulation requested", mesh_file=mesh_file)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunFeaInput(
            work_product_id=str(request.work_product_id),
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
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        passed = output.safety_factor >= 1.0
        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
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
                [f"Safety factor {output.safety_factor:.2f} is below 1.0"] if not passed else []
            ),
        )

    async def _run_cfd(self, request: TaskRequest) -> TaskResult:
        """Run CFD thermal/flow analysis."""
        geometry_file: str = request.parameters.get("geometry_file", "")
        if not geometry_file:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: geometry_file"],
            )

        self.logger.info("CFD simulation requested", geometry_file=geometry_file)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunCfdInput(
            work_product_id=str(request.work_product_id),
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
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        converged = output.convergence_residual < 1e-3
        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
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
                work_product_id=request.work_product_id,
                success=False,
                errors=[
                    "No simulations could be run. "
                    "Provide at least one of: netlist_path, mesh_file, geometry_file"
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
