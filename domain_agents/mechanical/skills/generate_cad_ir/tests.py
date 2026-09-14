"""Unit tests for the generate_cad_ir skill."""

from __future__ import annotations

import base64
from uuid import uuid4

import structlog

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from .handler import GenerateCadIrHandler
from .schema import GenerateCadIrInput

_BRACKET_ENTITIES = [
    {"id": "body1", "op": "create_body"},
    {
        "id": "sk1",
        "op": "sketch",
        "body_ref": "body1",
        "plane": "XY",
        "elements": [{"type": "rectangle", "origin": (0.0, 0.0), "width": 40.0, "height": 20.0}],
    },
    {"id": "sol1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 10.0},
    {"id": "sol2", "op": "fillet_edges", "body_ref": "body1", "radius": 2.0},
]


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="test-ir-model",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/test_ir.step",
        content_hash="sha256:test789",
        format="step",
        created_by="human",
        metadata={},
    )


_BOX_ENTITY = [
    {
        "id": "box1",
        "op": "create_primitive",
        "kind": "box",
        "parameters": {"length": 40.0, "width": 20.0, "height": 10.0},
    }
]


def _register_cadquery_script_tool(mcp: InMemoryMcpBridge) -> None:
    mcp.register_tool("cadquery.execute_script", capability="cad_script")
    mcp.register_tool_response(
        "cadquery.execute_script",
        {
            "step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii"),
            "volume_mm3": 8000.0,
            "surface_area_mm2": 2800.0,
            "bounding_box": {
                "min_x": 0.0,
                "min_y": 0.0,
                "min_z": 0.0,
                "max_x": 40.0,
                "max_y": 20.0,
                "max_z": 10.0,
            },
            "script_text": "",
        },
    )


def _register_freecad_session_tools(mcp: InMemoryMcpBridge) -> None:
    mcp.register_tool("freecad.open_session", capability="cad_session")
    mcp.register_tool_response("freecad.open_session", {"session_id": "sess-1"})
    mcp.register_tool("freecad.close_session", capability="cad_session")
    mcp.register_tool_response("freecad.close_session", {})
    mcp.register_tool("freecad.create_body", capability="cad_author")
    mcp.register_tool_response("freecad.create_body", {"obj_id": "body_1"})
    mcp.register_tool("freecad.create_sketch", capability="cad_author")
    mcp.register_tool_response("freecad.create_sketch", {"obj_id": "sketch_1"})
    mcp.register_tool("freecad.pad_sketch", capability="cad_author")
    mcp.register_tool_response("freecad.pad_sketch", {"obj_id": "pad_1"})
    mcp.register_tool("freecad.fillet_edges", capability="cad_author")
    mcp.register_tool_response("freecad.fillet_edges", {"obj_id": "fillet_1"})
    mcp.register_tool("freecad.measure", capability="cad_inspect")
    mcp.register_tool_response(
        "freecad.measure",
        {
            "volume_mm3": 7800.0,
            "surface_area_mm2": 2500.0,
            "bounding_box": {
                "min_x": 0.0,
                "min_y": 0.0,
                "min_z": 0.0,
                "max_x": 40.0,
                "max_y": 20.0,
                "max_z": 10.0,
            },
        },
    )
    mcp.register_tool("freecad.export_model", capability="cad_export")
    mcp.register_tool_response(
        "freecad.export_model",
        {"step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii")},
    )


async def _make_ctx_and_handler() -> tuple[SkillContext, GenerateCadIrHandler, WorkProduct]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    _register_freecad_session_tools(mcp)

    work_product = await twin.create_work_product(_make_work_product())

    ctx = SkillContext(
        twin=twin,
        mcp=mcp,
        logger=structlog.get_logger().bind(skill="generate_cad_ir"),
        session_id=uuid4(),
        branch="main",
    )
    handler = GenerateCadIrHandler(ctx)
    return ctx, handler, work_product


