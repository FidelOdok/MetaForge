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
        with tracer.start_as_current_span("tool.run_cfd") as span:
            span.set_attribute("geometry_file", geometry_file)
            logger.info("Running CFD", geometry_file=geometry_file)

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
