"""Handler for the generate_technical_drawing skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import GenerateTechnicalDrawingInput, GenerateTechnicalDrawingOutput


class GenerateTechnicalDrawingHandler(
    SkillBase[GenerateTechnicalDrawingInput, GenerateTechnicalDrawingOutput]
):
    """Persists a structured drawing-package spec (dimensions, GD&T, surface
    finishes, inspection requirements) for a CAD part as a
    TECHNICAL_DRAWING work product, linked back to its source CAD_MODEL.

    Not a rendered 2D vector drawing -- MetaForge has no TechDraw-equivalent
    generator; this persists the callout DATA a real drawing would encode.
    """

    input_type = GenerateTechnicalDrawingInput
    output_type = GenerateTechnicalDrawingOutput

    async def validate_preconditions(self, input_data: GenerateTechnicalDrawingInput) -> list[str]:
        errors: list[str] = []
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        if not await self.context.mcp.is_available("twin.commit_technical_drawing"):
            errors.append("twin.commit_technical_drawing tool is not available")
        return errors

    async def execute(
        self, input_data: GenerateTechnicalDrawingInput
    ) -> GenerateTechnicalDrawingOutput:
        self.logger.info(
            "Generating technical drawing package",
            work_product_id=str(input_data.work_product_id),
            part_name=input_data.part_name,
            dimension_count=len(input_data.dimensions),
        )
        result = await self.context.mcp.invoke(
            "twin.commit_technical_drawing",
            {
                "name": f"{input_data.part_name} Drawing",
                "part_name": input_data.part_name,
                "dimensions": [d.model_dump() for d in input_data.dimensions],
                "gdt_callouts": [g.model_dump() for g in input_data.gdt_callouts],
                "surface_finishes": [s.model_dump() for s in input_data.surface_finishes],
                "inspection_requirements": input_data.inspection_requirements,
                "source_node_ids": [str(input_data.work_product_id)],
                "project_id": input_data.project_id,
            },
        )
        return GenerateTechnicalDrawingOutput(
            node_id=result["node_id"],
            dimension_count=int(result.get("dimension_count", len(input_data.dimensions))),
            gdt_callout_count=int(result.get("gdt_callout_count", len(input_data.gdt_callouts))),
            surface_finish_count=int(
                result.get("surface_finish_count", len(input_data.surface_finishes))
            ),
        )
