"""Unit tests for the generate_cad_script skill."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
import structlog

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from .handler import GenerateCadScriptHandler
from .schema import GenerateCadScriptInput

SCRIPT_RESULT = {
    "cad_file": "output/script_result.step",
    "script_text": "import cadquery as cq\nresult = cq.Workplane('XY').box(50, 30, 20)\n",
    "volume_mm3": 30000.0,
    "surface_area_mm2": 6200.0,
    "bounding_box": {
        "min_x": -25.0,
        "min_y": -15.0,
        "min_z": -10.0,
        "max_x": 25.0,
        "max_y": 15.0,
        "max_z": 10.0,
    },
}


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="test-script-model",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/test_script.step",
        content_hash="sha256:test456",
        format="step",
        created_by="human",
        metadata={},
    )


async def _make_ctx_and_handler(
    register_cadquery: bool = True,
) -> tuple[SkillContext, GenerateCadScriptHandler, WorkProduct]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    if register_cadquery:
        mcp.register_tool(
            "cadquery.execute_script", capability="cad_scripting", name="Execute Script"
        )
        mcp.register_tool_response("cadquery.execute_script", SCRIPT_RESULT)

    work_product = await twin.create_work_product(_make_work_product())

    ctx = SkillContext(
        twin=twin,
        mcp=mcp,
        logger=structlog.get_logger().bind(skill="generate_cad_script"),
        session_id=uuid4(),
        branch="main",
    )
    handler = GenerateCadScriptHandler(ctx)
    return ctx, handler, work_product


def _register_freecad_session_tools(mcp: InMemoryMcpBridge, *, obj_id: str = "feature_1") -> None:
    """Register the 4-call FreeCAD session sequence _run_freecad_code drives."""
    mcp.register_tool("freecad.open_session", capability="cad_session", name="Open Session")
    mcp.register_tool_response("freecad.open_session", {"session_id": "sess-1"})

    mcp.register_tool("freecad.execute_code", capability="cad_scripting", name="Execute Code")
    mcp.register_tool_response("freecad.execute_code", {"executed": True, "obj_id": obj_id})

    mcp.register_tool("freecad.measure", capability="cad_inspect", name="Measure")
    mcp.register_tool_response(
        "freecad.measure",
        {
            "volume_mm3": 8000.0,
            "surface_area_mm2": 2400.0,
            "bounding_box": {
                "min_x": 0.0,
                "min_y": 0.0,
                "min_z": 0.0,
                "max_x": 20.0,
                "max_y": 20.0,
                "max_z": 20.0,
            },
        },
    )

    mcp.register_tool("freecad.export_model", capability="cad_export", name="Export Model")
    mcp.register_tool_response(
        "freecad.export_model", {"step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii")}
    )

    mcp.register_tool("freecad.close_session", capability="cad_session", name="Close Session")
    mcp.register_tool_response("freecad.close_session", {})


class TestGenerateCadScriptHandler:
    """Unit tests for GenerateCadScriptHandler."""

    async def test_execute_basic(self):
        """Happy path: generate and execute a CadQuery script."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A simple rectangular box 50x30x20mm",
                constraints={"length": 50.0, "width": 30.0, "height": 20.0},
            )
        )

        assert output.cad_file == "output/script_result.step"
        assert output.volume_mm3 == 30000.0
        assert output.surface_area_mm2 == 6200.0
        assert "cq.Workplane" in output.script_text or "cadquery" in output.script_text.lower()

    async def test_execute_with_material(self):
        """Script generation includes material metadata."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A mounting bracket",
                material="stainless_steel_304",
            )
        )

        assert output.cad_file == "output/script_result.step"

    async def test_build_script_includes_description(self):
        """The built script includes a comment with the description."""
        _ctx, handler, _wp = await _make_ctx_and_handler()

        script = handler._build_script(
            "A custom mounting plate", {"length": 100.0, "width": 50.0, "height": 5.0}
        )

        assert "A custom mounting plate" in script
        assert "100.0" in script
        assert "result" in script

    async def test_execute_with_script_passthrough(self):
        """When script is provided, it is sent to the tool as-is, bypassing _build_script."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        custom_script = "result = cq.Workplane('XY').cylinder(20, 10)"
        output = await handler.execute(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A cylinder",
                script=custom_script,
            )
        )
        assert output.cad_file == "output/script_result.step"
        tool_id, params = ctx.mcp.calls[-1]
        assert tool_id == "cadquery.execute_script"
        assert params["script"] == custom_script

    async def test_execute_without_script_uses_fallback(self):
        """When script is omitted, the deterministic box builder is sent instead."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        await handler.execute(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A simple box",
                constraints={"length": 50.0, "width": 30.0, "height": 20.0},
            )
        )
        _tool_id, params = ctx.mcp.calls[-1]
        assert "cq.Workplane" in params["script"]
        assert "50.0" in params["script"]

    async def test_preconditions_missing_tool(self):
        """Precondition check fails when no CAD scripting backend is registered."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_work_product())

        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad_script"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadScriptHandler(ctx)

        errors = await handler.validate_preconditions(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A box",
            )
        )
        assert any("No CAD scripting backend" in e for e in errors)

    async def test_execute_freecad_backend(self, tmp_path, monkeypatch):
        """Happy path: run a FreeCAD script through the 4-call session sequence."""
        monkeypatch.chdir(tmp_path)  # _write_output writes a real file; keep it out of the repo
        ctx, handler, work_product = await _make_ctx_and_handler(register_cadquery=False)
        _register_freecad_session_tools(ctx.mcp)
        custom_code = "result = doc.addObject('Part::Box', 'Box')"

        output = await handler.execute(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A 20mm cube",
                script=custom_code,
                backend="freecad",
            )
        )

        assert output.volume_mm3 == 8000.0
        assert output.surface_area_mm2 == 2400.0
        assert output.bounding_box.max_x == 20.0
        assert output.script_text == custom_code
        assert output.cad_file  # written to disk by _write_output

        called_tool_ids = [tool_id for tool_id, _params in ctx.mcp.calls]
        assert called_tool_ids == [
            "freecad.open_session",
            "freecad.execute_code",
            "freecad.measure",
            "freecad.export_model",
            "freecad.close_session",
        ]
        _tool_id, exec_params = ctx.mcp.calls[1]
        assert exec_params["code"] == custom_code
        assert exec_params["session_id"] == "sess-1"

    async def test_freecad_backend_wrong_output_format_raises(self):
        """FreeCAD's session export is STEP-only; a non-step format is rejected up front."""
        ctx, handler, work_product = await _make_ctx_and_handler(register_cadquery=False)
        _register_freecad_session_tools(ctx.mcp)

        with pytest.raises(ValueError, match="STEP"):
            await handler.execute(
                GenerateCadScriptInput(
                    work_product_id=work_product.id,
                    description="A cube",
                    script="result = doc.addObject('Part::Box', 'Box')",
                    backend="freecad",
                    output_format="stl",
                )
            )

    async def test_no_backend_available_raises(self):
        """Raises RuntimeError when neither cadquery nor freecad is registered."""
        _ctx, handler, work_product = await _make_ctx_and_handler(register_cadquery=False)

        with pytest.raises(RuntimeError, match="No CAD backend available"):
            await handler.execute(
                GenerateCadScriptInput(
                    work_product_id=work_product.id,
                    description="A box",
                )
            )

    async def test_preconditions_missing_artifact(self):
        """Precondition check fails when work_product is missing."""
        _ctx, handler, _wp = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            GenerateCadScriptInput(
                work_product_id=uuid4(),
                description="A box",
            )
        )
        assert any("not found" in e for e in errors)

    async def test_run_pipeline(self):
        """Full skill pipeline (preconditions -> execute -> wrap)."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        result = await handler.run(
            GenerateCadScriptInput(
                work_product_id=work_product.id,
                description="A simple box",
                constraints={"length": 50.0, "width": 30.0, "height": 20.0},
            )
        )

        assert result.success is True
        assert result.data is not None
        assert result.duration_ms > 0

    async def test_validate_output_empty_file(self):
        """Output validation catches empty CAD file path."""
        from .schema import GenerateCadScriptOutput

        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_output(
            GenerateCadScriptOutput(
                work_product_id=work_product.id,
                cad_file="",
                script_text="import cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)\n",
                volume_mm3=1.0,
                surface_area_mm2=6.0,
            )
        )
        assert any("empty" in e for e in errors)
