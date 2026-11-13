"""A STEP model as triangles grouped by the colour its maker gave each face (2026-11-12).

``convert.py`` draws one colour per product: it reads a part's own colour and drops every
colour set on a face, and pythonocc's named reader fills a part with no colour with a grey
of its own. A maker's board (blue solder mask, gold pads, a black screen) then came out
grey. This reader walks the STEP's assembly through OCCT's XDE layer and gives every face
the colour the file states for it, in this order:

1. the face's own colour (a style on the face, or on a sub-shape that holds it);
2. the colour of the placed instance of the part;
3. the part's own colour;
4. the nearest enclosing assembly's colour;

and no colour at all when the file states none (``colour: None``, ``colour_source:
"none"``). Nothing is made up. Colours are returned as sRGB screen values from 0 to 1,
which is how a STEP file writes them; OCCT holds them as linear light internally.

Output: one group per part and colour, ``{"name", "colour", "colour_source", "positions",
"indices"}``, with positions flat ``[x, y, z, ...]`` in millimetres in the file's own axes.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("occt-converter.faces")

_KINDS = None  # (gen, surf, curv), filled on first use


def _kinds() -> tuple[Any, Any, Any]:
    global _KINDS
    if _KINDS is None:
        from OCC.Core.XCAFDoc import XCAFDoc_ColorCurv, XCAFDoc_ColorGen, XCAFDoc_ColorSurf

        # pythonocc's label overloads take the plain integer, not the enum member.
        _KINDS = (int(XCAFDoc_ColorGen), int(XCAFDoc_ColorSurf), int(XCAFDoc_ColorCurv))
    return _KINDS


def _srgb(colour: Any) -> list[float]:
    from OCC.Core.Quantity import Quantity_TOC_sRGB

    r, g, b = colour.Values(Quantity_TOC_sRGB)
    return [round(float(r), 4), round(float(g), 4), round(float(b), 4)]


def _label_colour(color_tool: Any, label: Any) -> list[float] | None:
    """The surface colour a label states, else its general colour, else None."""
    from OCC.Core.Quantity import Quantity_Color
    from OCC.Core.XCAFDoc import XCAFDoc_ColorTool

    gen, surf, _ = _kinds()
    for kind in (surf, gen):
        c = Quantity_Color()
        # The label form is static in OCCT 7.7; called on the instance it is refused.
        if XCAFDoc_ColorTool.GetColor(label, kind, c):
            return _srgb(c)
    return None


def _name(label: Any) -> str:
    try:
        return str(label.GetLabelName()).strip()
    except Exception:  # noqa: BLE001
        return ""


def _face_colours(
    shape_tool: Any, color_tool: Any, part: Any, shape: Any
) -> dict[int, list[float]]:
    """Face index (in the part's face map) -> the colour its own style states."""
    from OCC.Core.TDF import TDF_LabelSequence
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer, topexp
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape

    fmap = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, TopAbs_FACE, fmap)
    found: dict[int, list[float]] = {}
    subs = TDF_LabelSequence()
    shape_tool.GetSubShapes(part, subs)
    # A face's own style wins over a style on a larger sub-shape that holds it.
    styled: list[tuple[bool, list[float], Any]] = []
    for i in range(1, subs.Length() + 1):
        lab = subs.Value(i)
        colour = _label_colour(color_tool, lab)
        if colour is None:
            continue
        sub = shape_tool.GetShape(lab)
        styled.append((sub.ShapeType() == TopAbs_FACE, colour, sub))
    styled.sort(key=lambda row: row[0])  # larger sub-shapes first, faces last
    for _, colour, sub in styled:
        exp = TopExp_Explorer(sub, TopAbs_FACE)
        while exp.More():
            index = fmap.FindIndex(exp.Current())
            if index:
                found[index] = colour
            exp.Next()
    return found


