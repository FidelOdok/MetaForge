"""Each face keeps the colour its maker stated (faces.py, 2026-11-12).

Runs where pythonocc is installed (the converter image). The STEP files are written here
through OCCT's XDE writer, so the colours they hold are known.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("OCC.Core.STEPCAFControl")

from faces import read_coloured_faces  # noqa: E402
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB  # noqa: E402
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer  # noqa: E402
from OCC.Core.STEPControl import STEPControl_AsIs  # noqa: E402
from OCC.Core.TDocStd import TDocStd_Document  # noqa: E402
from OCC.Core.TopAbs import TopAbs_FACE  # noqa: E402
from OCC.Core.TopExp import TopExp_Explorer  # noqa: E402
from OCC.Core.XCAFDoc import (  # noqa: E402
    XCAFDoc_ColorGen,
    XCAFDoc_ColorSurf,
    XCAFDoc_DocumentTool,
)

BLUE = (0.275, 0.333, 0.765)  # the XIAO's solder mask, as its STEP writes it
RED = (0.843, 0.059, 0.059)
GREY = (0.627, 0.627, 0.627)


def srgb(rgb: tuple[float, float, float]) -> Quantity_Color:
    return Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TOC_sRGB)


def write_step(path: Path, *, face_red: bool = True, plain_part: bool = True) -> None:
    """A blue 10 x 20 x 3 box with one red face, and an uncoloured cylinder."""
    doc = TDocStd_Document("test")
    shapes = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    colours = XCAFDoc_DocumentTool.ColorTool(doc.Main())
    box = BRepPrimAPI_MakeBox(10, 20, 3).Shape()
    board = shapes.AddShape(box, False)
    colours.SetColor(board, srgb(BLUE), XCAFDoc_ColorGen)
    if face_red:
        face = TopExp_Explorer(box, TopAbs_FACE).Current()
        sub = shapes.AddSubShape(board, face)
        colours.SetColor(sub, srgb(RED), XCAFDoc_ColorSurf)
    if plain_part:
        shapes.AddShape(BRepPrimAPI_MakeCylinder(2, 5).Shape(), False)
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.Transfer(doc, STEPControl_AsIs)
    writer.Write(str(path))


def close(a: list[float] | None, b: tuple[float, float, float]) -> bool:
    return a is not None and all(abs(x - y) < 0.01 for x, y in zip(a, b, strict=True))


def test_a_face_keeps_its_own_colour_and_the_rest_keep_the_parts(tmp_path: Path) -> None:
    step = tmp_path / "board.step"
    write_step(step)
    groups = read_coloured_faces(str(step), 0.1)
    red = [g for g in groups if close(g["colour"], RED)]
    blue = [g for g in groups if close(g["colour"], BLUE)]
    assert len(red) == 1 and red[0]["colour_source"] == "face"
    assert len(red[0]["indices"]) // 3 == 2  # one flat face: two triangles
    assert len(blue) == 1 and blue[0]["colour_source"] == "part"
    assert len(blue[0]["indices"]) // 3 == 10  # the other five faces


def test_a_part_with_no_colour_is_given_none_not_a_grey(tmp_path: Path) -> None:
    step = tmp_path / "board.step"
    write_step(step)
    plain = [g for g in read_coloured_faces(str(step), 0.1) if g["colour"] is None]
    assert len(plain) == 1 and plain[0]["colour_source"] == "none"
    assert plain[0]["indices"]


def test_colours_come_back_as_the_screen_values_the_file_holds(tmp_path: Path) -> None:
    step = tmp_path / "grey.step"
    doc = TDocStd_Document("test")
    shapes = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    colours = XCAFDoc_DocumentTool.ColorTool(doc.Main())
    label = shapes.AddShape(BRepPrimAPI_MakeBox(1, 1, 1).Shape(), False)
    colours.SetColor(label, srgb(GREY), XCAFDoc_ColorGen)
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    writer.Write(str(step))
    assert "0.627" in step.read_text(errors="replace")  # the file holds the screen value
    (group,) = read_coloured_faces(str(step), 0.1)
    assert close(group["colour"], GREY)  # not 0.353, its linear-light value


def test_negative_control_the_old_reader_loses_the_red_face(tmp_path: Path) -> None:
    """Why this module exists: convert.py's reader gives each part one colour."""
    from convert import _read_step_named

    step = tmp_path / "board.step"
    write_step(step)
    named = _read_step_named(str(step))
    colours = {tuple(round(c, 2) for c in col) for _, _, col in named if col}
    assert not any(abs(c[0] - RED[0]) < 0.05 and c[1] < 0.1 for c in colours)


def test_the_positions_are_in_millimetres_in_the_files_axes(tmp_path: Path) -> None:
    step = tmp_path / "board.step"
    write_step(step, face_red=False, plain_part=False)
    (group,) = read_coloured_faces(str(step), 0.1)
    xs, ys, zs = group["positions"][0::3], group["positions"][1::3], group["positions"][2::3]
    assert (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) == pytest.approx((10, 20, 3))


def test_a_part_placed_twice_is_drawn_at_both_places_in_its_instance_colour(
    tmp_path: Path,
) -> None:
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.TopLoc import TopLoc_Location

    doc = TDocStd_Document("test")
    shapes = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    colours = XCAFDoc_DocumentTool.ColorTool(doc.Main())
    pin = shapes.AddShape(BRepPrimAPI_MakeBox(1, 1, 1).Shape(), False)
    top = shapes.NewShape()
    shapes.AddComponent(top, pin, TopLoc_Location())
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(10, 0, 0))
    second = shapes.AddComponent(top, pin, TopLoc_Location(move))
    colours.SetColor(second, srgb(RED), XCAFDoc_ColorGen)
    shapes.UpdateAssemblies()
    step = tmp_path / "pins.step"
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    writer.Write(str(step))
    groups = read_coloured_faces(str(step), 0.1)
    xs = [x for g in groups for x in g["positions"][0::3]]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(11)
    red = [g for g in groups if close(g["colour"], RED)]
    assert len(red) == 1 and red[0]["colour_source"] == "instance"
    assert min(red[0]["positions"][0::3]) == pytest.approx(10)
