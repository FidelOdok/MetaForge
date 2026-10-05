"""FORGE-517: material appearance table and STEP colour authoring.

Pure-Python parts run everywhere. The FreeCAD end-to-end tests need the adapter
image (``HAS_FREECAD``) and are skipped in CI; run them in the deployed
freecad-adapter container the way ``test_freecad_mesh_frame.py`` documents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tool_registry.tools.freecad.materials_appearance import (
    apply_step_colours,
    lookup_material_rgb,
    resolve_rgb,
)
from tool_registry.tools.freecad.operations import HAS_FREECAD, FreecadOperations

_STEP = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1 = APPLICATION_CONTEXT('x');
#2 = PRODUCT('Board','Board','',(#3));
#3 = PRODUCT_CONTEXT('',#1,'mechanical');
#4 = PRODUCT_DEFINITION_FORMATION('','',#2);
#5 = PRODUCT_DEFINITION('design','',#4,#6);
#6 = PRODUCT_DEFINITION_CONTEXT('part definition',#1,'design');
#7 = PRODUCT_DEFINITION_SHAPE('','',#5);
#8 = SHAPE_DEFINITION_REPRESENTATION(#7,#9);
#9 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#10,#11),#30);
#10 = AXIS2_PLACEMENT_3D('',#40,#41,#42);
#11 = MANIFOLD_SOLID_BREP('',#12);
#12 = CLOSED_SHELL('',());
#20 = PRODUCT('Bracket','Bracket','',(#3));
#21 = PRODUCT_DEFINITION_FORMATION('','',#20);
#22 = PRODUCT_DEFINITION('design','',#21,#6);
#23 = PRODUCT_DEFINITION_SHAPE('','',#22);
#24 = SHAPE_DEFINITION_REPRESENTATION(#23,#25);
#25 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#10,#26),#30);
#26 = MANIFOLD_SOLID_BREP('',#12);
#30 = GEOMETRIC_REPRESENTATION_CONTEXT(3);
ENDSEC;
END-ISO-10303-21;
"""


def _colour_of(step: bytes, name_solid_id: int) -> tuple[float, float, float] | None:
    """Colour attached to a solid via STYLED_ITEM -> ... -> COLOUR_RGB."""
    text = step.decode()
    m = re.search(rf"STYLED_ITEM\('',\(#(\d+)\),#{name_solid_id}\);", text)
    if not m:
        return None
    cur = m.group(1)
    for _ in range(6):  # walk PSA -> usage -> side -> fill -> style -> colour
        nxt = re.search(rf"#{cur} = [A-Z_]+\([^#]*\(?#(\d+)", text)
        assert nxt, text
        cur = nxt.group(1)
    rgb = re.search(rf"#{cur} = COLOUR_RGB\('',([\d.]+),([\d.]+),([\d.]+)\);", text)
    assert rgb
    return (float(rgb.group(1)), float(rgb.group(2)), float(rgb.group(3)))


class TestMaterialTable:
    @pytest.mark.parametrize(
        ("material", "expected_high"),
        [
            ("18 mm birch plywood", "r"),
            ("Baltic Birch Ply", "r"),
            ("PETG", "b"),
            ("aluminium 6061-T6", None),
        ],
    )
    def test_known_materials_resolve(self, material: str, expected_high: str | None) -> None:
        rgb = lookup_material_rgb(material)
        assert rgb is not None
        assert all(0.0 <= c <= 1.0 for c in rgb)
        if expected_high == "r":
            assert rgb[0] > rgb[2]  # warm tan
        if expected_high == "b":
            assert rgb[2] > rgb[0]

    def test_birch_plywood_is_more_specific_than_plywood(self) -> None:
        assert lookup_material_rgb("birch plywood") != lookup_material_rgb("plywood")

    def test_case_insensitive_and_substring(self) -> None:
        assert lookup_material_rgb("STEEL") == lookup_material_rgb("mild steel plate")
        assert lookup_material_rgb("pla") == lookup_material_rgb("PLA filament")

    def test_pla_does_not_match_inside_other_words(self) -> None:
        assert lookup_material_rgb("plastic") is None
        assert lookup_material_rgb("plywood") == lookup_material_rgb("Plywood 12mm")

    @pytest.mark.parametrize("material", [None, "", "unobtainium", "kryptonite 9"])
    def test_unknown_material_returns_none(self, material: str | None) -> None:
        assert lookup_material_rgb(material) is None

    def test_explicit_colour_wins_and_accepts_255_range(self) -> None:
        assert resolve_rgb([255, 0, 0], "PETG") == (1.0, 0.0, 0.0)
        assert resolve_rgb([0.1, 0.2, 0.3], None) == (0.1, 0.2, 0.3)
        assert resolve_rgb(None, "unobtainium") is None

    def test_bad_colour_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_rgb([1, 2], None)
        with pytest.raises(ValueError):
            resolve_rgb([-1, 0, 0], None)


