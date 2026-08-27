"""Unit tests for domain_agents/shared/cadquery_lowering.py (requirements doc §6.6.2, MET-689)."""

from __future__ import annotations

import base64

import pytest

from domain_agents.shared.cadquery_lowering import LoweringError, lower_design_ir_cadquery
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.design_ir import DesignIR


def _script_response(**overrides: object) -> dict:
    response: dict = {
        "step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii"),
        "volume_mm3": 32000.0,
        "surface_area_mm2": 6400.0,
        "bounding_box": {
            "min_x": 0.0,
            "min_y": 0.0,
            "min_z": 0.0,
            "max_x": 40.0,
            "max_y": 20.0,
            "max_z": 40.0,
        },
        "script_text": "",
    }
    response.update(overrides)
    return response


def _bridge(**overrides: object) -> InMemoryMcpBridge:
    mcp = InMemoryMcpBridge()
    mcp.register_tool("cadquery.execute_script", capability="cad_script")
    mcp.register_tool_response("cadquery.execute_script", _script_response(**overrides))
    return mcp


class TestHappyPath:
    async def test_lowers_a_bare_primitive(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 40.0, "width": 20.0, "height": 40.0},
                }
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)

        assert result.step_bytes == b"ISO-10303-21;"
        assert result.volume_mm3 == 32000.0
        assert result.bounding_box["max_z"] == 40.0
        assert result.terminal_entity_id == "box1"
        assert result.obj_id_map == {"box1": "v_box1"}
        assert mcp.calls == [("cadquery.execute_script", {"script": result.script_text})]
        assert "cq.Workplane().box(40.0, 20.0, 40.0, centered=(False, False, False))" in (
            result.script_text
        )
        assert result.script_text.strip().splitlines()[-1] == "result = v_box1"

    async def test_transform_rebinds_both_its_own_id_and_the_target_ref(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {"id": "t1", "op": "transform", "target_ref": "box1", "position": (5.0, 0.0, 0.0)},
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)

        assert result.terminal_entity_id == "t1"
        assert result.obj_id_map["box1"] == "v_t1"
        assert result.obj_id_map["t1"] == "v_t1"
        assert "v_t1 = v_box1.translate((5.0, 0.0, 0.0))" in result.script_text

    async def test_boolean_union(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {
                    "id": "cyl1",
                    "op": "create_primitive",
                    "kind": "cylinder",
                    "parameters": {"radius": 3.0, "height": 20.0},
                },
                {
                    "id": "u1",
                    "op": "boolean",
                    "operation": "union",
                    "base_ref": "box1",
                    "tool_refs": ["cyl1"],
                },
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)

        assert result.terminal_entity_id == "u1"
        assert "v_u1 = v_box1.union(v_cyl1)" in result.script_text
        assert "cq.Workplane().cylinder(20.0, 3.0, centered=(True, True, False))" in (
            result.script_text
        )

    async def test_sketch_pad_pocket(self):
        mcp = _bridge()
        doc = DesignIR(
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
                {
                    "id": "pad1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "depth": 10.0,
                },
                {
                    "id": "sk2",
                    "op": "sketch",
                    "body_ref": "body1",
                    "plane": "XY",
                    "offset": 10.0,
                    "elements": [{"type": "circle", "center": (20.0, 10.0), "radius": 3.0}],
                },
                {
                    "id": "pk1",
                    "op": "pocket",
                    "body_ref": "body1",
                    "sketch_ref": "sk2",
                    "depth": 5.0,
                },
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)

        assert result.terminal_entity_id == "pk1"
        assert "v_pad1 = v_sk1.extrude(10.0, both=False)" in result.script_text
        # Pocket cuts INTO the body (opposite direction from pad's default
        # growth away from the sketch plane) -- regression for a live e2e
        # bug where the same sign as pad extruded the cutter away from the
        # body with zero overlap, so .cut() silently removed nothing.
        assert "v_pk1_cutter = v_sk2.extrude(-5.0)" in result.script_text
        assert "v_pk1 = v_pad1.cut(v_pk1_cutter)" in result.script_text
        # create_body has no shape of its own -- excluded from obj_id_map's
        # script-variable entries entirely (it never produces one).
        assert "body1" not in result.obj_id_map