def _triangles(face: Any, trsf: Any) -> tuple[list[float], list[int]]:
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopAbs import TopAbs_REVERSED
    from OCC.Core.TopLoc import TopLoc_Location

    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation(face, loc)
    if tri is None:
        return [], []
    local = loc.Transformation()
    pts: list[float] = []
    for i in range(1, tri.NbNodes() + 1):
        p = tri.Node(i).Transformed(local).Transformed(trsf)
        pts += [p.X(), p.Y(), p.Z()]
    flip = face.Orientation() == TopAbs_REVERSED
    idx: list[int] = []
    for i in range(1, tri.NbTriangles() + 1):
        a, b, c = tri.Triangle(i).Get()
        idx += [a - 1, c - 1, b - 1] if flip else [a - 1, b - 1, c - 1]
    return pts, idx


def read_coloured_faces(
    path: str, deflection: float, part: str | None = None
) -> list[dict[str, Any]]:
    """Every part of the STEP at ``path``, split into one group per stated colour.

    With ``part``, only the first product of that name (any depth, case-insensitive) is
    read, at its own origin: a chip taken out of a maker's board model. No match gives [].
    """
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
    from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopLoc import TopLoc_Location
    from OCC.Core.TopTools import TopTools_IndexedMapOfShape
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document("metaforge-faces")
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file: {path}")
    reader.Transfer(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

    meshed: set[int] = set()
    out: list[dict[str, Any]] = []

    def emit(part: Any, loc: Any, name: str, inherited: list[float] | None,
             instance: list[float] | None) -> None:  # fmt: skip
        shape = shape_tool.GetShape(part)
        if shape.IsNull():
            return
        key = shape.__hash__()
        if key not in meshed:
            BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)
            meshed.add(key)
        own = _label_colour(color_tool, part)
        by_face = _face_colours(shape_tool, color_tool, part, shape)
        fmap = TopTools_IndexedMapOfShape()
        topexp.MapShapes(shape, TopAbs_FACE, fmap)
        trsf = loc.Transformation()
        groups: dict[tuple[Any, str], dict[str, Any]] = {}
        for index in range(1, fmap.Size() + 1):
            if index in by_face:
                colour, source = by_face[index], "face"
            elif instance is not None:
                colour, source = instance, "instance"
            elif own is not None:
                colour, source = own, "part"
            elif inherited is not None:
                colour, source = inherited, "assembly"
            else:
                colour, source = None, "none"
            group_key = (tuple(colour) if colour else None, source)
            group = groups.setdefault(
                group_key,
                {"name": name, "colour": colour, "colour_source": source,
                 "positions": [], "indices": []},
            )  # fmt: skip
            pts, idx = _triangles(fmap.FindKey(index), trsf)
            base = len(group["positions"]) // 3
            group["positions"] += pts
            group["indices"] += [base + i for i in idx]
        out.extend(g for g in groups.values() if g["indices"])

    def visit(label: Any, loc: Any, name: str, inherited: list[float] | None,
              instance: list[float] | None) -> None:  # fmt: skip
        if shape_tool.IsAssembly(label):
            here = _label_colour(color_tool, label) or inherited
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(label, comps)
            for i in range(1, comps.Length() + 1):
                comp = comps.Value(i)
                ref = TDF_Label()
                if not shape_tool.GetReferredShape(comp, ref):
                    continue
                visit(ref, loc.Multiplied(shape_tool.GetLocation(comp)),
                      _name(ref) or _name(comp), here,
                      _label_colour(color_tool, comp))  # fmt: skip
            return
        emit(label, loc, name or _name(label), inherited, instance)

    if part:
        from OCC.Core.TDF import TDF_ChildIterator

        want = part.strip().lower()
        found = None
        it = TDF_ChildIterator(shape_tool.BaseLabel(), True)
        while it.More() and found is None:
            lab = it.Value()
            if _name(lab).lower() == want:
                ref = TDF_Label()
                referred = shape_tool.IsReference(lab) and shape_tool.GetReferredShape(lab, ref)
                found = ref if referred else lab
            it.Next()
        if found is None:
            logger.info("No part named %r in %s", part, path)
            return []
        visit(found, TopLoc_Location(), _name(found), _label_colour(color_tool, found), None)
        logger.info("Read %d coloured groups of part %r from %s", len(out), part, path)
        return out
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    for i in range(1, roots.Length() + 1):
        root = roots.Value(i)
        visit(root, TopLoc_Location(), _name(root), None, None)
    logger.info("Read %d coloured groups from %s", len(out), path)
    return out
