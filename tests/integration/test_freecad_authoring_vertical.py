"""FreeCAD authoring vertical — integration test against REAL FreeCAD (MET-527).

Drives ``FreecadOperations`` through the full PartDesign authoring surface
(primitive → body/sketch/pad/pocket/revolve/loft/sweep → transform →
fillet/chamfer → patterns/mirror → assembly → parametric → inspection → STEP
export) against a real FreeCAD runtime.

Skipped automatically when FreeCAD bindings aren't importable, so it's a no-op
in CI (which has no FreeCAD). To run it against the real runtime, execute inside
the freecad-adapter container::

    docker run -i --rm --entrypoint /usr/bin/python3 \
        metaforge/freecad-adapter:latest -m pytest \
        tests/integration/test_freecad_authoring_vertical.py -v

This is the permanent form of the in-container validation that caught the
interpreter mismatch (MET-527/PR #344), the multi-body origin-Role bug, and the
shell/Thickness-fails-headless issue (MET-533) during development.
"""

from __future__ import annotations

import pytest

from tool_registry.tools.freecad import operations as ops_mod
from tool_registry.tools.freecad.operations import FreecadOperations

pytestmark = pytest.mark.skipif(
    not (ops_mod.HAS_FREECAD and ops_mod.HAS_PARTDESIGN),
    reason="FreeCAD + PartDesign/Sketcher workbenches unavailable (run in the freecad container)",
)


@pytest.fixture()
def doc():  # type: ignore[no-untyped-def]
    import FreeCAD  # type: ignore[import-untyped]

    document = FreeCAD.newDocument("authoring_vertical")
    yield document
    FreeCAD.closeDocument(document.Name)


@pytest.fixture()
def ops() -> FreecadOperations:
    return FreecadOperations(work_dir="/tmp/freecad")


def _box_body(ops: FreecadOperations, doc, name: str, w: float = 20.0, h: float = 10.0):  # type: ignore[no-untyped-def]
    body = ops.create_body(doc, name)
    sk = ops.create_sketch(
        doc, body, "XY", [{"type": "rectangle", "x": 0, "y": 0, "width": w, "height": w}]
    )
    ops.pad_sketch(doc, body, sk, h)
    return body