class TestGenerateCadIrHandler:
    """Unit tests for GenerateCadIrHandler."""

    async def test_execute_bracket(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # _write_output writes a real file; keep it out of the repo
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadIrInput(work_product_id=work_product.id, entities=_BRACKET_ENTITIES)
        )

        assert output.entity_count == 4
        assert output.volume_mm3 == 7800.0
        assert output.surface_area_mm2 == 2500.0
        assert output.bounding_box.max_x == 40.0
        assert output.obj_id_map["sol2"] == "fillet_1"
        assert output.cad_file  # written to disk by _write_output

    async def test_invalid_entities_rejected_before_any_mcp_call(self):
        ctx, handler, work_product = await _make_ctx_and_handler()

        try:
            await handler.execute(
                GenerateCadIrInput(
                    work_product_id=work_product.id,
                    entities=[
                        {
                            "id": "sol1",
                            "op": "pad",
                            "body_ref": "ghost",
                            "sketch_ref": "sk1",
                            "depth": 5.0,
                        }
                    ],
                )
            )
            raised = False
        except ValueError:
            raised = True

        assert raised
        assert ctx.mcp.calls == []

    async def test_create_parametric_rejected(self):
        ctx, handler, work_product = await _make_ctx_and_handler()

        try:
            await handler.execute(
                GenerateCadIrInput(
                    work_product_id=work_product.id,
                    entities=[{"id": "p1", "op": "create_parametric", "shape_type": "bracket"}],
                )
            )
            raised = False
        except ValueError as exc:
            raised = "unsupported op" in str(exc)

        assert raised
        assert ctx.mcp.calls == []

    async def test_preconditions_missing_tool(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_work_product())
        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad_ir"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadIrHandler(ctx)

        errors = await handler.validate_preconditions(
            GenerateCadIrInput(work_product_id=work_product.id, entities=_BRACKET_ENTITIES)
        )
        assert any("FreeCAD session API" in e for e in errors)

    async def test_preconditions_missing_artifact(self):
        _ctx, handler, _wp = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            GenerateCadIrInput(work_product_id=uuid4(), entities=_BRACKET_ENTITIES)
        )
        assert any("not found" in e for e in errors)

    async def test_commit_skipped_when_tool_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadIrInput(work_product_id=work_product.id, entities=_BRACKET_ENTITIES)
        )

        assert output.committed is False
        assert output.commit_error == "twin.commit_geometry tool is not available"

    async def test_commit_geometry_invoked_when_available(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx, handler, work_product = await _make_ctx_and_handler()
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry")
        ctx.mcp.register_tool_response(
            "twin.commit_geometry",
            {"node_id": "node-456", "model_url": "https://twin.local/models/node-456"},
        )

        output = await handler.execute(
            GenerateCadIrInput(
                work_product_id=work_product.id,
                entities=_BRACKET_ENTITIES,
                project_id="13d60463-433b-4735-af07-690cbf8e07b9",
            )
        )

        assert output.committed is True
        assert output.twin_node_id == "node-456"
        assert output.model_url == "https://twin.local/models/node-456"
        assert output.commit_error is None

    async def test_commit_false_skips_persistence(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx, handler, work_product = await _make_ctx_and_handler()
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry")
        ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-456"})

        output = await handler.execute(
            GenerateCadIrInput(
                work_product_id=work_product.id, entities=_BRACKET_ENTITIES, commit=False
            )
        )

        assert output.committed is False
        assert output.commit_error is None

    async def test_run_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _ctx, handler, work_product = await _make_ctx_and_handler()

        result = await handler.run(
            GenerateCadIrInput(work_product_id=work_product.id, entities=_BRACKET_ENTITIES)
        )

        assert result.success is True
        assert result.data is not None
        assert result.duration_ms > 0
        assert result.errors == []

    async def test_validate_output_zero_volume(self):
        from .schema import BoundingBox, GenerateCadIrOutput

        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_output(
            GenerateCadIrOutput(
                work_product_id=work_product.id,
                cad_file="output/test.step",
                entity_count=1,
                volume_mm3=0.0,
                surface_area_mm2=10.0,
                bounding_box=BoundingBox(),
                material="aluminum_6061",
            )
        )
        assert any("volume" in e.lower() for e in errors)


class TestCadqueryAdapter:
    """adapter="cadquery" dispatches to lower_design_ir_cadquery instead of FreeCAD."""

    async def test_execute_dispatches_to_cadquery(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        _register_cadquery_script_tool(mcp)
        work_product = await twin.create_work_product(_make_work_product())
        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad_ir"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadIrHandler(ctx)

        output = await handler.execute(
            GenerateCadIrInput(
                work_product_id=work_product.id, entities=_BOX_ENTITY, adapter="cadquery"
            )
        )

        assert output.entity_count == 1
        assert output.volume_mm3 == 8000.0
        assert output.obj_id_map == {"box1": "v_box1"}
        # Only the CadQuery tool was ever called -- no FreeCAD session opened.
        assert [tool_id for tool_id, _params in mcp.calls] == ["cadquery.execute_script"]

    async def test_preconditions_check_cadquery_tool_not_freecad(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()  # neither tool registered
        work_product = await twin.create_work_product(_make_work_product())
        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad_ir"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadIrHandler(ctx)

        errors = await handler.validate_preconditions(
            GenerateCadIrInput(
                work_product_id=work_product.id, entities=_BOX_ENTITY, adapter="cadquery"
            )
        )
        assert any("CadQuery script API" in e for e in errors)
        assert not any("FreeCAD" in e for e in errors)

    async def test_preconditions_pass_when_cadquery_tool_available(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        _register_cadquery_script_tool(mcp)
        work_product = await twin.create_work_product(_make_work_product())
        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad_ir"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadIrHandler(ctx)

        errors = await handler.validate_preconditions(
            GenerateCadIrInput(
                work_product_id=work_product.id, entities=_BOX_ENTITY, adapter="cadquery"
            )
        )
        assert errors == []

    async def test_unsupported_op_via_cadquery_rejected_before_any_mcp_call(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        _register_cadquery_script_tool(mcp)
        work_product = await twin.create_work_product(_make_work_product())
        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad_ir"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadIrHandler(ctx)

        try:
            await handler.execute(
                GenerateCadIrInput(
                    work_product_id=work_product.id,
                    entities=[{"id": "p1", "op": "create_parametric", "shape_type": "bracket"}],
                    adapter="cadquery",
                )
            )
            raised = False
        except ValueError as exc:
            raised = "unsupported op" in str(exc)

        assert raised
        assert mcp.calls == []
