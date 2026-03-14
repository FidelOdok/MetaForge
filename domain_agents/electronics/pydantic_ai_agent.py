"""Standalone PydanticAI agent definition for electronics engineering.

Provides a self-contained Agent() instance with tool definitions that
delegate to existing skill handlers. This module can be used independently
of the ElectronicsAgent class in agent.py, or imported by it for the
LLM-driven execution path.

Usage::

    from domain_agents.electronics.pydantic_ai_agent import (
        create_electronics_agent,
        ElectronicsAgentDeps,
        run_agent,
    )

    deps = ElectronicsAgentDeps(twin=twin, mcp_bridge=mcp, session_id="s1")
    result = await run_agent("Run ERC on schematic", deps)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from domain_agents.electronics.skills.run_drc.handler import RunDrcHandler
from domain_agents.electronics.skills.run_drc.schema import RunDrcInput
from domain_agents.electronics.skills.run_erc.handler import RunErcHandler
from domain_agents.electronics.skills.run_erc.schema import RunErcInput
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.electronics.pydantic_ai")

# ---------------------------------------------------------------------------
# Dependencies dataclass
# ---------------------------------------------------------------------------


@dataclass
class ElectronicsAgentDeps:
    """Dependencies injected into PydanticAI RunContext for the electronics agent."""

    twin: Any  # TwinAPI -- avoid circular import
    mcp_bridge: McpBridge
    session_id: str = ""
    branch: str = "main"


# ---------------------------------------------------------------------------
# Structured result model
# ---------------------------------------------------------------------------


class ElectronicsAgentResult(BaseModel):
    """Structured output from the electronics PydanticAI agent."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all electronics checks passed",
    )
    total_erc_errors: int = Field(
        default=0,
        description="Total ERC errors found",
    )
    total_drc_errors: int = Field(
        default=0,
        description="Total DRC errors found",
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
You are an expert electronics engineer working within the MetaForge design \
validation platform. You have deep knowledge of PCB design, schematic review, \
power distribution networks, signal integrity, and EMC compliance.

You have access to the following tools:

- **run_erc**: Run Electrical Rules Check on a KiCad schematic file. Checks \
for unconnected pins, missing power flags, and other schematic errors.
- **run_drc**: Run Design Rules Check on a KiCad PCB layout file. Checks for \
clearance violations, unconnected nets, and manufacturing rule violations.
- **check_power_budget**: Analyze power consumption of all components against \
available power supply capacity.

Given a user request, determine which tools to call and in what order. \
Analyze the results and provide a clear assessment with pass/fail status \
and recommendations for fixing any violations.

Always validate that required parameters are available before calling a tool.
"""


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_electronics_agent(
    model: str | Any = "test",
) -> Agent[ElectronicsAgentDeps, ElectronicsAgentResult]:
    """Create a PydanticAI Agent for electronics engineering.

    Args:
        model: PydanticAI model string (e.g. 'openai:gpt-4o') or model
            instance. Defaults to 'test' for deterministic testing.

    Returns:
        Configured Agent instance with electronics engineering tools.
    """
    agent: Agent[ElectronicsAgentDeps, ElectronicsAgentResult] = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        result_type=ElectronicsAgentResult,
        deps_type=ElectronicsAgentDeps,
    )

    # -- Tool: run_erc --------------------------------------------------------

    @agent.tool
    async def run_erc(
        ctx: RunContext[ElectronicsAgentDeps],
        schematic_file: str,
        severity_filter: str = "all",
    ) -> dict[str, Any]:
        """Run Electrical Rules Check on a KiCad schematic.

        Args:
            schematic_file: Path to the KiCad schematic file (.kicad_sch).
            severity_filter: Filter by severity ('all', 'error', 'warning').
        """
        with tracer.start_as_current_span("tool.run_erc") as span:
            span.set_attribute("schematic_file", schematic_file)
            logger.info("Running ERC", schematic_file=schematic_file)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = RunErcInput(
                work_product_id=UUID(int=0),
                schematic_file=schematic_file,
                severity_filter=severity_filter,
            )

            handler = RunErcHandler(skill_ctx)
            result = await handler.run(skill_input)

            if not result.success:
                return {"skill": "run_erc", "success": False, "errors": result.errors}

            output = result.data
            return {
                "skill": "run_erc",
                "success": True,
                "passed": output.passed,
                "total_violations": output.total_violations,
                "total_errors": output.total_errors,
                "total_warnings": output.total_warnings,
                "summary": output.summary,
            }

    # -- Tool: run_drc --------------------------------------------------------

    @agent.tool
    async def run_drc(
        ctx: RunContext[ElectronicsAgentDeps],
        pcb_file: str,
        severity_filter: str = "all",
    ) -> dict[str, Any]:
        """Run Design Rules Check on a KiCad PCB layout.

        Args:
            pcb_file: Path to the KiCad PCB file (.kicad_pcb).
            severity_filter: Filter by severity ('all', 'error', 'warning').
        """
        with tracer.start_as_current_span("tool.run_drc") as span:
            span.set_attribute("pcb_file", pcb_file)
            logger.info("Running DRC", pcb_file=pcb_file)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = RunDrcInput(
                work_product_id=UUID(int=0),
                pcb_file=pcb_file,
                severity_filter=severity_filter,
            )

            handler = RunDrcHandler(skill_ctx)
            result = await handler.run(skill_input)

            if not result.success:
                return {"skill": "run_drc", "success": False, "errors": result.errors}

            output = result.data
            return {
                "skill": "run_drc",
                "success": True,
                "passed": output.passed,
                "total_violations": output.total_violations,
                "total_errors": output.total_errors,
                "total_warnings": output.total_warnings,
                "summary": output.summary,
            }

    # -- Tool: check_power_budget ---------------------------------------------

    @agent.tool
    async def check_power_budget(
        ctx: RunContext[ElectronicsAgentDeps],
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Analyze power budget for the design.

        Args:
            components: List of components with power ratings
                (each has 'name' and 'power_mw' fields).
        """
        with tracer.start_as_current_span("tool.check_power_budget") as span:
            span.set_attribute("num_components", len(components))
            logger.info("Checking power budget", num_components=len(components))

            return {
                "skill": "check_power_budget",
                "status": "not_implemented",
                "num_components": len(components),
                "error": "check_power_budget skill is not yet implemented",
            }

    logger.debug("electronics_pydantic_ai_agent_created")
    return agent


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    deps: ElectronicsAgentDeps,
    *,
    model: str | Any = "test",
) -> dict[str, Any]:
    """Run the electronics PydanticAI agent with a natural-language prompt.

    Args:
        prompt: Natural-language description of the task.
        deps: Agent dependencies (twin, mcp_bridge, etc.).
        model: PydanticAI model string or instance.

    Returns:
        Dictionary with agent results including analysis, recommendations,
        and tool call records.
    """
    with tracer.start_as_current_span("electronics.run_agent") as span:
        span.set_attribute("prompt_length", len(prompt))
        logger.info("Running electronics agent", prompt_preview=prompt[:100])

        agent = create_electronics_agent(model=model)
        result = await agent.run(prompt, deps=deps)
        data: ElectronicsAgentResult = result.data

        logger.info(
            "Electronics agent completed",
            overall_passed=data.overall_passed,
            num_recommendations=len(data.recommendations),
        )

        return {
            "overall_passed": data.overall_passed,
            "total_erc_errors": data.total_erc_errors,
            "total_drc_errors": data.total_drc_errors,
            "work_products": data.work_products,
            "analysis": data.analysis,
            "recommendations": data.recommendations,
            "tool_calls": data.tool_calls,
        }
