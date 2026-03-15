"""Handler for the create_assembly skill."""

from __future__ import annotations

import time

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import CreateAssemblyInput, CreateAssemblyOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.create_assembly")


class CreateAssemblyHandler(SkillBase[CreateAssemblyInput, CreateAssemblyOutput]):
    """Creates multi-part CAD assemblies via the CadQuery assembly tool.

    Combines multiple STEP files into a single assembly with positioning
    and optional mating constraints.
    """

    input_type = CreateAssemblyInput
    output_type = CreateAssemblyOutput

    async def validate_preconditions(self, input_data: CreateAssemblyInput) -> list[str]:
        """Check that the work_product exists and CadQuery assembly tool is available."""
        errors: list[str] = []

        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if not await self.context.mcp.is_available("cadquery.create_assembly"):
            errors.append("CadQuery create_assembly tool is not available")

        # Validate unique part names
        names = [p.name for p in input_data.parts]
        if len(names) != len(set(names)):
            errors.append("Part names must be unique within the assembly")

        # Validate constraint references
        name_set = set(names)
        for constraint in input_data.constraints:
            if constraint.part_a not in name_set:
                errors.append(f"Constraint references unknown part: {constraint.part_a}")
            if constraint.part_b not in name_set:
                errors.append(f"Constraint references unknown part: {constraint.part_b}")

        return errors

    async def execute(self, input_data: CreateAssemblyInput) -> CreateAssemblyOutput:
        """Create assembly via CadQuery MCP tool."""
        with tracer.start_as_current_span("create_assembly") as span:
            span.set_attribute("skill.name", "create_assembly")
            span.set_attribute("skill.domain", "mechanical")
            span.set_attribute("part_count", len(input_data.parts))

            self.logger.info(
                "Creating assembly",
                work_product_id=str(input_data.work_product_id),
                part_count=len(input_data.parts),
                constraint_count=len(input_data.constraints),
            )

            start = time.monotonic()

            parts_dicts = [p.model_dump() for p in input_data.parts]
            constraints_dicts = (
                [c.model_dump() for c in input_data.constraints]
                if input_data.constraints
                else None
            )

            output_path = input_data.output_path
            if not output_path:
                output_path = f"output/assembly_{input_data.work_product_id}.step"

            try:
                result = await self.context.mcp.invoke(
                    "cadquery.create_assembly",
                    {
                        "parts": parts_dicts,
                        "constraints": constraints_dicts,
                        "output_path": output_path,
                    },
                    timeout=600,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start

            self.logger.info(
                "Assembly created",
                assembly_file=result.get("assembly_file", ""),
                part_count=result.get("part_count", 0),
                elapsed_s=round(elapsed, 3),
            )

            span.set_attribute("elapsed_s", elapsed)

            return CreateAssemblyOutput(
                work_product_id=input_data.work_product_id,
                assembly_file=result.get("assembly_file", ""),
                part_count=int(result.get("part_count", 0)),
                total_volume=float(result.get("total_volume", 0.0)),
                interference_check_passed=bool(result.get("interference_check_passed", True)),
            )

    async def validate_output(self, output: CreateAssemblyOutput) -> list[str]:
        """Verify assembly output."""
        errors: list[str] = []
        if not output.assembly_file:
            errors.append("Assembly file path is empty")
        if output.part_count <= 0:
            errors.append("Assembly must contain at least one part")
        return errors
