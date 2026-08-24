"""Unit tests for domain_agents/shared/freecad_lowering.py (requirements doc §6.6.2)."""

from __future__ import annotations

import base64

import pytest

from domain_agents.shared.freecad_lowering import LoweringError, lower_design_ir_freecad
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.design_ir import DesignIR


def _register(mcp: InMemoryMcpBridge, tool_id: str, response: dict) -> None:
    mcp.register_tool(tool_id, capability="cad_session")
    mcp.register_tool_response(tool_id, response)


def _bracket_bridge() -> InMemoryMcpBridge:
    """Wires the exact session sequence a body -> sketch -> pad -> fillet_edges
    document should drive."""
    mcp = InMemoryMcpBridge()
    _register(mcp, "freecad.open_session", {"session_id": "sess-1"})
    _register(mcp, "freecad.create_body", {"obj_id": "body_1", "kind": "body"})
    _register(mcp, "freecad.create_sketch", {"obj_id": "sketch_1", "kind": "sketch"})
    _register(mcp, "freecad.pad_sketch", {"obj_id": "pad_1", "kind": "feature"})
    _register(mcp, "freecad.fillet_edges", {"obj_id": "fillet_1", "kind": "feature"})
    _register(
        mcp,
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
    _register(
        mcp,
        "freecad.export_model",
        {"step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii")},
    )
    _register(mcp, "freecad.close_session", {})
    return mcp


