"""Handler for the generate_cad_script skill."""

from __future__ import annotations

import time
from typing import Any

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import BoundingBox, GenerateCadScriptInput, GenerateCadScriptOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.generate_cad_script")


class GenerateCadScriptHandler(SkillBase[GenerateCadScriptInput, GenerateCadScriptOutput]):
    """Generates CadQuery Python scripts and executes them to produce 3D models.

    This is the highest-value CadQuery skill: it takes a natural language
    description, generates a CadQuery script, and executes it via the
    ``cadquery.execute_script`` MCP tool in a sandboxed environment.

    The script generation itself happens upstream (by the LLM agent that
    invokes this skill). This handler receives the generated script text
    as part of the tool invocation flow and executes it safely.
    """

    input_type = GenerateCadScriptInput
    output_type = GenerateCadScriptOutput

    async def validate_preconditions(self, input_data: GenerateCadScriptInput) -> list[str]:
        """Check that the work_product exists and CadQuery scripting tool is available."""
        errors: list[str] = []

        # Work product lookup is optional for generative actions
        if input_data.work_product_id is not None:
            wp = await self.context.twin.get_work_product(
                input_data.work_product_id, branch=self.context.branch
            )
            if wp is None:
                errors.append(
                    f"WorkProduct {input_data.work_product_id} not found"
                )

        if not await self.context.mcp.is_available("cadquery.execute_script"):
            errors.append("CadQuery execute_script tool is not available")

        return errors

    async def execute(self, input_data: GenerateCadScriptInput) -> GenerateCadScriptOutput:
        """Execute a CadQuery script generated from the description."""
        with tracer.start_as_current_span("generate_cad_script") as span:
            span.set_attribute("skill.name", "generate_cad_script")
            span.set_attribute("skill.domain", "mechanical")

            self.logger.info(
                "Generating CAD from script",
                work_product_id=str(input_data.work_product_id),
                description_length=len(input_data.description),
                material=input_data.material,
            )

            start = time.monotonic()

            # Build a CadQuery script from the description and constraints.
            # In the full agent loop, the LLM generates this script. Here we
            # construct a minimal parametric script from structured constraints
            # as a deterministic fallback.
            script = self._build_script(input_data.description, input_data.constraints)

            wp_tag = input_data.work_product_id or "new"
            output_path = f"output/script_{wp_tag}.{input_data.output_format}"

            try:
                result = await self.context.mcp.invoke(
                    "cadquery.execute_script",
                    {
                        "script": script,
                        "output_path": output_path,
                    },
                    timeout=300,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start

            cad_file: str = result.get("cad_file", "")
            script_text: str = result.get("script_text", script)
            volume_mm3: float = float(result.get("volume_mm3", 0.0))
            surface_area_mm2: float = float(result.get("surface_area_mm2", 0.0))

            raw_bbox: dict[str, Any] = result.get("bounding_box", {})
            bounding_box = BoundingBox(
                min_x=float(raw_bbox.get("min_x", 0.0)),
                min_y=float(raw_bbox.get("min_y", 0.0)),
                min_z=float(raw_bbox.get("min_z", 0.0)),
                max_x=float(raw_bbox.get("max_x", 0.0)),
                max_y=float(raw_bbox.get("max_y", 0.0)),
                max_z=float(raw_bbox.get("max_z", 0.0)),
            )

            self.logger.info(
                "CAD script execution complete",
                cad_file=cad_file,
                volume_mm3=volume_mm3,
                elapsed_s=round(elapsed, 3),
            )

            span.set_attribute("volume_mm3", volume_mm3)
            span.set_attribute("elapsed_s", elapsed)

            return GenerateCadScriptOutput(
                work_product_id=input_data.work_product_id,
                cad_file=cad_file,
                script_text=script_text,
                volume_mm3=volume_mm3,
                surface_area_mm2=surface_area_mm2,
                bounding_box=bounding_box,
            )

    def _build_script(self, description: str, constraints: dict[str, Any]) -> str:
        """Build a CadQuery script from description and constraints.

        This is a deterministic fallback that produces a simple parametric
        model. In the full agent loop, the LLM generates more sophisticated
        scripts from the natural language description.
        """
        length = constraints.get("length", 50.0)
        width = constraints.get("width", 30.0)
        height = constraints.get("height", 20.0)

        return (
            f"# Generated from: {description[:80]}\n"
            f"length = {length}\n"
            f"width = {width}\n"
            f"height = {height}\n"
            "\n"
            "result = cq.Workplane('XY').box(length, width, height)\n"
        )

    async def validate_output(self, output: GenerateCadScriptOutput) -> list[str]:
        """Verify that the generated CAD file path is non-empty and volume > 0."""
        errors: list[str] = []
        if not output.cad_file:
            errors.append("Generated CAD file path is empty")
        if output.volume_mm3 <= 0:
            errors.append("Generated volume must be greater than zero")
        if not output.script_text:
            errors.append("Script text is empty")
        return errors
