"""Handler for the generate_cad_ir skill."""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import structlog
from pydantic import ValidationError

from domain_agents.shared.cadquery_lowering import (
    CadqueryLoweringResult,
    lower_design_ir_cadquery,
)
from domain_agents.shared.cadquery_lowering import (
    LoweringError as CadqueryLoweringError,
)
from domain_agents.shared.freecad_lowering import (
    FreecadLoweringResult,
    lower_design_ir_freecad,
)
from domain_agents.shared.freecad_lowering import (
    LoweringError as FreecadLoweringError,
)
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase
from twin_core.design_ir import DesignIR

from .schema import BoundingBox, GenerateCadIrInput, GenerateCadIrOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.generate_cad_ir")


class GenerateCadIrHandler(SkillBase[GenerateCadIrInput, GenerateCadIrOutput]):
    """Generates CAD geometry from a Design IR document (requirements doc §6).

    This is FR-1's structured path: agents emit typed, id-addressed feature
    entities, not a script, and this skill lowers them against one of two
    real compilers (§6.6.2), selected by ``input_data.adapter``:

    - ``"freecad"`` (default): session-based, one MCP call per entity, full
      v1 op coverage (``domain_agents/shared/freecad_lowering.py``).
    - ``"cadquery"``: a real compiler -- flattens the document into one
      generated script, executed via a single MCP call. Narrower v1 op
      subset than FreeCAD's (``domain_agents/shared/cadquery_lowering.py``;
      see its docstring for the exact cut).

    Single exportable terminal entity per document either way -- see each
    lowering module's docstring for its exact v1 scope cuts. The sandboxed
    script path (``generate_cad_script``) remains available for anything
    neither lowering pass covers yet.
    """

    input_type = GenerateCadIrInput
    output_type = GenerateCadIrOutput

    async def validate_preconditions(self, input_data: GenerateCadIrInput) -> list[str]:
        """Check that the work_product exists and the selected adapter's tool is available."""
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

    async def execute(self, input_data: GenerateCadIrInput) -> GenerateCadIrOutput:
        """Validate the Design IR, lower it via the selected adapter, then export and commit."""
        with tracer.start_as_current_span("generate_cad_ir") as span:
            span.set_attribute("skill.name", "generate_cad_ir")
            span.set_attribute("skill.domain", "mechanical")
            span.set_attribute("entity_count", len(input_data.entities))
            span.set_attribute("adapter", input_data.adapter)

            self.logger.info(
                "Generating CAD from Design IR",
                work_product_id=str(input_data.work_product_id),
                entity_count=len(input_data.entities),
                material=input_data.material,
                adapter=input_data.adapter,
            )

            start = time.monotonic()

            try:
                doc = DesignIR.model_validate({"entities": input_data.entities})
            except ValidationError as exc:
                raise ValueError(f"Invalid Design IR document: {exc}") from exc

            result: CadqueryLoweringResult | FreecadLoweringResult
            try:
                if input_data.adapter == "cadquery":
                    result = await lower_design_ir_cadquery(self.context.mcp, doc)
                else:
                    result = await lower_design_ir_freecad(self.context.mcp, doc)
            except (FreecadLoweringError, CadqueryLoweringError) as exc:
                span.record_exception(exc)
                raise ValueError(str(exc)) from exc

            wp_tag = input_data.work_product_id or "new"
            output_path = f"output/ir_{wp_tag}.step"
            self._write_output(output_path, result.step_bytes)

            elapsed = time.monotonic() - start

            raw_bbox = result.bounding_box
            bounding_box = BoundingBox(
                min_x=float(raw_bbox.get("min_x", 0.0)),
                min_y=float(raw_bbox.get("min_y", 0.0)),
                min_z=float(raw_bbox.get("min_z", 0.0)),
                max_x=float(raw_bbox.get("max_x", 0.0)),
                max_y=float(raw_bbox.get("max_y", 0.0)),
                max_z=float(raw_bbox.get("max_z", 0.0)),
            )

            self.logger.info(
                "Design IR lowering complete",
                cad_file=output_path,
                volume_mm3=result.volume_mm3,
                terminal_entity_id=result.terminal_entity_id,
                elapsed_s=round(elapsed, 3),
            )
            span.set_attribute("volume_mm3", result.volume_mm3)
            span.set_attribute("elapsed_s", elapsed)

            committed = False
            twin_node_id: str | None = None
            model_url: str | None = None
            commit_error: str | None = None
            if input_data.commit:
                committed, twin_node_id, model_url, commit_error = await self._commit_geometry(
                    step_bytes=result.step_bytes,
                    material=input_data.material,
                    project_id=input_data.project_id,
                )
                span.set_attribute("committed", committed)

            return GenerateCadIrOutput(
                work_product_id=input_data.work_product_id,
                cad_file=output_path,
                entity_count=len(doc.entities),
                volume_mm3=result.volume_mm3,
                surface_area_mm2=result.surface_area_mm2,
                bounding_box=bounding_box,
                obj_id_map=result.obj_id_map,
                material=input_data.material,
                committed=committed,
                twin_node_id=twin_node_id,
                model_url=model_url,
                commit_error=commit_error,
            )

    async def _commit_geometry(
        self, *, step_bytes: bytes, material: str, project_id: str | None
    ) -> tuple[bool, str | None, str | None, str | None]:
        """Best-effort persist the STEP bytes via twin.commit_geometry.

        Unlike ``generate_cad``'s equivalent, this needs no on-disk re-read:
        the Lowering Pass already returns the STEP content directly, since
        FreeCAD's ``export_model`` MCP tool returns bytes, not a file path.

        Returns:
            (committed, twin_node_id, model_url, commit_error).
        """
        if not await self.context.mcp.is_available("twin.commit_geometry"):
            return False, None, None, "twin.commit_geometry tool is not available"

        arguments: dict[str, Any] = {
            "name": f"design_ir ({material})",
            "step_base64": base64.b64encode(step_bytes).decode("ascii"),
            "domain": "mechanical",
            "format": "step",
        }
        if project_id:
            arguments["project_id"] = project_id

        try:
            result = await self.context.mcp.invoke("twin.commit_geometry", arguments, timeout=60)
        except Exception as exc:
            self.logger.warning("twin.commit_geometry failed", error=str(exc))
            return False, None, None, str(exc)

        return True, result.get("node_id"), result.get("model_url"), None

    @staticmethod
    def _write_output(output_path: str, content: bytes) -> None:
        """Write exported CAD bytes to the shared workspace, creating parent dirs."""
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(content)

    async def validate_output(self, output: GenerateCadIrOutput) -> list[str]:
        """Verify that the generated CAD file path is non-empty and volume > 0."""
        errors: list[str] = []
        if not output.cad_file:
            errors.append("Generated CAD file path is empty")
        if output.volume_mm3 <= 0:
            errors.append("Generated volume must be greater than zero")
        return errors
