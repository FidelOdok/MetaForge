"""FORGE-505: the FEA mesh must be in the twin's coordinate frame.

Live finding: a part authored on a rotated plane was committed with twin bbox
x 194..206, y 0..220, z -120..0 but meshed with (x, y, z) = twin (y, z, x),
because the STEP carries the Placement as an assembly transform that gmsh's
reader drops. Pure-Python parts run everywhere; the end-to-end test needs
FreeCAD and gmsh and is skipped without them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tool_registry.tools.freecad.operations import (
    HAS_FREECAD,
    FreecadOperations,
    _bboxes_match,
    _inp_node_bbox,
    _surface_sets_from_faces,
)

# Asymmetric, off-origin extents: x 194..206 (12), y 0..220, z -120..0.
_TWIN_BBOX = {"min": [194.0, 0.0, -120.0], "max": [206.0, 220.0, 0.0]}

# The same part as the frame-dropped mesh: (x, y, z) = twin (y, z, x).
_PERMUTED_INP = """\
*NODE
1, 0, -120, 194
2, 220, -120, 194
3, 220, 0, 206
4, 0, 0, 206
*ELEMENT, type=CPS3, ELSET=Surface3
1, 1, 2, 3
2, 1, 3, 4
"""


def test_bboxes_match_distinguishes_permuted_axes() -> None:
    permuted = {"min": [0.0, -120.0, 194.0], "max": [220.0, 0.0, 206.0]}
    assert _bboxes_match(_TWIN_BBOX, _TWIN_BBOX, 0.01)
    assert not _bboxes_match(_TWIN_BBOX, permuted, 0.01)


def test_inp_node_bbox_reads_mesh_frame(tmp_path: Path) -> None:
    inp = tmp_path / "m.inp"
    inp.write_text(_PERMUTED_INP, encoding="utf-8")
    assert _inp_node_bbox(str(inp)) == {"min": [0.0, -120.0, 194.0], "max": [220.0, 0.0, 206.0]}


def test_surface_sets_are_keyed_by_name_with_bbox() -> None:
    faces = [
        {
            "name": "Surface3",
            "bbox_mm": {"min": [194.0, 0.0, -120.0], "max": [194.0, 220.0, 0.0]},
            "centroid_mm": [194.0, 110.0, -60.0],
            "area_mm2": 1.0,
            "normal": [-1.0, 0.0, 0.0],
            "num_nodes": 4,
        }
    ]
    sets = _surface_sets_from_faces(faces)
    assert set(sets) == {"Surface3"}
    assert sets["Surface3"]["bbox_mm"]["min"] == [194.0, 0.0, -120.0]


@pytest.mark.skipif(
    not HAS_FREECAD or shutil.which("gmsh") is None, reason="needs FreeCAD and gmsh"
)
def test_mesh_bbox_equals_twin_bbox_for_rotated_asymmetric_part(tmp_path: Path) -> None:
    import FreeCAD
    import Part

    ops = FreecadOperations(work_dir=str(tmp_path))
    doc = FreeCAD.newDocument("frame505")
    try:
        box = doc.addObject("Part::Feature", "Bracket")
        # local box 220 x 120 x 12 rotated so the thin axis lands on global x
        # (cyclic permutation, as a sketch on the YZ plane gives), then offset.
        box.Shape = Part.makeBox(220, 120, 12)
        box.Placement = FreeCAD.Placement(
            FreeCAD.Vector(194, 0, -120), FreeCAD.Rotation(FreeCAD.Vector(1, 1, 1), 120)
        )
        doc.recompute()
        bb = box.Shape.BoundBox
        twin = {"min": [bb.XMin, bb.YMin, bb.ZMin], "max": [bb.XMax, bb.YMax, bb.ZMax]}
        step = tmp_path / "bracket.step"
        step.write_bytes(ops.export_object_step_bytes(box))
    finally:
        FreeCAD.closeDocument(doc.Name)

    result = ops.generate_mesh(str(step), element_size=6.0)
    assert result["coordinate_frame"] == "twin"
    assert _bboxes_match(twin, _inp_node_bbox(result["mesh_file"]), 0.05)
    assert _bboxes_match(twin, result["mesh_bbox_mm"], 0.05)
    for entry in result["surface_sets"].values():
        lo, hi = entry["bbox_mm"]["min"], entry["bbox_mm"]["max"]
        assert all(
            twin["min"][i] - 0.05 <= lo[i] <= hi[i] <= twin["max"][i] + 0.05 for i in range(3)
        )


@pytest.mark.skipif(not HAS_FREECAD, reason="needs FreeCAD")
def test_export_round_trip_frame_matches_live_bbox_after_transform(tmp_path: Path) -> None:
    """export -> stored bytes -> reload: the file's own frame equals the live,
    placed bbox the twin records (FORGE-505, transform_object placements)."""
    import FreeCAD
    import Part

    ops = FreecadOperations(work_dir=str(tmp_path))
    doc = FreeCAD.newDocument("rt505")
    try:
        obj = doc.addObject("Part::Feature", "Gusset")
        obj.Shape = Part.makeBox(220, 120, 12)
        obj.Placement = FreeCAD.Placement(
            FreeCAD.Vector(194, 0, -120), FreeCAD.Rotation(FreeCAD.Vector(1, 1, 1), 120)
        )
        doc.recompute()
        live = ops.shape_props(obj)["bounding_box"]
        step = ops.export_object_step_bytes(obj)
    finally:
        FreeCAD.closeDocument(doc.Name)
    stored = ops.measure_step_bytes(step)["bounding_box"]
    assert stored == live
    assert stored["min_x"] == 194.0 and stored["max_y"] == 220.0 and stored["min_z"] == -120.0
