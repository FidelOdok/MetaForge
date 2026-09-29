"""Handler for the generate_parametric_feature skill (FORGE-269, gap G-D1)."""

from __future__ import annotations

import structlog

from domain_agents.mechanical.skills.generate_cad_ir.handler import GenerateCadIrHandler
from domain_agents.mechanical.skills.generate_cad_ir.schema import GenerateCadIrInput
from domain_agents.shared.design_ir_macros import MACROS
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import GenerateParametricFeatureInput, GenerateParametricFeatureOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.generate_parametric_feature")


class GenerateParametricFeatureHandler(
    SkillBase[GenerateParametricFeatureInput, GenerateParametricFeatureOutput]
):
    """Generates a named, reusable parametric feature (bolt_pattern, rib, ...
    -- see ``domain_agents.shared.design_ir_macros`` for the full library
    and which features remain deferred) as a Design IR document.

    Composes, rather than re-derives: a macro builds the entity sequence,
    then this handler delegates lowering + commit to the SAME
    ``generate_cad_ir`` skill every other Design IR document already goes
    through -- zero duplication of that skill's real compiler/commit logic.
    Called directly (``GenerateCadIrHandler(self.context).execute(...)``,
    not its own ``run()``) so metrics/precondition checks aren't recorded
    twice -- this skill's own ``validate_preconditions`` does the
    equivalent check for the composed skill's own requirements.
    """

    input_type = GenerateParametricFeatureInput
    output_type = GenerateParametricFeatureOutput

    async def validate_preconditions(self, input_data: GenerateParametricFeatureInput) -> list[str]:
        """Same checks generate_cad_ir's own validate_preconditions makes --
        this skill delegates to it directly, bypassing its precondition
        check, so the equivalent must happen here."""
        errors: list[str] = []

        if input_data.work_product_id is not None:
            work_product = await self.context.twin.get_work_product(
                input_data.work_product_id, branch=self.context.branch
            )
            if work_product is None:
                errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        if input_data.adapter == "cadquery":
            if not await self.context.mcp.is_available("cadquery.execute_script"):
                errors.append(
                    "CadQuery script API is not available (adapter='cadquery' requires "
                    "cadquery.execute_script)"
                )
        elif not await self.context.mcp.is_available("freecad.open_session"):
            errors.append(
                "FreeCAD session API is not available (adapter='freecad' requires "
                "freecad.open_session)"
            )

        return errors

    async def execute(
        self, input_data: GenerateParametricFeatureInput
    ) -> GenerateParametricFeatureOutput:
        with tracer.start_as_current_span("generate_parametric_feature") as span:
            feature_type = input_data.feature.feature_type
            span.set_attribute("skill.name", "generate_parametric_feature")
            span.set_attribute("feature_type", feature_type)
            span.set_attribute("adapter", input_data.adapter)

            macro = MACROS[feature_type]
            params = input_data.feature.model_dump(exclude={"feature_type"})
            self.logger.info(
                "Generating parametric feature",
                feature_type=feature_type,
                params=params,
                adapter=input_data.adapter,
            )
            try:
                entities = macro(**params)
            except ValueError as exc:
                raise ValueError(f"Invalid {feature_type} parameters: {exc}") from exc

            cad_ir_input = GenerateCadIrInput(
                work_product_id=input_data.work_product_id,
                name=input_data.name,
                entities=entities,
                adapter=input_data.adapter,
                material=input_data.material,
                project_id=input_data.project_id,
                commit=input_data.commit,
            )
            cad_ir_output = await GenerateCadIrHandler(self.context).execute(cad_ir_input)

            return GenerateParametricFeatureOutput(
                feature_type=feature_type,
                work_product_id=cad_ir_output.work_product_id,
                cad_file=cad_ir_output.cad_file,
                entity_count=cad_ir_output.entity_count,
                volume_mm3=cad_ir_output.volume_mm3,
                surface_area_mm2=cad_ir_output.surface_area_mm2,
                bounding_box=cad_ir_output.bounding_box,
                material=cad_ir_output.material,
                committed=cad_ir_output.committed,
                twin_node_id=cad_ir_output.twin_node_id,
                model_url=cad_ir_output.model_url,
                commit_error=cad_ir_output.commit_error,
                already_committed=cad_ir_output.already_committed,
            )

    async def validate_output(self, output: GenerateParametricFeatureOutput) -> list[str]:
        errors: list[str] = []
        if not output.cad_file:
            errors.append("Generated CAD file path is empty")
        if output.volume_mm3 <= 0:
            errors.append("Generated volume must be greater than zero")
        return errors
