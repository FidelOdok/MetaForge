"""Standalone PydanticAI agent definition for mechanical engineering.

Provides a self-contained Agent() instance with tool definitions that
delegate to existing skill handlers. This module can be used independently
of the MechanicalAgent class in agent.py, or imported by it for the
LLM-driven execution path.

Usage::

    from domain_agents.mechanical.pydantic_ai_agent import (
        create_mechanical_agent,
        MechanicalAgentDeps,
        run_agent,
    )

    deps = MechanicalAgentDeps(twin=twin, mcp_bridge=mcp, session_id="s1")
    result = await run_agent("Validate stress on bracket", deps)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from domain_agents.mechanical.skills.check_tolerance.handler import (
    CheckToleranceHandler,
)
from domain_agents.mechanical.skills.check_tolerance.schema import (
    CheckToleranceInput,
    ManufacturingProcess,
    ToleranceSpec,
)
from domain_agents.mechanical.skills.generate_cad.handler import (
    GenerateCadHandler,
)
from domain_agents.mechanical.skills.generate_cad.schema import (
    GenerateCadInput,
)
from domain_agents.mechanical.skills.generate_mesh.handler import (
    GenerateMeshHandler,
)
from domain_agents.mechanical.skills.generate_mesh.schema import (
    GenerateMeshInput,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.mechanical.pydantic_ai")

# ---------------------------------------------------------------------------
# Dependencies dataclass
# ---------------------------------------------------------------------------


@dataclass
class MechanicalAgentDeps:
    """Dependencies injected into PydanticAI RunContext for the mechanical agent."""

    twin: Any  # TwinAPI -- avoid circular import
    mcp_bridge: McpBridge
    session_id: str = ""
    branch: str = "main"


# ---------------------------------------------------------------------------
# Structured result model
# ---------------------------------------------------------------------------


class MechanicalAgentResult(BaseModel):
    """Structured output from the mechanical PydanticAI agent."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all mechanical checks passed",
    )
    max_stress_mpa: float = Field(
        default=0.0,
        description="Maximum stress found across all analyses",
    )
    critical_region: str = Field(
        default="",
        description="Region with the highest stress",
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
You are an expert mechanical engineer working within the MetaForge design \
validation platform. You have deep knowledge of structural mechanics, FEA, \
tolerance stack-up analysis, mesh generation, and manufacturing processes.

You have access to the following tools:

- **validate_stress**: Run FEA stress validation on a meshed CAD model using \
CalculiX. Provide mesh_file_path, load_case, and stress constraints.
- **generate_mesh**: Generate a finite element mesh from a CAD file using \
FreeCAD/Netgen. Provide cad_file path and meshing parameters.
- **check_tolerance**: Check dimensional tolerances against manufacturing \
process capabilities. Provide tolerance specs and manufacturing process details.
- **generate_cad**: Generate a CAD model from parametric specifications. \
Provide shape_type, dimensions, and material.

Given a user request, determine which tools to call and in what order. \
Analyze the results and provide a clear engineering assessment with pass/fail \
status, safety factors, and recommendations.

Always validate that required parameters are available before calling a tool. \
If parameters are missing, note what is needed in your recommendations.
"""


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_mechanical_agent(
    model: str | Any = "test",
) -> Agent[MechanicalAgentDeps, MechanicalAgentResult]:
    """Create a PydanticAI Agent for mechanical engineering.

    Args:
        model: PydanticAI model string (e.g. 'openai:gpt-4o') or model
            instance. Defaults to 'test' for deterministic testing.

    Returns:
        Configured Agent instance with mechanical engineering tools.
    """
    agent: Agent[MechanicalAgentDeps, MechanicalAgentResult] = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        result_type=MechanicalAgentResult,
        deps_type=MechanicalAgentDeps,
    )

    # -- Tool: validate_stress ------------------------------------------------

    @agent.tool
    async def validate_stress(
        ctx: RunContext[MechanicalAgentDeps],
        mesh_file_path: str,
        load_case: str,
        constraints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run FEA stress validation using CalculiX.

        Args:
            mesh_file_path: Path to the mesh file (.inp format).
            load_case: Load case identifier (e.g. 'gravity', 'thermal').
            constraints: List of stress constraints, each with
                max_von_mises_mpa (float) and safety_factor (float).
        """
        with tracer.start_as_current_span("tool.validate_stress") as span:
            span.set_attribute("mesh_file", mesh_file_path)
            span.set_attribute("load_case", load_case)
            logger.info(
                "Running stress validation",
                mesh_file=mesh_file_path,
                load_case=load_case,
            )

            fea_result = await ctx.deps.mcp_bridge.invoke(
                "calculix.run_fea",
                {
                    "mesh_file": mesh_file_path,
                    "load_case": load_case,
                    "analysis_type": "static_stress",
                },
            )

            stress_data: dict[str, Any] = fea_result.get("max_von_mises", {})
            results: list[dict[str, Any]] = []
            all_passed = True

            for constraint in constraints:
                max_allowable = float(constraint.get("max_von_mises_mpa", float("inf")))
                safety_factor = float(constraint.get("safety_factor", 1.5))
                allowable = max_allowable / safety_factor

                for region, stress_val in stress_data.items():
                    passed = float(stress_val) <= allowable
                    if not passed:
                        all_passed = False
                    results.append(
                        {
                            "region": region,
                            "stress_mpa": float(stress_val),
                            "allowable_mpa": allowable,
                            "passed": passed,
                        }
                    )

            return {
                "skill": "validate_stress",
                "fea_result": fea_result,
                "constraint_results": results,
                "overall_passed": all_passed,
            }

    # -- Tool: generate_mesh --------------------------------------------------

    @agent.tool
    async def generate_mesh(
        ctx: RunContext[MechanicalAgentDeps],
        cad_file: str,
        element_size: float = 1.0,
        algorithm: str = "netgen",
    ) -> dict[str, Any]:
        """Generate a finite element mesh from a CAD file.

        Args:
            cad_file: Path to the CAD file (.step, .brep, etc.).
            element_size: Target element size in mm.
            algorithm: Meshing algorithm ('netgen', 'gmsh').
        """
        with tracer.start_as_current_span("tool.generate_mesh") as span:
            span.set_attribute("cad_file", cad_file)
            logger.info("Generating mesh", cad_file=cad_file)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = GenerateMeshInput(
                work_product_id=UUID(int=0),
                cad_file=cad_file,
                element_size=element_size,
                algorithm=algorithm,
            )

            handler = GenerateMeshHandler(skill_ctx)
            result = await handler.run(skill_input)

            if not result.success:
                return {"skill": "generate_mesh", "success": False, "errors": result.errors}

            output = result.data
            return {
                "skill": "generate_mesh",
                "success": True,
                "mesh_file": output.mesh_file,
                "num_nodes": output.num_nodes,
                "num_elements": output.num_elements,
                "quality_acceptable": output.quality_acceptable,
            }

    # -- Tool: check_tolerance ------------------------------------------------

    @agent.tool
    async def check_tolerance(
        ctx: RunContext[MechanicalAgentDeps],
        tolerances: list[dict[str, Any]],
        manufacturing_process: dict[str, Any],
        material: str = "aluminum_6061",
    ) -> dict[str, Any]:
        """Check dimensional tolerances against manufacturing process capabilities.

        Args:
            tolerances: List of tolerance specifications.
            manufacturing_process: Manufacturing process details.
            material: Material identifier.
        """
        with tracer.start_as_current_span("tool.check_tolerance") as span:
            span.set_attribute("material", material)
            logger.info("Checking tolerances", material=material)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            tol_specs = [ToleranceSpec.model_validate(t) for t in tolerances]
            process = ManufacturingProcess.model_validate(manufacturing_process)

            skill_input = CheckToleranceInput(
                work_product_id=UUID(int=0),
                tolerances=tol_specs,
                manufacturing_process=process,
                material=material,
            )

            handler = CheckToleranceHandler(skill_ctx)
            result = await handler.run(skill_input)

            if not result.success:
                return {"skill": "check_tolerance", "success": False, "errors": result.errors}

            output = result.data
            return {
                "skill": "check_tolerance",
                "success": True,
                "overall_status": output.overall_status,
                "total_dimensions_checked": output.total_dimensions_checked,
                "summary": output.summary,
            }

    # -- Tool: generate_cad ---------------------------------------------------

    @agent.tool
    async def generate_cad(
        ctx: RunContext[MechanicalAgentDeps],
        shape_type: str,
        dimensions: dict[str, Any],
        material: str = "aluminum_6061",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Generate a CAD model from parametric specifications.

        Args:
            shape_type: Type of shape to generate (e.g. 'box', 'cylinder').
            dimensions: Dimension parameters for the shape.
            material: Material identifier.
            output_path: Output file path for the CAD file.
        """
        with tracer.start_as_current_span("tool.generate_cad") as span:
            span.set_attribute("shape_type", shape_type)
            logger.info("Generating CAD", shape_type=shape_type)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = GenerateCadInput(
                work_product_id=UUID(int=0),
                shape_type=shape_type,
                dimensions=dimensions,
                material=material,
                output_path=output_path,
            )

            handler = GenerateCadHandler(skill_ctx)
            result = await handler.run(skill_input)

            if not result.success:
                return {"skill": "generate_cad", "success": False, "errors": result.errors}

            output = result.data
            return {
                "skill": "generate_cad",
                "success": True,
                "cad_file": output.cad_file,
                "shape_type": output.shape_type,
                "volume_mm3": output.volume_mm3,
                "surface_area_mm2": output.surface_area_mm2,
            }

    logger.debug("mechanical_pydantic_ai_agent_created")
    return agent


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    deps: MechanicalAgentDeps,
    *,
    model: str | Any = "test",
) -> dict[str, Any]:
    """Run the mechanical PydanticAI agent with a natural-language prompt.

    Args:
        prompt: Natural-language description of the task.
        deps: Agent dependencies (twin, mcp_bridge, etc.).
        model: PydanticAI model string or instance.

    Returns:
        Dictionary with agent results including analysis, recommendations,
        and tool call records.
    """
    with tracer.start_as_current_span("mechanical.run_agent") as span:
        span.set_attribute("prompt_length", len(prompt))
        logger.info("Running mechanical agent", prompt_preview=prompt[:100])

        agent = create_mechanical_agent(model=model)
        result = await agent.run(prompt, deps=deps)
        data: MechanicalAgentResult = result.data

        logger.info(
            "Mechanical agent completed",
            overall_passed=data.overall_passed,
            num_recommendations=len(data.recommendations),
        )

        return {
            "overall_passed": data.overall_passed,
            "max_stress_mpa": data.max_stress_mpa,
            "critical_region": data.critical_region,
            "work_products": data.work_products,
            "analysis": data.analysis,
            "recommendations": data.recommendations,
            "tool_calls": data.tool_calls,
        }
