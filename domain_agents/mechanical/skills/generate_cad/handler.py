"""Handler for the generate_cad skill."""

from __future__ import annotations

import time
from typing import Any

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import BoundingBox, GenerateCadInput, GenerateCadOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.generate_cad")

SUPPORTED_SHAPES = {"bracket", "plate", "enclosure", "cylinder"}


class GenerateCadHandler(SkillBase[GenerateCadInput, GenerateCadOutput]):
    """Generates parametric CAD geometry via FreeCAD MCP tool.

    This skill invokes the ``freecad.create_parametric`` MCP tool to produce
    a STEP file from shape parameters (type + dimensions), then returns
    the generated file path and geometric metadata.
    """

    input_type = GenerateCadInput
    output_type = GenerateCadOutput

    async def validate_preconditions(self, input_data: GenerateCadInput) -> list[str]:
        """Check that the artifact exists and FreeCAD tool is available."""
        errors: list[str] = []

        # Check artifact exists in the Twin
        artifact = await self.context.twin.get_artifact(
            input_data.artifact_id, branch=self.context.branch
        )
        if artifact is None:
            errors.append(f"Artifact {input_data.artifact_id} not found in Twin")

        # Check FreeCAD create_parametric tool is available
        if not await self.context.mcp.is_available("freecad.create_parametric"):
            errors.append("FreeCAD create_parametric tool is not available")

        return errors

    async def execute(self, input_data: GenerateCadInput) -> GenerateCadOutput:
        """Generate CAD via FreeCAD MCP tool."""
        with tracer.start_as_current_span("generate_cad") as span:
            span.set_attribute("skill.name", "generate_cad")
            span.set_attribute("skill.domain", "mechanical")
            span.set_attribute("shape_type", input_data.shape_type)

            self.logger.info(
                "Generating CAD",
                artifact_id=str(input_data.artifact_id),
                shape_type=input_data.shape_type,
                material=input_data.material,
            )

            start = time.monotonic()

            # 1. Validate shape_type
            if input_data.shape_type not in SUPPORTED_SHAPES:
                raise ValueError(
                    f"Unsupported shape type '{input_data.shape_type}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED_SHAPES))}"
                )

            # 2. Build output path if not provided
            output_path = input_data.output_path
            if not output_path:
                output_path = f"output/{input_data.shape_type}_{input_data.artifact_id}.step"

            # 3. Invoke freecad.create_parametric via MCP bridge
            try:
                result = await self.context.mcp.invoke(
                    "freecad.create_parametric",
                    {
                        "shape_type": input_data.shape_type,
                        "parameters": input_data.dimensions,
                        "material": input_data.material,
                        "output_path": output_path,
                    },
                    timeout=300,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start

            # 4. Extract results
            cad_file: str = result.get("cad_file", "")
            volume_mm3: float = float(result.get("volume_mm3", 0.0))
            surface_area_mm2: float = float(result.get("surface_area_mm2", 0.0))
            parameters_used: dict[str, Any] = result.get("parameters_used", input_data.dimensions)

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
                "CAD generation complete",
                cad_file=cad_file,
                volume_mm3=volume_mm3,
                elapsed_s=round(elapsed, 3),
            )

            span.set_attribute("volume_mm3", volume_mm3)
            span.set_attribute("elapsed_s", elapsed)

            return GenerateCadOutput(
                artifact_id=input_data.artifact_id,
                cad_file=cad_file,
                shape_type=input_data.shape_type,
                volume_mm3=volume_mm3,
                surface_area_mm2=surface_area_mm2,
                bounding_box=bounding_box,
                parameters_used=parameters_used,
                material=input_data.material,
            )

    async def validate_output(self, output: GenerateCadOutput) -> list[str]:
        """Verify that the generated CAD file path is non-empty and volume > 0."""
        errors: list[str] = []
        if not output.cad_file:
            errors.append("Generated CAD file path is empty")
        if output.volume_mm3 <= 0:
            errors.append("Generated volume must be greater than zero")
        return errors