class TestAuthoringVertical:
    def test_primitive_has_volume(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        box = ops.create_primitive(doc, "box", {"length": 20, "width": 10, "height": 5})
        assert ops.shape_props(box)["volume_mm3"] == pytest.approx(1000.0, abs=1.0)

    def test_body_sketch_pad_export(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = _box_body(ops, doc, "Main")
        assert ops.shape_props(body)["volume_mm3"] > 0
        step = ops.export_object_step_bytes(body)
        assert step[:13].decode("ascii", "ignore").startswith("ISO-10303")
        assert len(step) > 100

    def test_pocket(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = _box_body(ops, doc, "Plate", w=30, h=10)
        s2 = ops.create_sketch(doc, body, "XY", [{"type": "circle", "cx": 15, "cy": 15, "r": 5}])
        ops.pocket_sketch(doc, body, s2, 10)
        assert ops.shape_props(body)["volume_mm3"] > 0

    def test_revolve(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = ops.create_body(doc, "Rev")
        sk = ops.create_sketch(
            doc, body, "XZ", [{"type": "rectangle", "x": 5, "y": 0, "width": 5, "height": 20}]
        )
        rev = ops.revolve_sketch(doc, body, sk, 360.0)
        assert rev.isValid()

    def test_fillet_and_chamfer(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        ops.fillet_edges(doc, _box_body(ops, doc, "Fil"), 1.5)
        ops.chamfer_edges(doc, _box_body(ops, doc, "Cha"), 1.0)

    def test_shell(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = _box_body(ops, doc, "Shell", w=20, h=10)
        solid_vol = ops.shape_props(body)["volume_mm3"]
        shell = ops.shell_solid(doc, body, 1.5)
        assert 0 < ops.shape_props(shell)["volume_mm3"] < solid_vol  # hollowed

    def test_patterns_and_mirror(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = _box_body(ops, doc, "Pat", w=60, h=10)
        s2 = ops.create_sketch(doc, body, "XY", [{"type": "circle", "cx": 8, "cy": 8, "r": 2}])
        pk = ops.pocket_sketch(doc, body, s2, 10)
        assert ops.linear_pattern(doc, body, pk, 3, 12, axis="X").isValid()
        assert ops.polar_pattern(doc, body, pk, 4, angle=360, axis="Z").isValid()
        assert ops.mirror_feature(doc, body, pk, plane="YZ").isValid()

    def test_loft(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = ops.create_body(doc, "Loft")
        s1 = ops.create_sketch(
            doc, body, "XY", [{"type": "rectangle", "x": 0, "y": 0, "width": 20, "height": 20}]
        )
        s2 = ops.create_sketch(
            doc,
            body,
            "XY",
            [{"type": "rectangle", "x": 5, "y": 5, "width": 10, "height": 10}],
            offset=20,
        )
        assert ops.loft_sketches(doc, body, s1, [s2]).isValid()
        assert ops.shape_props(body)["volume_mm3"] > 0

    def test_sweep(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = ops.create_body(doc, "Sweep")
        prof = ops.create_sketch(doc, body, "XY", [{"type": "circle", "cx": 0, "cy": 0, "r": 3}])
        path = ops.create_sketch(
            doc, body, "XZ", [{"type": "line", "x1": 0, "y1": 0, "x2": 0, "y2": 30}]
        )
        assert ops.sweep_sketch(doc, body, prof, path).isValid()

    def test_assembly_and_parametric(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        box = ops.create_primitive(doc, "box", {"length": 20, "width": 10, "height": 5})
        asm = ops.create_assembly(doc, "Asm")
        ops.add_part_to_assembly(doc, asm, box, {"position": [0, 0, 0]})
        ops.create_variable_set(doc, "Params", {"w": {"value": 40, "type": "length"}})
        ops.set_expression(doc, box, "Length", "Params.w")
        length = box.Length
        assert (length.Value if hasattr(length, "Value") else float(length)) == pytest.approx(40.0)

    def test_generate_enclosure_skill(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        shell = ops.generate_enclosure(doc, 80, 50, 30, 2.0)
        props = ops.shape_props(shell)
        # A hollow box: smaller than the solid 80*50*30 envelope.
        assert 0 < props["volume_mm3"] < 80 * 50 * 30

    def test_fastener_hole_skill(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = _box_body(ops, doc, "Drilled", w=40, h=20)
        solid = ops.shape_props(body)["volume_mm3"]
        ops.fastener_hole(doc, body, 20, 20, 6, counterbore_diameter=10, counterbore_depth=6)
        assert ops.shape_props(body)["volume_mm3"] < solid  # material removed

    def test_thread_insert_skill(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        body = _box_body(ops, doc, "Bossed", w=40, h=10)
        solid = ops.shape_props(body)["volume_mm3"]
        ops.thread_insert(doc, body, 20, 20, 10, 8, 4, 8)
        # boss adds material then a pilot hole removes some → net still > original solid
        assert ops.shape_props(body)["volume_mm3"] > solid

    def test_generate_gear_skill(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        # Rigor: a correct involute gear's outer (addendum) diameter == module*(teeth+2),
        # so this verifies geometric correctness, not just "a valid solid".
        gear = ops.generate_gear(doc, 2.0, 20, 5.0)
        bb = gear.Shape.BoundBox
        outer = max(bb.XLength, bb.YLength)
        assert gear.Shape.isValid()
        assert abs(outer - 2.0 * (20 + 2)) < 0.5, outer  # addendum diameter = 44.0

    def test_inspection(self, ops: FreecadOperations, doc) -> None:  # type: ignore[no-untyped-def]
        box = ops.create_primitive(doc, "box", {"length": 20, "width": 10, "height": 5})
        m = ops.measure(box)
        assert m["edge_count"] == 12 and m["face_count"] == 6 and m["solid_count"] == 1
        d = ops.describe_model(box)
        assert d["dimensions_mm"]["x"] > 0 and d["solid_count"] == 1
