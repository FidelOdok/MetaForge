"""Handler for the generate_enclosure skill."""

from __future__ import annotations

import time
from typing import Any

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import (
    ExternalDimensions,
    GenerateEnclosureInput,
    GenerateEnclosureOutput,
    MountingInfo,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.generate_enclosure")


class GenerateEnclosureHandler(SkillBase[GenerateEnclosureInput, GenerateEnclosureOutput]):
    """Generates a PCB enclosure via the CadQuery enclosure tool.

    Cross-domain skill: Electronics Agent provides PCB dimensions from KiCad,
    Mechanical Agent generates the matching enclosure STEP file.
    """

    input_type = GenerateEnclosureInput
    output_type = GenerateEnclosureOutput

    async def validate_preconditions(self, input_data: GenerateEnclosureInput) -> list[str]:
        """Check that the work_product exists and CadQuery enclosure tool is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if not await self.context.mcp.is_available("cadquery.generate_enclosure"):
            errors.append("CadQuery generate_enclosure tool is not available")

        return errors

    async def execute(self, input_data: GenerateEnclosureInput) -> GenerateEnclosureOutput:
        """Generate enclosure via CadQuery MCP tool."""
        with tracer.start_as_current_span("generate_enclosure") as span:
            span.set_attribute("skill.name", "generate_enclosure")
            span.set_attribute("skill.domain", "mechanical")
            span.set_attribute("pcb.length", input_data.pcb_length)
            span.set_attribute("pcb.width", input_data.pcb_width)

            self.logger.info(
                "Generating enclosure",
                work_product_id=str(input_data.work_product_id),
                pcb_size=f"{input_data.pcb_length}x{input_data.pcb_width}",
                material=input_data.material,
            )

            start = time.monotonic()

            # Build connector cutout dicts for the tool
            cutouts = [c.model_dump() for c in input_data.connector_cutouts]
            holes = [h.model_dump() for h in input_data.mounting_holes]

            try:
                result = await self.context.mcp.invoke(
                    "cadquery.generate_enclosure",
                    {
                        "pcb_length": input_data.pcb_length,
                        "pcb_width": input_data.pcb_width,
                        "pcb_thickness": input_data.pcb_thickness,
                        "component_max_height": input_data.component_max_height,
                        "connector_cutouts": cutouts,
                        "mounting_holes": holes,
                        "wall_thickness": input_data.wall_thickness,
                        "material": input_data.material,
                    },
                    timeout=300,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start

            raw_dims: dict[str, Any] = result.get("external_dimensions", {})
            raw_mount: dict[str, Any] = result.get("mounting_info", {})

            self.logger.info(
                "Enclosure generated",
                cad_file=result.get("cad_file", ""),
                elapsed_s=round(elapsed, 3),
            )

            span.set_attribute("elapsed_s", elapsed)

            return GenerateEnclosureOutput(
                work_product_id=input_data.work_product_id,
                cad_file=result.get("cad_file", ""),
                internal_volume=float(result.get("internal_volume", 0.0)),
                external_dimensions=ExternalDimensions(
                    length=float(raw_dims.get("length", 0.0)),
                    width=float(raw_dims.get("width", 0.0)),
                    height=float(raw_dims.get("height", 0.0)),
                ),
                mounting_info=MountingInfo(
                    hole_count=int(raw_mount.get("hole_count", 0)),
                    cutout_count=int(raw_mount.get("cutout_count", 0)),
                ),
                material=result.get("material", input_data.material),
            )

    async def validate_output(self, output: GenerateEnclosureOutput) -> list[str]:
        """Verify enclosure output."""
        errors: list[str] = []
        if not output.cad_file:
            errors.append("Generated CAD file path is empty")
        if output.internal_volume <= 0:
            errors.append("Internal volume must be greater than zero")
        return errors