class TestRejections:
    async def test_pocket_before_any_pad_on_that_body_raises_and_makes_no_mcp_call(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "elements": [{"type": "circle", "center": (0.0, 0.0), "radius": 3.0}],
                },
                {
                    "id": "pk1",
                    "op": "pocket",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "depth": 5.0,
                },
            ]
        )
        with pytest.raises(LoweringError, match="pad a sketch first"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_sketch_arc_rejected(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "elements": [
                        {
                            "type": "arc",
                            "center": (0.0, 0.0),
                            "radius": 3.0,
                            "start_angle": 0.0,
                            "end_angle": 90.0,
                        }
                    ],
                },
                {"id": "pad1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 5.0},
            ]
        )
        with pytest.raises(LoweringError, match="SketchArc"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_transform_rotation_rejected(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {
                    "id": "t1",
                    "op": "transform",
                    "target_ref": "box1",
                    "rotation": (0.0, 0.0, 90.0),
                },
            ]
        )
        with pytest.raises(LoweringError, match="rotation"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_boolean_with_multiple_tool_refs_rejected(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {
                    "id": "box2",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {
                    "id": "box3",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {
                    "id": "u1",
                    "op": "boolean",
                    "operation": "union",
                    "base_ref": "box1",
                    "tool_refs": ["box2", "box3"],
                },
            ]
        )
        with pytest.raises(LoweringError, match="exactly one tool_ref"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_unsupported_op_rejected_before_any_mcp_call(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "elements": [
                        {"type": "rectangle", "origin": (0.0, 0.0), "width": 10.0, "height": 10.0}
                    ],
                },
                {
                    "id": "loft1",
                    "op": "loft",
                    "body_ref": "body1",
                    "profile_ref": "sk1",
                    "section_refs": ["sk1"],
                },
            ]
        )
        with pytest.raises(LoweringError, match="unsupported op"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_document_with_no_exportable_terminal_rejected(self):
        mcp = _bridge()
        doc = DesignIR(entities=[{"id": "body1", "op": "create_body"}])
        with pytest.raises(LoweringError, match="no exportable terminal"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_invalid_ir_rejected_before_any_mcp_call(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "t1",
                    "op": "transform",
                    "target_ref": "does_not_exist",
                }
            ]
        )
        with pytest.raises(LoweringError, match="invalid Design IR"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_missing_step_base64_rejected(self):
        mcp = _bridge()
        mcp.register_tool_response(
            "cadquery.execute_script",
            {
                "volume_mm3": 1.0,
                "surface_area_mm2": 1.0,
                "bounding_box": {},
            },
        )
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 1.0, "width": 1.0, "height": 1.0},
                }
            ]
        )
        with pytest.raises(LoweringError, match="no step_base64"):
            await lower_design_ir_cadquery(mcp, doc)


def _body_with_pad_doc(*extra: dict) -> DesignIR:
    """A create_body -> sketch -> pad document, with extra entities appended
    (all operating on the same body_ref/body1, matching the pad it produces)."""
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
            {"id": "pad1", "op": "pad", "body_ref": "body1", "sketch_ref": "sk1", "depth": 10.0},
            *extra,
        ]
    )


