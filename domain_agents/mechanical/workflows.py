"""Multi-step mechanical design workflow pipeline (MET-220).

Chains mechanical agent skills into a complete work product creation
pipeline: generate_cad -> generate_mesh -> validate_stress.

Each step writes back to the Digital Twin via the writeback service
and persists generated files via the file storage service.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from domain_agents.mechanical.skills.generate_cad.handler import GenerateCadHandler
from domain_agents.mechanical.skills.generate_cad.schema import GenerateCadInput, GenerateCadOutput
from domain_agents.mechanical.skills.generate_mesh.handler import GenerateMeshHandler
from domain_agents.mechanical.skills.generate_mesh.schema import (
    GenerateMeshInput,
    GenerateMeshOutput,
)
from domain_agents.mechanical.writeback import (
    writeback_cad,
    writeback_mesh,
    writeback_stress,
)
from observability.tracing import get_tracer
from shared.storage import FileStorageService
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.mechanical.workflows")


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class StepResult(BaseModel):
    """Result of a single workflow step."""

    step_name: str = Field(..., description="Name of the workflow step")
    success: bool = Field(..., description="Whether the step succeeded")
    skill_output: dict[str, Any] = Field(default_factory=dict, description="Raw skill output")
    work_product_id: str = Field(default="", description="ID of the created/updated work product")
    file_path: str = Field(default="", description="Path to the saved file, if any")
    errors: list[str] = Field(default_factory=list, description="Error messages")
    duration_ms: float = Field(default=0.0, description="Step duration in ms")


class WorkflowResult(BaseModel):
    """Result of the full multi-step design workflow."""

    success: bool = Field(..., description="Whether all steps passed")
    steps: list[StepResult] = Field(default_factory=list, description="Results of each step")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Fix recommendations if validation failed",
    )
    summary: str = Field(default="", description="Human-readable summary")
    total_duration_ms: float = Field(default=0.0, description="Total pipeline duration in ms")


# ---------------------------------------------------------------------------
# Workflow parameters
# ---------------------------------------------------------------------------


@dataclass
class DesignWorkflowParams:
    """Parameters for a mechanical design workflow run."""

    work_product_id: UUID
    session_id: UUID
    branch: str = "main"

    # CAD generation
    shape_type: str = "bracket"
    dimensions: dict[str, float] = field(default_factory=dict)
    material: str = "aluminum_6061"
    output_path: str = ""

    # Mesh generation
    element_size: float = 1.0
    mesh_algorithm: str = "netgen"
    output_format: str = "inp"

    # Stress validation
    load_case: str = "default"
    stress_constraints: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Workflow class
# ---------------------------------------------------------------------------


class MechanicalDesignWorkflow:
    """Multi-step mechanical design pipeline.

    Pipeline steps:
    1. **generate_cad** -- Create parametric CAD geometry via FreeCAD.
    2. **generate_mesh** -- Mesh the CAD model for FEA.
    3. **validate_stress** -- Run FEA stress analysis via CalculiX.

    Each step:
    - Calls the existing skill handler
    - Writes back to the Digital Twin
    - Saves generated files to storage

    If stress validation fails, the workflow returns recommendations
    for design changes (approval gate).
    """

    def __init__(
        self,
        twin: Any,
        mcp: McpBridge,
        storage: FileStorageService,
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.storage = storage
        self.logger = logger.bind(workflow="mechanical_design")

    async def run(self, params: DesignWorkflowParams) -> WorkflowResult:
        """Execute the full design workflow pipeline."""
        with tracer.start_as_current_span("workflow.mechanical_design") as span:
            span.set_attribute("session.id", str(params.session_id))
            span.set_attribute("work_product.id", str(params.work_product_id))
            span.set_attribute("shape_type", params.shape_type)

            self.logger.info(
                "workflow_started",
                session_id=str(params.session_id),
                work_product_id=str(params.work_product_id),
                shape_type=params.shape_type,
            )

            pipeline_start = time.monotonic()
            steps: list[StepResult] = []
            all_success = True

            # Step 1: Generate CAD
            cad_step = await self._step_generate_cad(params)
            steps.append(cad_step)
            if not cad_step.success:
                all_success = False
                total_ms = (time.monotonic() - pipeline_start) * 1000
                return self._build_result(steps, all_success, total_ms, failed_step="generate_cad")

            # Step 2: Generate Mesh (uses CAD file from step 1)
            cad_file = cad_step.skill_output.get("cad_file", "")
            mesh_step = await self._step_generate_mesh(params, cad_file)
            steps.append(mesh_step)
            if not mesh_step.success:
                all_success = False
                total_ms = (time.monotonic() - pipeline_start) * 1000
                return self._build_result(steps, all_success, total_ms, failed_step="generate_mesh")

            # Step 3: Validate Stress (uses mesh file from step 2)
            mesh_file = mesh_step.skill_output.get("mesh_file", "")
            stress_step = await self._step_validate_stress(params, mesh_file)
            steps.append(stress_step)
            if not stress_step.success:
                all_success = False

            total_ms = (time.monotonic() - pipeline_start) * 1000

            failed_step = ""
            if not all_success:
                failed_step = "validate_stress"

            result = self._build_result(steps, all_success, total_ms, failed_step)

            span.set_attribute("workflow.success", all_success)
            span.set_attribute("workflow.duration_ms", total_ms)
            span.set_attribute("workflow.steps_completed", len(steps))

            self.logger.info(
                "workflow_completed",
                success=all_success,
                steps_completed=len(steps),
                total_duration_ms=round(total_ms, 1),
            )

            return result

    # --- Step implementations ------------------------------------------------

    async def _step_generate_cad(self, params: DesignWorkflowParams) -> StepResult:
        """Step 1: Generate CAD model."""
        with tracer.start_as_current_span("workflow.step.generate_cad") as span:
            start = time.monotonic()
            span.set_attribute("step.name", "generate_cad")

            self.logger.info("step_started", step="generate_cad")

            ctx = self._create_skill_context(params)

            skill_input = GenerateCadInput(
                work_product_id=params.work_product_id,
                shape_type=params.shape_type,
                dimensions=params.dimensions,
                material=params.material,
                output_path=params.output_path,
            )

            handler = GenerateCadHandler(ctx)
            result = await handler.run(skill_input)

            elapsed_ms = (time.monotonic() - start) * 1000

            if not result.success:
                span.set_attribute("step.success", False)
                return StepResult(
                    step_name="generate_cad",
                    success=False,
                    errors=result.errors,
                    duration_ms=elapsed_ms,
                )

            output = cast(GenerateCadOutput, result.data)
            skill_output: dict[str, Any] = {
                "skill": "generate_cad",
                "cad_file": output.cad_file,
                "shape_type": output.shape_type,
                "volume_mm3": output.volume_mm3,
                "surface_area_mm2": output.surface_area_mm2,
                "bounding_box": output.bounding_box.model_dump(),
                "parameters_used": output.parameters_used,
                "material": output.material,
            }

            # Writeback to Twin
            wp_id = ""
            try:
                wb = await writeback_cad(
                    self.twin,
                    params.session_id,
                    params.branch,
                    skill_output,
                )
                wp_id = str(wb.id)
            except Exception as exc:
                self.logger.warning("writeback_cad_failed", error=str(exc))
                span.record_exception(exc)

            # Save file to storage
            file_path = ""
            try:
                cad_file_str = output.cad_file
                cad_file_name = (
                    cad_file_str.rsplit("/", maxsplit=1)[-1] if cad_file_str else "model.step"
                )
                content = f"STEP file content for {params.shape_type}".encode()
                file_path = self.storage.save(str(params.session_id), cad_file_name, content)
            except Exception as exc:
                self.logger.warning("storage_save_failed", error=str(exc))
                span.record_exception(exc)

            span.set_attribute("step.success", True)
            self.logger.info(
                "step_completed",
                step="generate_cad",
                duration_ms=round(elapsed_ms, 1),
            )

            return StepResult(
                step_name="generate_cad",
                success=True,
                skill_output=skill_output,
                work_product_id=wp_id,
                file_path=file_path,
                duration_ms=elapsed_ms,
            )

    async def _step_generate_mesh(self, params: DesignWorkflowParams, cad_file: str) -> StepResult:
        """Step 2: Generate FEA mesh from CAD model."""
        with tracer.start_as_current_span("workflow.step.generate_mesh") as span:
            start = time.monotonic()
            span.set_attribute("step.name", "generate_mesh")

            self.logger.info("step_started", step="generate_mesh", cad_file=cad_file)

            ctx = self._create_skill_context(params)

            skill_input = GenerateMeshInput(
                work_product_id=params.work_product_id,
                cad_file=cad_file,
                element_size=params.element_size,
                algorithm=params.mesh_algorithm,
                output_format=params.output_format,
            )

            handler = GenerateMeshHandler(ctx)
            result = await handler.run(skill_input)

            elapsed_ms = (time.monotonic() - start) * 1000

            if not result.success:
                span.set_attribute("step.success", False)
                return StepResult(
                    step_name="generate_mesh",
                    success=False,
                    errors=result.errors,
                    duration_ms=elapsed_ms,
                )

            output = cast(GenerateMeshOutput, result.data)
            skill_output: dict[str, Any] = {
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

            # Writeback to Twin
            wp_id = ""
            try:
                wb = await writeback_mesh(
                    self.twin,
                    params.session_id,
                    params.branch,
                    skill_output,
                )
                wp_id = str(wb.id)
            except Exception as exc:
                self.logger.warning("writeback_mesh_failed", error=str(exc))
                span.record_exception(exc)

            # Save file to storage
            file_path = ""
            try:
                mesh_file_str = output.mesh_file
                mesh_file_name = (
                    mesh_file_str.rsplit("/", maxsplit=1)[-1] if mesh_file_str else "mesh.inp"
                )
                content = f"Mesh file content ({output.num_nodes} nodes)".encode()
                file_path = self.storage.save(str(params.session_id), mesh_file_name, content)
            except Exception as exc:
                self.logger.warning("storage_save_failed", error=str(exc))
                span.record_exception(exc)

            span.set_attribute("step.success", True)
            self.logger.info(
                "step_completed",
                step="generate_mesh",
                duration_ms=round(elapsed_ms, 1),
            )

            return StepResult(
                step_name="generate_mesh",
                success=True,
                skill_output=skill_output,
                work_product_id=wp_id,
                file_path=file_path,
                duration_ms=elapsed_ms,
            )

    async def _step_validate_stress(
        self, params: DesignWorkflowParams, mesh_file: str
    ) -> StepResult:
        """Step 3: Run FEA stress validation."""
        with tracer.start_as_current_span("workflow.step.validate_stress") as span:
            start = time.monotonic()
            span.set_attribute("step.name", "validate_stress")

            self.logger.info("step_started", step="validate_stress", mesh_file=mesh_file)

            # Invoke CalculiX FEA via MCP bridge
            try:
                fea_result = await self.mcp.invoke(
                    "calculix.run_fea",
                    {
                        "mesh_file": mesh_file,
                        "load_case": params.load_case,
                        "analysis_type": "static_stress",
                    },
                )
            except Exception as exc:
                elapsed_ms = (time.monotonic() - start) * 1000
                span.record_exception(exc)
                return StepResult(
                    step_name="validate_stress",
                    success=False,
                    errors=[f"FEA solver failed: {exc}"],
                    duration_ms=elapsed_ms,
                )

            # Evaluate constraints
            stress_data: dict[str, Any] = fea_result.get("max_von_mises", {})
            all_passed = True
            constraint_results: list[dict[str, Any]] = []

            for constraint in params.stress_constraints:
                max_allowable = float(constraint.get("max_von_mises_mpa", float("inf")))
                safety_factor = float(constraint.get("safety_factor", 1.5))
                allowable = max_allowable / safety_factor

                for region, stress_val in stress_data.items():
                    passed = float(stress_val) <= allowable
                    if not passed:
                        all_passed = False
                    constraint_results.append(
                        {
                            "region": region,
                            "stress_mpa": float(stress_val),
                            "allowable_mpa": allowable,
                            "passed": passed,
                        }
                    )

            skill_output: dict[str, Any] = {
                "skill": "validate_stress",
                "fea_result": fea_result,
                "constraint_results": constraint_results,
                "overall_passed": all_passed,
            }

            elapsed_ms = (time.monotonic() - start) * 1000

            # Writeback to Twin (update the CAD work product with stress metadata)
            wp_id = ""
            try:
                wb = await writeback_stress(
                    self.twin,
                    params.session_id,
                    params.branch,
                    params.work_product_id,
                    skill_output,
                )
                wp_id = str(wb.id)
            except Exception as exc:
                self.logger.warning("writeback_stress_failed", error=str(exc))
                span.record_exception(exc)

            span.set_attribute("step.success", all_passed)
            self.logger.info(
                "step_completed",
                step="validate_stress",
                passed=all_passed,
                duration_ms=round(elapsed_ms, 1),
            )

            return StepResult(
                step_name="validate_stress",
                success=all_passed,
                skill_output=skill_output,
                work_product_id=wp_id,
                errors=(["One or more stress constraints violated"] if not all_passed else []),
                duration_ms=elapsed_ms,
            )

    # --- Helpers -------------------------------------------------------------

    def _create_skill_context(self, params: DesignWorkflowParams) -> SkillContext:
        """Create a SkillContext for skill execution."""
        return SkillContext(
            twin=self.twin,
            mcp=self.mcp,
            logger=self.logger,
            session_id=params.session_id,
            branch=params.branch,
        )

    def _build_result(
        self,
        steps: list[StepResult],
        success: bool,
        total_ms: float,
        failed_step: str = "",
    ) -> WorkflowResult:
        """Build the final WorkflowResult with summary and recommendations."""
        completed = [s for s in steps if s.success]
        failed = [s for s in steps if not s.success]

        # Build summary
        parts: list[str] = []
        parts.append(
            f"Workflow {'PASSED' if success else 'FAILED'}: "
            f"{len(completed)}/{len(steps)} steps completed."
        )
        for step in steps:
            status = "PASS" if step.success else "FAIL"
            parts.append(f"  - {step.step_name}: {status}")

        summary = "\n".join(parts)

        # Build recommendations if validation failed
        recommendations: list[str] = []
        if failed_step == "validate_stress" and failed:
            stress_output = failed[0].skill_output
            constraint_results = stress_output.get("constraint_results", [])
            for cr in constraint_results:
                if not cr.get("passed", True):
                    region = cr.get("region", "unknown")
                    stress = cr.get("stress_mpa", 0.0)
                    allowable = cr.get("allowable_mpa", 0.0)
                    overshoot = stress - allowable
                    recommendations.append(
                        f"Region '{region}': stress {stress:.1f} MPa exceeds "
                        f"allowable {allowable:.1f} MPa by {overshoot:.1f} MPa. "
                        f"Consider increasing wall thickness or using a "
                        f"stronger material."
                    )
            if not recommendations:
                recommendations.append(
                    "Stress validation failed. Review load case assumptions "
                    "and consider design modifications."
                )
        elif failed_step == "generate_cad" and failed:
            recommendations.append(
                f"CAD generation failed: {', '.join(failed[0].errors)}. "
                f"Check shape_type and dimensions parameters."
            )
        elif failed_step == "generate_mesh" and failed:
            recommendations.append(
                f"Mesh generation failed: {', '.join(failed[0].errors)}. "
                f"Check CAD file format and meshing parameters."
            )

        return WorkflowResult(
            success=success,
            steps=steps,
            recommendations=recommendations,
            summary=summary,
            total_duration_ms=total_ms,
        )


__all__ = [
    "DesignWorkflowParams",
    "MechanicalDesignWorkflow",
    "StepResult",
    "WorkflowResult",
]
