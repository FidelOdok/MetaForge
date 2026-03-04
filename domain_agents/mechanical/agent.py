"""Mechanical engineering domain agent.

Orchestrates skill execution for mechanical design validation:
stress analysis, tolerance checking, and mesh generation.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel

from domain_agents.mechanical.skills.check_tolerance.handler import (
    CheckToleranceHandler,
)
from domain_agents.mechanical.skills.check_tolerance.schema import (
    CheckToleranceInput,
    ManufacturingProcess,
    ToleranceSpec,
)
from domain_agents.mechanical.skills.generate_mesh.handler import (
    GenerateMeshHandler,
)
from domain_agents.mechanical.skills.generate_mesh.schema import (
    GenerateMeshInput,
)
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger()


class TaskRequest(BaseModel):
    """A request for the mechanical agent to perform a task."""

    task_type: str  # "validate_stress", "check_tolerances", "generate_mesh", "full_validation"
    artifact_id: UUID
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of a mechanical agent task."""

    task_type: str
    artifact_id: UUID
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


class MechanicalAgent:
    """Mechanical engineering domain agent.

    Orchestrates skill execution for mechanical design validation.
    The agent is stateless -- all state lives in the Digital Twin.

    Usage:
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(TaskRequest(
            task_type="validate_stress",
            artifact_id=artifact.id,
            parameters={"mesh_file_path": "mesh/bracket.inp", ...},
        ))
    """

    SUPPORTED_TASKS = {"validate_stress", "check_tolerances", "generate_mesh", "full_validation"}

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
            "validate_stress": self._run_validate_stress,
            "check_tolerances": self._run_check_tolerances,
            "generate_mesh": self._run_generate_mesh,
            "full_validation": self._run_full_validation,
        }
        return handlers[task_type]

    async def _run_validate_stress(self, request: TaskRequest) -> TaskResult:
        """Run stress validation using the validate_stress skill."""
        self._create_skill_context(request.branch)

        # Build skill input from request parameters
        skill_input_data: dict[str, Any] = {
            "artifact_id": str(request.artifact_id),
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
                artifact_id=request.artifact_id,
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

        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=all_passed,
            skill_results=[
                {
                    "skill": "validate_stress",
                    "fea_result": fea_result,
                    "constraint_results": results,
                    "overall_passed": all_passed,
                }
            ],
            warnings=[] if all_passed else ["One or more stress constraints violated"],
        )

    async def _run_check_tolerances(self, request: TaskRequest) -> TaskResult:
        """Run tolerance checking using the check_tolerance skill."""
        ctx = self._create_skill_context(request.branch)

        # Build tolerance specs from request parameters
        raw_tolerances: list[dict[str, Any]] = request.parameters.get("tolerances", [])
        tolerances = [ToleranceSpec.model_validate(t) for t in raw_tolerances]

        raw_process: dict[str, Any] = request.parameters.get(
            "manufacturing_process", {}
        )
        if not raw_process:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: manufacturing_process"],
            )

        try:
            process = ManufacturingProcess.model_validate(raw_process)
        except Exception as exc:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[f"Invalid manufacturing_process: {exc}"],
            )

        skill_input = CheckToleranceInput(
            artifact_id=str(request.artifact_id),
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
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=output.overall_status != "fail",
            skill_results=[
                {
                    "skill": "check_tolerance",
                    "overall_status": output.overall_status,
                    "total_dimensions_checked": output.total_dimensions_checked,
                    "passed": output.passed,
                    "warnings": output.warnings,
                    "failures": output.failures,
                    "summary": output.summary,
                }
            ],
            warnings=(
                [output.summary]
                if output.overall_status == "marginal"
                else []
            ),
        )

    async def _run_generate_mesh(self, request: TaskRequest) -> TaskResult:
        """Run mesh generation using the generate_mesh skill."""
        ctx = self._create_skill_context(request.branch)

        cad_file: str = request.parameters.get("cad_file", "")
        if not cad_file:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: cad_file"],
            )

        skill_input = GenerateMeshInput(
            artifact_id=str(request.artifact_id),
            cad_file=cad_file,
            element_size=request.parameters.get("element_size", 1.0),
            algorithm=request.parameters.get("algorithm", "netgen"),
            output_format=request.parameters.get("output_format", "inp"),
            min_angle_threshold=request.parameters.get("min_angle_threshold", 15.0),
            max_aspect_ratio_threshold=request.parameters.get(
                "max_aspect_ratio_threshold", 10.0
            ),
            refinement_regions=request.parameters.get("refinement_regions", []),
        )

        handler = GenerateMeshHandler(ctx)
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
            success=output.quality_acceptable,
            skill_results=[
                {
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
            ],
            warnings=output.quality_issues if not output.quality_acceptable else [],
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
        if request.parameters.get("tolerances") and request.parameters.get(
            "manufacturing_process"
        ):
            tol_result = await self._run_check_tolerances(request)
            all_results.extend(tol_result.skill_results)
            all_errors.extend(tol_result.errors)
            all_warnings.extend(tol_result.warnings)
            if not tol_result.success:
                overall_success = False

        return TaskResult(
            task_type="full_validation",
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
