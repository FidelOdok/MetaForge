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

# Tool IDs per backend
_TOOL_IDS = {
    "cadquery": "cadquery.create_parametric",
    "freecad": "freecad.create_parametric",
}


class GenerateCadHandler(SkillBase[GenerateCadInput, GenerateCadOutput]):
    """Generates parametric CAD geometry via CadQuery or FreeCAD MCP tool.

    This skill invokes ``cadquery.create_parametric`` (default) or
    ``freecad.create_parametric`` MCP tool to produce a STEP file from
    shape parameters (type + dimensions). If the preferred backend is
    unavailable, it falls back to the other with a warning.
    """

    input_type = GenerateCadInput
    output_type = GenerateCadOutput

    async def _resolve_backend(self, preferred: str) -> tuple[str, str]:
        """Resolve which backend tool to use, with fallback.

        Returns:
            Tuple of (backend_name, tool_id).

        Raises:
            RuntimeError: If no CAD backend is available.
        """
        preferred_tool = _TOOL_IDS[preferred]
        if await self.context.mcp.is_available(preferred_tool):
            return preferred, preferred_tool

        # Try fallback
        fallback = "freecad" if preferred == "cadquery" else "cadquery"
        fallback_tool = _TOOL_IDS[fallback]
        if await self.context.mcp.is_available(fallback_tool):
            self.logger.warning(
                "Preferred CAD backend unavailable, falling back",
                preferred=preferred,
                fallback=fallback,
            )
            return fallback, fallback_tool

        raise RuntimeError(f"No CAD backend available. Tried {preferred_tool} and {fallback_tool}.")

    async def validate_preconditions(self, input_data: GenerateCadInput) -> list[str]:
        """Check that the work_product exists and at least one CAD tool is available."""
        errors: list[str] = []

        if input_data.work_product_id is not None:
            work_product = await self.context.twin.get_work_product(
                input_data.work_product_id, branch=self.context.branch
            )
            if work_product is None:
                errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        # Check that at least one backend is available
        cadquery_ok = await self.context.mcp.is_available("cadquery.create_parametric")
        freecad_ok = await self.context.mcp.is_available("freecad.create_parametric")
        if not cadquery_ok and not freecad_ok:
            errors.append("No CAD backend available (neither cadquery nor freecad)")

        return errors

    async def execute(self, input_data: GenerateCadInput) -> GenerateCadOutput:
        """Generate CAD via the selected backend MCP tool."""
        with tracer.start_as_current_span("generate_cad") as span:
            span.set_attribute("skill.name", "generate_cad")
            span.set_attribute("skill.domain", "mechanical")
            span.set_attribute("shape_type", input_data.shape_type)
            span.set_attribute("backend.requested", input_data.backend)

            self.logger.info(
                "Generating CAD",
                work_product_id=str(input_data.work_product_id),
                shape_type=input_data.shape_type,
                material=input_data.material,
                backend=input_data.backend,
            )

            start = time.monotonic()

            # 1. Validate shape_type
            if input_data.shape_type not in SUPPORTED_SHAPES:
                raise ValueError(
                    f"Unsupported shape type '{input_data.shape_type}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED_SHAPES))}"
                )

            # 2. Resolve backend (with fallback)
            backend, tool_id = await self._resolve_backend(input_data.backend)
            span.set_attribute("backend.resolved", backend)

            # 3. Build output path if not provided
            output_path = input_data.output_path
            if not output_path:
                output_path = f"output/{input_data.shape_type}_{input_data.work_product_id}.step"

            # 4. Invoke create_parametric via MCP bridge
            try:
                result = await self.context.mcp.invoke(
                    tool_id,
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

            # 5. Extract results
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
                backend=backend,
                elapsed_s=round(elapsed, 3),
            )

            span.set_attribute("volume_mm3", volume_mm3)
            span.set_attribute("elapsed_s", elapsed)

            return GenerateCadOutput(
                work_product_id=input_data.work_product_id,
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
