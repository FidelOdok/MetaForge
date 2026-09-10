"""Handler for the generate_cad_script skill."""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import structlog

from domain_agents.shared.cad_backend import resolve_cad_backend
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import BoundingBox, GenerateCadScriptInput, GenerateCadScriptOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("skill.generate_cad_script")


class GenerateCadScriptHandler(SkillBase[GenerateCadScriptInput, GenerateCadScriptOutput]):
    """Generates a CAD script from a natural-language description and executes it.

    Takes a natural language description, generates a script in whichever
    dialect ``backend`` selects (CadQuery or FreeCAD), and executes it via
    the matching sandboxed MCP tool (``cadquery.execute_script`` or
    ``freecad.execute_code``).

    The script generation itself happens upstream (by the LLM agent that
    invokes this skill). This handler receives the generated script text
    as part of the tool invocation flow and executes it safely.
    """

    input_type = GenerateCadScriptInput
    output_type = GenerateCadScriptOutput

    async def validate_preconditions(self, input_data: GenerateCadScriptInput) -> list[str]:
        """Check that the work_product exists and a CAD scripting backend is available."""
        errors: list[str] = []

        # Work product lookup is optional for generative actions
        if input_data.work_product_id is not None:
            work_product = await self.context.twin.get_work_product(
                input_data.work_product_id, branch=self.context.branch
            )
            if work_product is None:
                errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")

        candidates = await self.context.mcp.list_tools(capability="cad_scripting")
        if not candidates:
            errors.append("No CAD scripting backend available (neither cadquery nor freecad)")

        return errors

    async def execute(self, input_data: GenerateCadScriptInput) -> GenerateCadScriptOutput:
        """Execute a CAD script generated from the description, on the resolved backend."""
        with tracer.start_as_current_span("generate_cad_script") as span:
            span.set_attribute("skill.name", "generate_cad_script")
            span.set_attribute("skill.domain", "mechanical")
            span.set_attribute("backend.requested", input_data.backend)

            self.logger.info(
                "Generating CAD from script",
                work_product_id=str(input_data.work_product_id),
                description_length=len(input_data.description),
                material=input_data.material,
                backend=input_data.backend,
            )

            start = time.monotonic()

            # Prefer a script the caller already generated (typically an LLM
            # writing real CadQuery/FreeCAD code from the description). Only
            # fall back to the deterministic CadQuery box builder when none
            # was supplied.
            script = input_data.script or self._build_script(
                input_data.description, input_data.constraints
            )

            backend, tool_id = await resolve_cad_backend(
                self.context.mcp, "cad_scripting", input_data.backend
            )
            span.set_attribute("backend.resolved", backend)

            wp_tag = input_data.work_product_id or "new"
            output_path = f"output/script_{wp_tag}.{input_data.output_format}"

            script_text = script
            try:
                if backend == "freecad":
                    if input_data.output_format != "step":
                        raise ValueError(
                            "FreeCAD's session export only produces STEP; "
                            f"output_format was '{input_data.output_format}'"
                        )
                    (
                        cad_file,
                        volume_mm3,
                        surface_area_mm2,
                        bounding_box,
                    ) = await self._run_freecad_code(script, output_path)
                else:
                    result = await self.context.mcp.invoke(
                        tool_id,
                        {"script": script, "output_path": output_path},
                        timeout=300,
                    )
                    cad_file = result.get("cad_file", "")
                    # CadQuery's adapter may echo back a transformed script
                    # (e.g. sandbox-import-stripped) — that's what actually
                    # ran, so prefer it over the submitted text when present.
                    script_text = result.get("script_text", script)
                    raw_bbox: dict[str, Any] = result.get("bounding_box", {})
                    bounding_box = BoundingBox(
                        min_x=float(raw_bbox.get("min_x", 0.0)),
                        min_y=float(raw_bbox.get("min_y", 0.0)),
                        min_z=float(raw_bbox.get("min_z", 0.0)),
                        max_x=float(raw_bbox.get("max_x", 0.0)),
                        max_y=float(raw_bbox.get("max_y", 0.0)),
                        max_z=float(raw_bbox.get("max_z", 0.0)),
                    )
                    volume_mm3 = float(result.get("volume_mm3", 0.0))
                    surface_area_mm2 = float(result.get("surface_area_mm2", 0.0))
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start

            self.logger.info(
                "CAD script execution complete",
                cad_file=cad_file,
                volume_mm3=volume_mm3,
                backend=backend,
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

    async def _run_freecad_code(
        self, code: str, output_path: str
    ) -> tuple[str, float, float, BoundingBox]:
        """Run ``code`` against a fresh FreeCAD session and export the result to STEP.

        FreeCAD's authoring surface is stateful (unlike CadQuery's one-shot
        ``execute_script``): open a session, run the code against its live
        document (the code must assign its output to a variable named
        ``result``, same convention as CadQuery), measure and export that
        object, then always close the session.

        Returns:
            Tuple of ``(cad_file, volume_mm3, surface_area_mm2, bounding_box)``.
        """
        mcp = self.context.mcp
        session = await mcp.invoke("freecad.open_session", {}, timeout=60)
        session_id = session.get("session_id")
        if not session_id:
            raise RuntimeError("freecad.open_session did not return a session_id")

        try:
            exec_result = await mcp.invoke(
                "freecad.execute_code",
                {"session_id": session_id, "code": code},
                timeout=300,
            )
            obj_id = exec_result.get("obj_id")
            if not obj_id:
                raise RuntimeError(
                    "freecad.execute_code produced no result object — the script "
                    "must assign its output to a variable named 'result'"
                )

            measurements = await mcp.invoke(
                "freecad.measure", {"session_id": session_id, "obj_id": obj_id}, timeout=60
            )
            volume_mm3 = float(measurements.get("volume_mm3", 0.0))
            surface_area_mm2 = float(measurements.get("surface_area_mm2", 0.0))
            raw_bbox: dict[str, Any] = measurements.get("bounding_box", {})
            bounding_box = BoundingBox(
                min_x=float(raw_bbox.get("min_x", 0.0)),
                min_y=float(raw_bbox.get("min_y", 0.0)),
                min_z=float(raw_bbox.get("min_z", 0.0)),
                max_x=float(raw_bbox.get("max_x", 0.0)),
                max_y=float(raw_bbox.get("max_y", 0.0)),
                max_z=float(raw_bbox.get("max_z", 0.0)),
            )

            export_result = await mcp.invoke(
                "freecad.export_model",
                {"session_id": session_id, "obj_id": obj_id},
                timeout=120,
            )
            step_b64 = export_result.get("step_base64")
            if not step_b64:
                raise RuntimeError("freecad.export_model returned no step_base64")
        finally:
            # Best-effort — a stuck session shouldn't mask a successful
            # export, but leaking sessions is a real resource cost.
            try:
                await mcp.invoke("freecad.close_session", {"session_id": session_id}, timeout=30)
            except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
                self.logger.warning(
                    "freecad_session_close_failed", session_id=session_id, error=str(exc)
                )

        content = base64.b64decode(step_b64)
        self._write_output(output_path, content)
        return output_path, volume_mm3, surface_area_mm2, bounding_box

    @staticmethod
    def _write_output(output_path: str, content: bytes) -> None:
        """Write exported CAD bytes to the shared workspace, creating parent dirs."""
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(content)

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
            "import cadquery as cq\n"
            "\n"
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
