"""Handler for the create_assembly skill."""

from __future__ import annotations

import time
from typing import Any

import structlog

from domain_agents.shared.commit_geometry import commit_geometry
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

        if input_data.work_product_id is not None:
            work_product = await self.context.twin.get_work_product(
                input_data.work_product_id, branch=self.context.branch
            )
            if work_product is None:
                errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if not await self.context.mcp.is_available("cadquery.create_assembly"):
            errors.append("CadQuery create_assembly tool is not available")

        if any(p.node_id for p in input_data.parts) and not await self.context.mcp.is_available(
            "twin.stage_work_product_file"
        ):
            errors.append(
                "A part references a Twin node_id but twin.stage_work_product_file "
                "is not available to materialize it"
            )

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

            # Resolve each part to a local file path CadQuery can load: a
            # node_id-referenced part is materialized from its committed
            # Twin blob via twin.stage_work_product_file -- the durable
            # reference every other committed-geometry lookup in this
            # system uses -- rather than depending on the model to track
            # and correctly re-supply a raw adapter-ephemeral path, which
            # silently goes stale the moment a later call reuses the same
            # filename (FORGE-85). A raw file path is used as given.
            parts_dicts: list[dict[str, Any]] = []
            for part in input_data.parts:
                file_path = part.file
                if part.node_id:
                    staged = await self.context.mcp.invoke(
                        "twin.stage_work_product_file", {"node_id": part.node_id}, timeout=60
                    )
                    file_path = staged["file_path"]
                parts_dicts.append(
                    {"name": part.name, "file": file_path, "location": part.location}
                )

            constraints_dicts = (
                [c.model_dump() for c in input_data.constraints] if input_data.constraints else None
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

            assembly_file: str = result.get("assembly_file", "")

            self.logger.info(
                "Assembly created",
                assembly_file=assembly_file,
                part_count=result.get("part_count", 0),
                elapsed_s=round(elapsed, 3),
            )

            span.set_attribute("elapsed_s", elapsed)

            # Persist into the Twin so this skill always leaves a reviewable
            # work product behind, same as generate_cad/generate_enclosure
            # (FORGE-85) -- this skill previously never committed at all.
            committed = False
            twin_node_id: str | None = None
            model_url: str | None = None
            commit_error: str | None = None
            if input_data.commit:
                committed, twin_node_id, model_url, commit_error = await commit_geometry(
                    self.context.mcp,
                    cad_file=assembly_file,
                    name=input_data.name,
                    project_id=input_data.project_id,
                )
                span.set_attribute("committed", committed)

            return CreateAssemblyOutput(
                work_product_id=input_data.work_product_id,
                assembly_file=assembly_file,
                part_count=int(result.get("part_count", 0)),
                total_volume=float(result.get("total_volume", 0.0)),
                interference_check_passed=bool(result.get("interference_check_passed", True)),
                committed=committed,
                twin_node_id=twin_node_id,
                model_url=model_url,
                commit_error=commit_error,
            )

    async def validate_output(self, output: CreateAssemblyOutput) -> list[str]:
        """Verify assembly output."""
        errors: list[str] = []
        if not output.assembly_file:
            errors.append("Assembly file path is empty")
        if output.part_count <= 0:
            errors.append("Assembly must contain at least one part")
        return errors