class TestFilletChamferMirrorShell:
    async def test_plain_fillet_on_a_boolean_result(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {"id": "f1", "op": "fillet", "target_ref": "box1", "radius": 1.0},
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "f1"
        assert "v_f1 = v_box1.fillet(1.0)" in result.script_text

    async def test_plain_chamfer(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {
                    "id": "box1",
                    "op": "create_primitive",
                    "kind": "box",
                    "parameters": {"length": 10.0, "width": 10.0, "height": 10.0},
                },
                {"id": "c1", "op": "chamfer", "target_ref": "box1", "distance": 0.5},
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "c1"
        assert "v_c1 = v_box1.chamfer(0.5)" in result.script_text

    async def test_fillet_edges_empty_selector(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {"id": "fe1", "op": "fillet_edges", "body_ref": "body1", "radius": 2.0}
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "fe1"
        assert "v_fe1 = v_pad1.fillet(2.0)" in result.script_text

    async def test_fillet_edges_nonempty_selector_rejected(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {
                "id": "fe1",
                "op": "fillet_edges",
                "body_ref": "body1",
                "radius": 2.0,
                "edge_selectors": ["Edge3"],
            }
        )
        with pytest.raises(LoweringError, match="edge_selectors"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_fillet_edges_with_no_prior_pad_rejected(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "fe1", "op": "fillet_edges", "body_ref": "body1", "radius": 2.0},
            ]
        )
        with pytest.raises(LoweringError, match="pad a sketch first"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_chamfer_edges_empty_selector(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {"id": "ce1", "op": "chamfer_edges", "body_ref": "body1", "distance": 1.5}
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "ce1"
        assert "v_ce1 = v_pad1.chamfer(1.5)" in result.script_text

    async def test_chamfer_edges_nonempty_selector_rejected(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {
                "id": "ce1",
                "op": "chamfer_edges",
                "body_ref": "body1",
                "distance": 1.5,
                "edge_selectors": ["Edge5"],
            }
        )
        with pytest.raises(LoweringError, match="edge_selectors"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_mirror_mirrors_source_ref_and_unions_onto_the_body(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {"id": "m1", "op": "mirror", "body_ref": "body1", "source_ref": "pad1", "plane": "YZ"}
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "m1"
        assert (
            "v_m1_mirrored = v_pad1.mirror(mirrorPlane='YZ', "
            "basePointVector=(0.0, 0.0, 0.0))" in result.script_text
        )
        assert "v_m1 = v_pad1.union(v_m1_mirrored)" in result.script_text

    async def test_mirror_with_no_prior_pad_rejected(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "elements": [{"type": "circle", "center": (0.0, 0.0), "radius": 3.0}],
                },
                {"id": "m1", "op": "mirror", "body_ref": "body1", "source_ref": "sk1"},
            ]
        )
        with pytest.raises(LoweringError, match="pad a sketch first"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_shell_empty_selector(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {"id": "sh1", "op": "shell", "body_ref": "body1", "thickness": 2.0}
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "sh1"
        # Negative thickness shells INWARD (hollows into the solid).
        assert "v_sh1 = v_pad1.shell(-2.0)" in result.script_text

    async def test_shell_nonempty_selector_rejected(self):
        mcp = _bridge()
        doc = _body_with_pad_doc(
            {
                "id": "sh1",
                "op": "shell",
                "body_ref": "body1",
                "thickness": 2.0,
                "face_selectors": ["Face2"],
            }
        )
        with pytest.raises(LoweringError, match="face_selectors"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []

    async def test_shell_with_no_prior_pad_rejected(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {"id": "sh1", "op": "shell", "body_ref": "body1", "thickness": 2.0},
            ]
        )
        with pytest.raises(LoweringError, match="pad a sketch first"):
            await lower_design_ir_cadquery(mcp, doc)
        assert mcp.calls == []


class TestRevolve:
    async def test_revolve_as_the_first_feature_of_a_body(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "plane": "XY",
                    "elements": [
                        {"type": "rectangle", "origin": (10.0, 2.0), "width": 20.0, "height": 5.0}
                    ],
                },
                {
                    "id": "rev1",
                    "op": "revolve",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "axis": "H",
                    "angle": 360.0,
                },
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "rev1"
        assert "v_rev1_axis_start = v_sk1.plane.origin" in result.script_text
        assert "v_rev1_axis_end = v_rev1_axis_start + v_sk1.plane.xDir" in result.script_text
        assert (
            "v_rev1 = v_sk1.revolve(360.0, "
            "(v_rev1_axis_start.x, v_rev1_axis_start.y, v_rev1_axis_start.z), "
            "(v_rev1_axis_end.x, v_rev1_axis_end.y, v_rev1_axis_end.z))" in result.script_text
        )
        # First feature of the body -- no union with a nonexistent prior solid.
        assert "union" not in result.script_text

    async def test_revolve_vertical_axis_reversed(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "elements": [
                        {"type": "rectangle", "origin": (10.0, 2.0), "width": 20.0, "height": 5.0}
                    ],
                },
                {
                    "id": "rev1",
                    "op": "revolve",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "axis": "V",
                    "angle": 180.0,
                    "reversed": True,
                },
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert "v_rev1_axis_end = v_rev1_axis_start - v_sk1.plane.yDir" in result.script_text
        assert "v_sk1.revolve(180.0," in result.script_text

    async def test_revolve_as_a_subsequent_feature_unions_onto_the_body(self):
        mcp = _bridge()
        doc = DesignIR(
            entities=[
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sk1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "elements": [
                        {"type": "rectangle", "origin": (0.0, 0.0), "width": 40.0, "height": 20.0}
                    ],
                },
                {
                    "id": "pad1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "sk1",
                    "depth": 10.0,
                },
                {
                    "id": "sk2",
                    "op": "sketch",
                    "body_ref": "body1",
                    "offset": 10.0,
                    "elements": [
                        {"type": "rectangle", "origin": (10.0, 2.0), "width": 20.0, "height": 5.0}
                    ],
                },
                {
                    "id": "rev1",
                    "op": "revolve",
                    "body_ref": "body1",
                    "sketch_ref": "sk2",
                },
            ]
        )
        result = await lower_design_ir_cadquery(mcp, doc)
        assert result.terminal_entity_id == "rev1"
        assert "v_rev1_revolved = v_sk2.revolve(360.0," in result.script_text
        assert "v_rev1 = v_pad1.union(v_rev1_revolved)" in result.script_text
