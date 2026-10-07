"""Standalone PydanticAI agent definition for simulation engineering.

Provides a self-contained Agent() instance with tool definitions that
delegate to existing skill handlers. This module can be used independently
of the SimulationAgent class in agent.py, or imported by it for the
LLM-driven execution path.

Usage::

    from domain_agents.simulation.pydantic_ai_agent import (
        create_simulation_agent,
        SimulationAgentDeps,
        run_agent,
    )

    deps = SimulationAgentDeps(twin=twin, mcp_bridge=mcp, session_id="s1")
    result = await run_agent("Run SPICE simulation on power supply", deps)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

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
tracer = get_tracer("domain_agents.simulation.pydantic_ai")

# ---------------------------------------------------------------------------
# Dependencies dataclass
# ---------------------------------------------------------------------------


@dataclass
class SimulationAgentDeps:
    """Dependencies injected into PydanticAI RunContext for the simulation agent."""

    twin: Any  # TwinAPI -- avoid circular import
    mcp_bridge: McpBridge
    session_id: str = ""
    branch: str = "main"


# ---------------------------------------------------------------------------
# Structured result model
# ---------------------------------------------------------------------------


class SimulationAgentResult(BaseModel):
    """Structured output from the simulation PydanticAI agent."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all simulations passed",
    )
    convergence_achieved: bool = Field(
        default=True,
        description="Whether all simulations converged",
    )
    work_products: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Artifacts produced or modified",
    )
    analysis: dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis report",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Engineering recommendations",
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Record of tool calls made during execution",
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
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


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_simulation_agent(
    model: str | Any = "test",
) -> Agent[SimulationAgentDeps, SimulationAgentResult]:
    """Create a PydanticAI Agent for simulation engineering.

    Args:
        model: PydanticAI model string (e.g. 'openai:gpt-4o') or model
            instance. Defaults to 'test' for deterministic testing.

    Returns:
        Configured Agent instance with simulation engineering tools.
    """
    agent: Agent[SimulationAgentDeps, SimulationAgentResult] = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        result_type=SimulationAgentResult,
        deps_type=SimulationAgentDeps,
    )

    # -- Tool: run_fea --------------------------------------------------------

    @agent.tool
    async def run_fea(
        ctx: RunContext[SimulationAgentDeps],
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
        with tracer.start_as_current_span("tool.run_fea") as span:
            span.set_attribute("mesh_file", mesh_file)
            span.set_attribute("analysis_type", analysis_type)
            logger.info("Running FEA", mesh_file=mesh_file, analysis_type=analysis_type)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = RunFeaInput(
                work_product_id=str(UUID(int=0)),
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
        ctx: RunContext[SimulationAgentDeps],
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
        with tracer.start_as_current_span("tool.run_spice") as span:
            span.set_attribute("netlist_path", netlist_path)
            span.set_attribute("analysis_type", analysis_type)
            logger.info("Running SPICE", netlist_path=netlist_path)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = RunSpiceInput(
                work_product_id=str(UUID(int=0)),
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
        ctx: RunContext[SimulationAgentDeps],
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
        with tracer.start_as_current_span("tool.run_cfd") as span:
            span.set_attribute("geometry_file", geometry_file or "")
            logger.info("Running thermal (conduction only)", geometry_file=geometry_file)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = RunCfdInput(
                work_product_id=str(UUID(int=0)),
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

    logger.debug("simulation_pydantic_ai_agent_created")
    return agent


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    deps: SimulationAgentDeps,
    *,
    model: str | Any = "test",
) -> dict[str, Any]:
    """Run the simulation PydanticAI agent with a natural-language prompt.

    Args:
        prompt: Natural-language description of the task.
        deps: Agent dependencies (twin, mcp_bridge, etc.).
        model: PydanticAI model string or instance.

    Returns:
        Dictionary with agent results including analysis, recommendations,
        and tool call records.
    """
    with tracer.start_as_current_span("simulation.run_agent") as span:
        span.set_attribute("prompt_length", len(prompt))
        logger.info("Running simulation agent", prompt_preview=prompt[:100])

        agent = create_simulation_agent(model=model)
        result = await agent.run(prompt, deps=deps)
        data: SimulationAgentResult = result.data

        logger.info(
            "Simulation agent completed",
            overall_passed=data.overall_passed,
            convergence_achieved=data.convergence_achieved,
        )

        return {
            "overall_passed": data.overall_passed,
            "convergence_achieved": data.convergence_achieved,
            "work_products": data.work_products,
            "analysis": data.analysis,
            "recommendations": data.recommendations,
            "tool_calls": data.tool_calls,
        }
