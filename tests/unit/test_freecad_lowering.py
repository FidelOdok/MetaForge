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

    async def test_sketch_elements_translated_to_the_flat_shape_the_adapter_expects(self):
        # Regression (MET-682): the pre-fix code dumped each IR sketch-element
        # model as-is (el.model_dump()) straight into "elements" -- IR field
        # names (center/radius, origin, start/end tuples) don't match what
        # tool_registry.tools.freecad.operations.FreecadOperations
        # ._add_sketch_element actually reads (flat cx/cy/r, x/y, x1/y1/x2/y2).
        # A real InMemoryMcpBridge test double can't catch this -- it returns
        # a canned response regardless of what's sent -- so this test asserts
        # on the *outgoing* call payload directly, which is what a real
        # FreeCAD adapter would have KeyError'd on.
        mcp = _bracket_bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "plane": "XY",
                    "elements": [
                        {"type": "rectangle", "origin": (1.0, 2.0), "width": 40.0, "height": 20.0},
                        {"type": "circle", "center": (5.0, 6.0), "radius": 3.0},
                        {"type": "line", "start": (0.0, 0.0), "end": (10.0, 10.0)},
                    ],
                },
                {
                    "id": "sol1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "depth": 10.0,
                },
            ]
        )

        await lower_design_ir_freecad(mcp, doc)

        calls = dict(mcp.calls)
        elements = calls["freecad.create_sketch"]["elements"]
        assert elements[0] == {
            "type": "rectangle",
            "x": 1.0,
            "y": 2.0,
            "width": 40.0,
            "height": 20.0,
        }
        assert elements[1] == {"type": "circle", "cx": 5.0, "cy": 6.0, "r": 3.0}
        assert elements[2] == {"type": "line", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0}

    async def test_document_ending_in_joint_picks_the_last_real_shape_as_terminal(self):
        # Regression (MET-682): freecad.add_assembly_joint registers its
        # obj_id against a Python None (a joint is pure kinematics metadata,
        # not a FreeCAD object with a .Shape). The pre-fix _NO_SHAPE_OPS
        # didn't exclude "joint", so a document assembled part -> part ->
        # joint (the natural shape of an assembly script, confirmed live via
        # a real text-to-CAD eval run) picked the joint as terminal and
        # freecad.measure/export_model failed with "nothing to export: None
        # has no exportable geometry" against a real FreeCAD adapter. A real
        # InMemoryMcpBridge test double can't catch this on its own -- it
        # returns a canned response regardless of what's sent -- so this
        # test asserts on which obj_id ends up as the *terminal* (the one
        # sent to freecad.measure/export_model), not just that lowering
        # doesn't raise.
        mcp = InMemoryMcpBridge()
        _register(mcp, "freecad.open_session", {"session_id": "sess-1"})
        _register(mcp, "freecad.create_primitive", {"obj_id": "prim_1"})
        _register(mcp, "freecad.create_assembly", {"obj_id": "asm_1"})
        _register(mcp, "freecad.add_part_to_assembly", {})
        _register(mcp, "freecad.add_assembly_joint", {"obj_id": "joint_1"})
        _register(
            mcp,
            "freecad.measure",
            {"volume_mm3": 500.0, "surface_area_mm2": 300.0, "bounding_box": {}},
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
                {"id": "asm", "op": "create_assembly"},
                {"id": "place1", "op": "place", "assembly_ref": "asm", "part_ref": "p1"},
                {
                    "id": "j1",
                    "op": "joint",
                    "assembly_ref": "asm",
                    "part_a_ref": "p1",
                    "part_b_ref": "p1",
                    "joint_type": "fixed",
                },
            ]
        )
        result = await lower_design_ir_freecad(mcp, doc)

        assert result.terminal_entity_id == "place1"
        calls = dict(mcp.calls)
        assert calls["freecad.measure"]["obj_id"] == "prim_1"
        assert calls["freecad.export_model"]["obj_id"] == "prim_1"

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