def _bracket_doc() -> DesignIR:
    return DesignIR(
        entities=[
            {"id": "body1", "op": "create_body"},
            {
                "id": "sk1",
                "op": "sketch",
                "body_ref": "body1",
                "plane": "XY",
                "elements": [
                    {"type": "rectangle", "origin": (0.0, 0.0), "width": 40.0, "height": 20.0}
                ],
            },
            {"id": "sol1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 10.0},
            {
                "id": "sol2",
                "op": "fillet_edges",
                "body_ref": "body1",
                "radius": 2.0,
                "edge_selectors": ["Edge3"],
            },
        ]
    )


class TestHappyPath:
    async def test_lowers_body_sketch_pad_fillet(self):
        mcp = _bracket_bridge()
        result = await lower_design_ir_freecad(mcp, _bracket_doc())

        assert result.step_bytes == b"ISO-10303-21;"
        assert result.volume_mm3 == 7800.0
        assert result.surface_area_mm2 == 2500.0
        assert result.bounding_box["max_x"] == 40.0
        assert result.terminal_entity_id == "sol2"
        assert result.obj_id_map == {
            "body1": "body_1",
            "sk1": "sketch_1",
            "sol1": "pad_1",
            "sol2": "fillet_1",
        }

    async def test_calls_are_in_document_order_and_session_always_closed(self):
        mcp = _bracket_bridge()
        await lower_design_ir_freecad(mcp, _bracket_doc())

        tool_ids = [tool_id for tool_id, _params in mcp.calls]
        assert tool_ids == [
            "freecad.open_session",
            "freecad.create_body",
            "freecad.create_sketch",
            "freecad.pad_sketch",
            "freecad.fillet_edges",
            "freecad.measure",
            "freecad.export_model",
            "freecad.close_session",
        ]

    async def test_refs_resolved_to_freecad_obj_ids_on_the_wire(self):
        mcp = _bracket_bridge()
        await lower_design_ir_freecad(mcp, _bracket_doc())

        calls = dict(mcp.calls)
        # dict() collapses same-tool-id repeats, fine here since each tool fires once
        assert calls["freecad.create_sketch"]["body_id"] == "body_1"
        assert calls["freecad.pad_sketch"]["body_id"] == "body_1"
        assert calls["freecad.pad_sketch"]["sketch_id"] == "sketch_1"
        assert calls["freecad.pad_sketch"]["length"] == 10.0
        assert calls["freecad.fillet_edges"]["body_id"] == "body_1"
        assert calls["freecad.fillet_edges"]["edges"] == ["Edge3"]

    async def test_transform_mutates_in_place_no_new_obj_id(self):
        mcp = InMemoryMcpBridge()
        _register(mcp, "freecad.open_session", {"session_id": "sess-1"})
        _register(mcp, "freecad.create_primitive", {"obj_id": "prim_1"})
        _register(mcp, "freecad.transform_object", {})  # real tool returns no obj_id
        _register(
            mcp,
            "freecad.measure",
            {"volume_mm3": 1000.0, "surface_area_mm2": 600.0, "bounding_box": {}},
        )
        _register(
            mcp,
            "freecad.export_model",
            {"step_base64": base64.b64encode(b"STEP").decode("ascii")},
        )
        _register(mcp, "freecad.close_session", {})

        doc = DesignIR(
            entities=[
                {"id": "p1", "op": "create_primitive", "kind": "box"},
                {"id": "t1", "op": "transform", "target_ref": "p1", "position": (10.0, 0.0, 0.0)},
            ]
        )
        result = await lower_design_ir_freecad(mcp, doc)
        assert result.obj_id_map["t1"] == "prim_1"
        assert result.terminal_entity_id == "t1"


class TestRejections:
    async def test_invalid_ir_rejected_before_any_mcp_call(self):
        mcp = InMemoryMcpBridge()
        doc = DesignIR(
            entities=[
                {"id": "sol1", "op": "pad", "body_ref": "ghost", "sketch_ref": "sk1", "depth": 5.0}
            ]
        )
        with pytest.raises(LoweringError, match="invalid Design IR"):
            await lower_design_ir_freecad(mcp, doc)
        assert mcp.calls == []

    async def test_create_parametric_rejected(self):
        mcp = InMemoryMcpBridge()
        doc = DesignIR(entities=[{"id": "p1", "op": "create_parametric", "shape_type": "bracket"}])
        with pytest.raises(LoweringError, match="unsupported op"):
            await lower_design_ir_freecad(mcp, doc)
        assert mcp.calls == []

    async def test_no_exportable_terminal_entity_rejected(self):
        mcp = InMemoryMcpBridge()
        doc = DesignIR(entities=[{"id": "body1", "op": "create_body"}])
        with pytest.raises(LoweringError, match="no exportable terminal entity"):
            await lower_design_ir_freecad(mcp, doc)
        assert mcp.calls == []

    async def test_empty_document_rejected(self):
        mcp = InMemoryMcpBridge()
        with pytest.raises(LoweringError, match="no exportable terminal entity"):
            await lower_design_ir_freecad(mcp, DesignIR())

    async def test_boolean_with_multiple_tool_refs_rejected(self):
        mcp = InMemoryMcpBridge()
        _register(mcp, "freecad.open_session", {"session_id": "sess-1"})
        _register(mcp, "freecad.create_primitive", {"obj_id": "prim_1"})
        doc = DesignIR(
            entities=[
                {"id": "p1", "op": "create_primitive", "kind": "box"},
                {"id": "p2", "op": "create_primitive", "kind": "cylinder"},
                {"id": "p3", "op": "create_primitive", "kind": "sphere"},
                {
                    "id": "b1",
                    "op": "boolean",
                    "operation": "union",
                    "base_ref": "p1",
                    "tool_refs": ["p2", "p3"],
                },
            ]
        )
        with pytest.raises(LoweringError, match="exactly one tool_ref"):
            await lower_design_ir_freecad(mcp, doc)

    async def test_transform_rotation_rejected(self):
        mcp = InMemoryMcpBridge()
        _register(mcp, "freecad.open_session", {"session_id": "sess-1"})
        _register(mcp, "freecad.create_primitive", {"obj_id": "prim_1"})
        doc = DesignIR(
            entities=[
                {"id": "p1", "op": "create_primitive", "kind": "box"},
                {
                    "id": "t1",
                    "op": "transform",
                    "target_ref": "p1",
                    "rotation": (0.0, 0.0, 45.0),
                },
            ]
        )
        with pytest.raises(LoweringError, match="rotation is not supported"):
            await lower_design_ir_freecad(mcp, doc)