class TestApplyStepColours:
    def test_no_colour_leaves_bytes_untouched(self) -> None:
        data = _STEP.encode()
        assert apply_step_colours(data) == data
        assert apply_step_colours(data, None, {}) == data

    def test_default_colour_applies_to_every_solid(self) -> None:
        out = apply_step_colours(_STEP.encode(), (0.87, 0.74, 0.54))
        assert _colour_of(out, 11) == (0.87, 0.74, 0.54)
        assert _colour_of(out, 26) == (0.87, 0.74, 0.54)
        assert b"MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION" in out
        assert out.rstrip().endswith(b"END-ISO-10303-21;")

    def test_assembly_keeps_per_part_colours(self) -> None:
        out = apply_step_colours(
            _STEP.encode(), None, {"Board": (0.87, 0.74, 0.54), "Bracket": (0.78, 0.79, 0.81)}
        )
        assert _colour_of(out, 11) == (0.87, 0.74, 0.54)
        assert _colour_of(out, 26) == (0.78, 0.79, 0.81)

    def test_part_without_material_stays_uncoloured(self) -> None:
        out = apply_step_colours(_STEP.encode(), None, {"Board": (0.87, 0.74, 0.54)})
        assert _colour_of(out, 11) == (0.87, 0.74, 0.54)
        assert _colour_of(out, 26) is None

    def test_part_override_beats_default(self) -> None:
        out = apply_step_colours(_STEP.encode(), (0.1, 0.1, 0.1), {"Bracket": (0.9, 0.9, 0.9)})
        assert _colour_of(out, 11) == (0.1, 0.1, 0.1)
        assert _colour_of(out, 26) == (0.9, 0.9, 0.9)

    def test_unknown_part_name_colours_nothing(self) -> None:
        data = _STEP.encode()
        assert apply_step_colours(data, None, {"Nope": (1.0, 0.0, 0.0)}) == data

    def test_new_entity_ids_do_not_collide(self) -> None:
        out = apply_step_colours(_STEP.encode(), (0.5, 0.5, 0.5)).decode()
        ids = re.findall(r"^#(\d+) =", out, re.MULTILINE)
        assert len(ids) == len(set(ids))


@pytest.mark.skipif(not HAS_FREECAD, reason="needs FreeCAD")
def test_freecad_assembly_export_keeps_per_part_colours_and_geometry(tmp_path: Path) -> None:
    """Real Import.export STEP of a two-part assembly (with a placed part, so
    the FORGE-505 bake runs) coloured per part, still readable as solids."""
    import FreeCAD
    import Part

    ops = FreecadOperations(work_dir=str(tmp_path))
    doc = FreeCAD.newDocument("col517")
    try:
        asm = doc.addObject("App::Part", "Shelf")
        board = doc.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(100, 20, 10)
        brk = doc.addObject("Part::Feature", "Bracket")
        brk.Shape = Part.makeBox(10, 10, 30)
        brk.Placement = FreeCAD.Placement(
            FreeCAD.Vector(5, 0, 0), FreeCAD.Rotation(FreeCAD.Vector(1, 1, 1), 120)
        )
        asm.addObject(board)
        asm.addObject(brk)
        doc.recompute()
        step = ops.export_object_step_bytes(asm)
    finally:
        FreeCAD.closeDocument(doc.Name)

    tan, grey = (0.87, 0.74, 0.54), (0.78, 0.79, 0.81)
    coloured = apply_step_colours(step, None, {"Board": tan, "Bracket": grey})
    text = coloured.decode()
    assert text.count("COLOUR_RGB") == 2
    assert "0.870000,0.740000,0.540000" in text
    assert "0.780000,0.790000,0.810000" in text
    assert text.count("STYLED_ITEM") == 2
    # geometry unchanged and still readable by a placement-blind reader
    assert ops.measure_step_bytes(coloured) == ops.measure_step_bytes(step)


@pytest.mark.skipif(not HAS_FREECAD, reason="needs FreeCAD")
def test_freecad_single_part_material_colour_survives_bake(tmp_path: Path) -> None:
    import FreeCAD
    import Part

    ops = FreecadOperations(work_dir=str(tmp_path))
    doc = FreeCAD.newDocument("mat517")
    try:
        obj = doc.addObject("Part::Feature", "Panel")
        obj.Shape = Part.makeBox(220, 120, 12)
        obj.Placement = FreeCAD.Placement(
            FreeCAD.Vector(194, 0, -120), FreeCAD.Rotation(FreeCAD.Vector(1, 1, 1), 120)
        )
        doc.recompute()
        step = ops.export_object_step_bytes(obj)  # takes the FORGE-505 bake path
    finally:
        FreeCAD.closeDocument(doc.Name)
    rgb = lookup_material_rgb("18 mm birch plywood")
    assert rgb is not None
    out = apply_step_colours(step, rgb)
    assert b"COLOUR_RGB('',0.870000,0.740000,0.540000)" in out
    assert apply_step_colours(step, lookup_material_rgb("unobtainium")) == step
