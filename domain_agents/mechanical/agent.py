"""Mechanical engineering domain agent.

Orchestrates skill execution for mechanical design validation:
stress analysis, tolerance checking, and mesh generation.

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
from domain_agents.mechanical.writeback import (
    writeback_cad,
    writeback_mesh,
    writeback_stress,
    writeback_tolerance,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.mechanical")


# ---------------------------------------------------------------------------
# Domain-specific result model for PydanticAI structured output
# ---------------------------------------------------------------------------


class MechanicalResult(AgentResult):
    """Structured output from the mechanical agent's PydanticAI run."""

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


# ---------------------------------------------------------------------------
# Backward-compatible request/result models
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    """A request for the mechanical agent to perform a task."""

    task_type: str  # "validate_stress", "check_tolerances", "generate_mesh", "full_validation"
    work_product_id: UUID
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of a mechanical agent task."""

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

MECHANICAL_SYSTEM_PROMPT = """\
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

Given a user request, determine which tools to call and in what order. \
Analyze the results and provide a clear engineering assessment with pass/fail \
status, safety factors, and recommendations.

Always validate that required parameters are available before calling a tool. \
If parameters are missing, note what is needed in your recommendations.
"""


def _get_or_create_pydantic_agent() -> Any:
    """Lazily create the PydanticAI Agent for mechanical engineering."""
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
        system_prompt=MECHANICAL_SYSTEM_PROMPT,
        output_type=MechanicalResult,
        deps_type=AgentDependencies,
    )

    # -- Tool: validate_stress ------------------------------------------------

    @agent.tool
    async def validate_stress(
        ctx: RunContext[AgentDependencies],
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
        ctx: RunContext[AgentDependencies],
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
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = GenerateMeshInput(
            work_product_id=UUID("00000000-0000-0000-0000-000000000000"),
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
        ctx: RunContext[AgentDependencies],
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
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        tol_specs = [ToleranceSpec.model_validate(t) for t in tolerances]
        process = ManufacturingProcess.model_validate(manufacturing_process)

        skill_input = CheckToleranceInput(
            work_product_id=UUID("00000000-0000-0000-0000-000000000000"),
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

    _pydantic_agent = agent
    return agent


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class MechanicalAgent:
    """Mechanical engineering domain agent.

    Orchestrates skill execution for mechanical design validation.
    The agent is stateless -- all state lives in the Digital Twin.

    Supports two execution modes:
    - PydanticAI mode: LLM-driven tool selection (when METAFORGE_LLM_PROVIDER is set)
    - Hardcoded mode: Deterministic dispatch by task_type (fallback)

    Usage:
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(TaskRequest(
            task_type="validate_stress",
            work_product_id=work_product.id,
            parameters={"mesh_file_path": "mesh/bracket.inp", ...},
        ))
    """

    SUPPORTED_TASKS = {
        "validate_stress",
        "check_tolerances",
        "generate_mesh",
        "generate_cad",
        "full_validation",
    }

    def __init__(
        self,
        twin: Any,  # TwinAPI -- avoid circular import at module level
        mcp: McpBridge,
        session_id: UUID | None = None,
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.session_id = session_id or uuid4()
        self.logger = logger.bind(agent="mechanical", session_id=str(self.session_id))

    async def run_task(self, request: TaskRequest) -> TaskResult:
        """Execute a mechanical engineering task.

        If an LLM is configured, attempts PydanticAI-driven execution.
        Falls back to hardcoded dispatch on LLM unavailability or error.
        """
        with tracer.start_as_current_span("agent.execute") as span:
            span.set_attribute("agent.code", "mechanical")
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

        # Build a natural language prompt from the task request
        prompt = self._build_prompt(request)

        t0 = time.monotonic()
        result = await agent.run(prompt, deps=deps)
        elapsed = time.monotonic() - t0

        self.logger.info(
            "LLM execution completed",
            task_type=request.task_type,
            elapsed_s=round(elapsed, 3),
        )

        mechanical_result: MechanicalResult = result.output

        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=mechanical_result.overall_passed,
            skill_results=mechanical_result.tool_calls
            if mechanical_result.tool_calls
            else [mechanical_result.analysis],
            warnings=(
                mechanical_result.recommendations if not mechanical_result.overall_passed else []
            ),
        )

    def _build_prompt(self, request: TaskRequest) -> str:
        """Build a natural language prompt from a structured TaskRequest."""
        parts = [f"Perform a '{request.task_type}' task on work_product {request.work_product_id}."]
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
            "validate_stress": self._run_validate_stress,
            "check_tolerances": self._run_check_tolerances,
            "generate_mesh": self._run_generate_mesh,
            "generate_cad": self._run_generate_cad,
            "full_validation": self._run_full_validation,
        }
        return handlers[task_type]

    async def _run_validate_stress(self, request: TaskRequest) -> TaskResult:
        """Run stress validation using the validate_stress skill."""
        self._create_skill_context(request.branch)

        # Build skill input from request parameters
        skill_input_data: dict[str, Any] = {
            "work_product_id": request.work_product_id,
            "mesh_file_path": request.parameters.get("mesh_file_path", ""),
            "load_case": request.parameters.get("load_case", "default"),
            "constraints": request.parameters.get("constraints", []),
        }

        # Invoke via MCP bridge (CalculiX FEA)
        try:
            fea_result = await self.mcp.invoke(
                "calculix.run_fea",
                {
                    "mesh_file": skill_input_data["mesh_file_path"],
                    "load_case": skill_input_data["load_case"],
                    "analysis_type": "static_stress",
                },
            )
        except Exception as exc:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[f"FEA solver failed: {exc}"],
            )

        self.logger.info("FEA completed", solver_time=fea_result.get("solver_time", 0))

        # Evaluate constraints
        constraints: list[dict[str, Any]] = skill_input_data.get("constraints", [])
        stress_data: dict[str, Any] = fea_result.get("max_von_mises", {})
        all_passed = True
        results: list[dict[str, Any]] = []

        for constraint in constraints:
            max_allowable: float = float(constraint.get("max_von_mises_mpa", float("inf")))
            safety_factor: float = float(constraint.get("safety_factor", 1.5))
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

        skill_result_dict: dict[str, Any] = {
            "skill": "validate_stress",
            "fea_result": fea_result,
            "constraint_results": results,
            "overall_passed": all_passed,
        }

        # Writeback: update the existing WorkProduct with stress validation metadata
        try:
            wb = await writeback_stress(
                self.twin,
                self.session_id,
                request.branch,
                request.work_product_id,
                skill_result_dict,
            )
            skill_result_dict["work_product_id"] = str(wb.id)
        except Exception as exc:
            self.logger.warning("writeback_stress_failed", error=str(exc))

        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=all_passed,
            skill_results=[skill_result_dict],
            warnings=[] if all_passed else ["One or more stress constraints violated"],
        )

    async def _run_check_tolerances(self, request: TaskRequest) -> TaskResult:
        """Run tolerance checking using the check_tolerance skill."""
        ctx = self._create_skill_context(request.branch)

        # Build tolerance specs from request parameters
        raw_tolerances: list[dict[str, Any]] = request.parameters.get("tolerances", [])
        tolerances = [ToleranceSpec.model_validate(t) for t in raw_tolerances]

        raw_process: dict[str, Any] = request.parameters.get("manufacturing_process", {})
        if not raw_process:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: manufacturing_process"],
            )

        try:
            process = ManufacturingProcess.model_validate(raw_process)
        except Exception as exc:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=[f"Invalid manufacturing_process: {exc}"],
            )

        skill_input = CheckToleranceInput(
            work_product_id=request.work_product_id,
            tolerances=tolerances,
            manufacturing_process=process,
            material=request.parameters.get("material", "aluminum_6061"),
            check_stack_up=request.parameters.get("check_stack_up", False),
        )

        handler = CheckToleranceHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        skill_result_dict: dict[str, Any] = {
            "skill": "check_tolerance",
            "overall_status": output.overall_status,
            "total_dimensions_checked": output.total_dimensions_checked,
            "passed": output.passed,
            "warnings": output.warnings,
            "failures": output.failures,
            "summary": output.summary,
        }

        # Writeback: update the existing WorkProduct with tolerance metadata
        try:
            wb = await writeback_tolerance(
                self.twin,
                self.session_id,
                request.branch,
                request.work_product_id,
                skill_result_dict,
            )
            skill_result_dict["work_product_id"] = str(wb.id)
        except Exception as exc:
            self.logger.warning("writeback_tolerance_failed", error=str(exc))

        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=output.overall_status != "fail",
            skill_results=[skill_result_dict],
            warnings=([output.summary] if output.overall_status == "marginal" else []),
        )

    async def _run_generate_mesh(self, request: TaskRequest) -> TaskResult:
        """Run mesh generation using the generate_mesh skill."""
        ctx = self._create_skill_context(request.branch)

        cad_file: str = request.parameters.get("cad_file", "")
        if not cad_file:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: cad_file"],
            )

        skill_input = GenerateMeshInput(
            work_product_id=request.work_product_id,
            cad_file=cad_file,
            element_size=request.parameters.get("element_size", 1.0),
            algorithm=request.parameters.get("algorithm", "netgen"),
            output_format=request.parameters.get("output_format", "inp"),
            min_angle_threshold=request.parameters.get("min_angle_threshold", 15.0),
            max_aspect_ratio_threshold=request.parameters.get("max_aspect_ratio_threshold", 10.0),
            refinement_regions=request.parameters.get("refinement_regions", []),
        )

        handler = GenerateMeshHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        skill_result_dict: dict[str, Any] = {
            "skill": "generate_mesh",
            "mesh_file": output.mesh_file,
            "num_nodes": output.num_nodes,
            "num_elements": output.num_elements,
            "element_types": output.element_types,
            "quality_acceptable": output.quality_acceptable,
            "quality_issues": output.quality_issues,
            "algorithm_used": output.algorithm_used,
            "element_size_used": output.element_size_used,
        }

        # Writeback: create a new SIMULATION_RESULT WorkProduct for the mesh
        try:
            wb = await writeback_mesh(
                self.twin,
                self.session_id,
                request.branch,
                skill_result_dict,
            )
            skill_result_dict["work_product_id"] = str(wb.id)
        except Exception as exc:
            self.logger.warning("writeback_mesh_failed", error=str(exc))

        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=output.quality_acceptable,
            skill_results=[skill_result_dict],
            warnings=output.quality_issues if not output.quality_acceptable else [],
        )

    async def _run_generate_cad(self, request: TaskRequest) -> TaskResult:
        """Run CAD generation using the generate_cad skill."""
        ctx = self._create_skill_context(request.branch)

        shape_type: str = request.parameters.get("shape_type", "")
        if not shape_type:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: shape_type"],
            )

        dimensions: dict = request.parameters.get("dimensions", {})
        if not dimensions:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=["Missing required parameter: dimensions"],
            )

        skill_input = GenerateCadInput(
            work_product_id=request.work_product_id,
            shape_type=shape_type,
            dimensions=dimensions,
            material=request.parameters.get("material", "aluminum_6061"),
            output_path=request.parameters.get("output_path", ""),
            constraints=request.parameters.get("constraints", {}),
        )

        handler = GenerateCadHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                work_product_id=request.work_product_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        skill_result_dict: dict[str, Any] = {
            "skill": "generate_cad",
            "cad_file": output.cad_file,
            "shape_type": output.shape_type,
            "volume_mm3": output.volume_mm3,
            "surface_area_mm2": output.surface_area_mm2,
            "bounding_box": output.bounding_box.model_dump(),
            "parameters_used": output.parameters_used,
            "material": output.material,
        }

        # Writeback: create a new CAD_MODEL WorkProduct
        try:
            wb = await writeback_cad(
                self.twin,
                self.session_id,
                request.branch,
                skill_result_dict,
            )
            skill_result_dict["work_product_id"] = str(wb.id)
        except Exception as exc:
            self.logger.warning("writeback_cad_failed", error=str(exc))

        return TaskResult(
            task_type=request.task_type,
            work_product_id=request.work_product_id,
            success=True,
            skill_results=[skill_result_dict],
        )

    async def _run_full_validation(self, request: TaskRequest) -> TaskResult:
        """Run full mechanical validation (stress + tolerances)."""
        # Run stress validation first
        stress_result = await self._run_validate_stress(request)

        all_results = list(stress_result.skill_results)
        all_errors = list(stress_result.errors)
        all_warnings = list(stress_result.warnings)

        overall_success = stress_result.success

        # Run tolerance check if tolerance parameters are provided
        if request.parameters.get("tolerances") and request.parameters.get("manufacturing_process"):
            tol_result = await self._run_check_tolerances(request)
            all_results.extend(tol_result.skill_results)
            all_errors.extend(tol_result.errors)
            all_warnings.extend(tol_result.warnings)
            if not tol_result.success:
                overall_success = False

        return TaskResult(
            task_type="full_validation",
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
