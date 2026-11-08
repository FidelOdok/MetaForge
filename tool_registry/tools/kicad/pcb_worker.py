"""Board generation and autorouting with KiCad's pcbnew (FORGE-683).

Runs under the system Python that ships KiCad's ``pcbnew`` module, not the
adapter's interpreter, so it imports nothing from MetaForge: it reads one JSON
request on stdin and writes one JSON result on stdout.

``generate``: a ``.kicad_pcb`` from the circuit -- footprints from the KiCad
libraries with their nets, the outline, design rules and net classes, corner
mounting holes (or holes at given positions), inner planes on a 4-layer
board, and a deterministic placement that puts connectors on the left edge,
the main IC at the centre and every other part next to the part it is most
connected to. Placement is a reviewable starting point, not an optimum. A
part with a ``placement`` keeps the position, rotation and side it names; an
``edge_mount`` turns a connector so its mating side faces a board edge and
puts its mouth flush with (or proud of) that edge.

``edit``: an existing board, changed by an ordered list of operations (move,
rotate or place a footprint, set or remove a mounting hole, strip the
routing), saved in place. The caller works on a copy.

``route``: plane-net pads fanned out to vias, the board out as Specctra DSN,
routed by Freerouting (a separate GPL-3 program), the session imported back,
then the ground pour added and filled. Whether the result is complete is for
KiCad's DRC to say, not this script.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from typing import Any

import pcbnew  # type: ignore[import-not-found]

MM = pcbnew.FromMM


def _mm(v: int) -> float:
    return pcbnew.ToMM(v)


# -- generate ---------------------------------------------------------------------


def generate(req: dict[str, Any]) -> dict[str, Any]:
    circuit = req["circuit"]
    board_spec = circuit["board"]
    fp_root = req.get("footprint_root", "/usr/share/kicad/footprints")
    sym_uuids: dict[str, str] = req.get("symbol_uuids", {})
    sheet_file = req.get("sheet_file", "")
    out = req["out_path"]

    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(int(board_spec.get("layers", 2)))
    _rules(board, board_spec)

    # Pad nets from the schematic's own netlist when given (authoritative,
    # including KiCad's unconnected-(...) nets), else from the circuit.
    net_of: dict[str, str] = req.get("pad_nets") or {
        node: n["name"] for n in circuit.get("nets", []) for node in n["nodes"]
    }
    nets: dict[str, Any] = {}
    for name in sorted(set(net_of.values())):
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        nets[name] = item

    footprints: dict[str, Any] = {}
    missing: list[str] = []
    for comp in circuit["components"]:
        lib, name = comp["footprint"].split(":", 1)
        path = os.path.join(fp_root, f"{lib}.pretty")
        fp = pcbnew.FootprintLoad(path, name) if os.path.isdir(path) else None
        if fp is None:
            missing.append(f"{comp['ref']}: footprint {comp['footprint']} not in {fp_root}")
            continue
        fp.SetReference(comp["ref"])
        if comp.get("model"):
            models = fp.Models()
            if len(models):
                models[0].m_Filename = comp["model"]
            else:
                m = pcbnew.FP_3DMODEL()
                m.m_Filename = comp["model"]
                models.append(m)
        fp.SetValue(comp["value"])
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        for key, val in (comp.get("fields") or {}).items():
            fp.SetField(key, val)
            fp.GetFieldByName(key).SetVisible(False)  # data (MPN, ...), not silkscreen
        if comp["ref"] in sym_uuids:
            fp.SetPath(pcbnew.KIID_PATH("/" + sym_uuids[comp["ref"]]))
            fp.SetSheetname("/")
            fp.SetSheetfile(sheet_file)
        pad_numbers = {p.GetNumber() for p in fp.Pads()}
        for pad in fp.Pads():
            net = net_of.get(f"{comp['ref']}.{pad.GetNumber()}")
            if net:
                pad.SetNet(nets[net])
        for node in net_of:
            ref, pin = node.split(".", 1)
            if ref == comp["ref"] and pin not in pad_numbers:
                missing.append(
                    f"{node}: footprint {comp['footprint']} has no pad {pin!r} "
                    f"(pads: {', '.join(sorted(pad_numbers, key=lambda s: (len(s), s)))})"
                )
        board.Add(fp)
        footprints[comp["ref"]] = fp
    if missing:
        return {"ok": False, "errors": missing}

    w, h = float(board_spec["width_mm"]), float(board_spec["height_mm"])
    x0, y0 = 100.0, 100.0
    rect = pcbnew.PCB_SHAPE(board)
    rect.SetShape(pcbnew.SHAPE_T_RECT)
    rect.SetStart(pcbnew.VECTOR2I(MM(x0), MM(y0)))
    rect.SetEnd(pcbnew.VECTOR2I(MM(x0 + w), MM(y0 + h)))
    rect.SetLayer(pcbnew.Edge_Cuts)
    rect.SetWidth(MM(0.1))
    board.Add(rect)

    for layer, net_name in zip(
        (pcbnew.In1_Cu, pcbnew.In2_Cu), board_spec.get("inner_planes") or []
    ):
        if net_name not in nets:
            return {"ok": False, "errors": [f"inner_planes: no net named {net_name!r}"]}
        # A power layer: the Specctra export then hands the zone to the router
        # as a plane, which it reaches with vias rather than tracks.
        board.SetLayerType(layer, pcbnew.LT_POWER)
        board.Add(_zone(board, layer, nets[net_name], inset=0.5))

    keepouts: list[tuple[float, float, float, float]] = []
    hole = board_spec.get("mounting_holes_mm")
    given = board_spec.get("mounting_holes") or []
    if given:
        default_drill = float(hole) if hole else 3.2
        spots = [
            (
                x0 + float(g["position"]["x_mm"]),
                y0 + float(g["position"]["y_mm"]),
                float(g.get("drill_mm") or default_drill),
            )
            for g in given
        ]
        keepouts.extend(_mounting_holes(board, fp_root, spots))
    elif hole:
        keepouts.extend(_mounting_holes(board, fp_root, corner_holes(float(hole), x0, y0, w, h)))

    # Placement hints: side and rotation first, so the auto-placer sizes each
    # part as it will sit; a part with a position (or an edge mount) is fixed.
    fixed: dict[str, tuple[float, float]] = {}
    hinted: dict[str, dict[str, Any]] = {}
    for comp in circuit["components"]:
        hint = comp.get("placement")
        if not hint or comp["ref"] not in footprints:
            continue
        anchor, note = apply_placement(footprints[comp["ref"]], hint, x0, y0, w, h)
        hinted[comp["ref"]] = note
        if anchor is not None:
            fixed[comp["ref"]] = anchor

    power = {*board_spec.get("power_nets", []), *(board_spec.get("inner_planes") or [])}
    if board_spec.get("ground_pour"):
        power.add(board_spec["ground_pour"])
    placement = _place(footprints, net_of, x0, y0, w, h, keepouts, power, fixed)
    for entry in placement["placements"]:
        if entry["ref"] in hinted:
            entry.update(hinted[entry["ref"]])
    pcbnew.SaveBoard(out, board)
    return {
        "ok": True,
        "path": out,
        "footprints": len(footprints),
        "nets": len(nets),
        "unplaced": placement["unplaced"],
        "placements": placement["placements"],
        "placement_warnings": placement["warnings"],
    }


def _rules(board: Any, spec: dict[str, Any]) -> None:
    ds = board.GetDesignSettings()
    ns = ds.m_NetSettings
    nc = ns.GetDefaultNetclass()
    nc.SetClearance(MM(float(spec.get("clearance_mm", 0.2))))
    nc.SetTrackWidth(MM(float(spec.get("track_mm", 0.25))))
    nc.SetViaDiameter(MM(float(spec.get("via_diameter_mm", 0.6))))
    nc.SetViaDrill(MM(float(spec.get("via_drill_mm", 0.3))))
    # The board minimums follow the requested via and track, or DRC rejects
    # every via smaller than KiCad's defaults (0.3 mm hole) as out of range.
    ds.m_MinThroughDrill = min(ds.m_MinThroughDrill, MM(float(spec.get("via_drill_mm", 0.3))))
    ds.m_ViasMinSize = min(ds.m_ViasMinSize, MM(float(spec.get("via_diameter_mm", 0.6))))
    ds.m_TrackMinWidth = min(ds.m_TrackMinWidth, MM(float(spec.get("track_mm", 0.25))))
    if spec.get("hole_clearance_mm"):
        ds.m_HoleClearance = MM(float(spec["hole_clearance_mm"]))
    power = spec.get("power_track_mm")
    if power and spec.get("power_nets"):
        pc = pcbnew.NETCLASS("Power")
        pc.SetClearance(MM(float(spec.get("clearance_mm", 0.2))))
        pc.SetTrackWidth(MM(float(power)))
        pc.SetViaDiameter(MM(float(spec.get("via_diameter_mm", 0.6))))
        pc.SetViaDrill(MM(float(spec.get("via_drill_mm", 0.3))))
        ns.SetNetclass("Power", pc)
        for name in spec["power_nets"]:
            ns.SetNetclassPatternAssignment(name, "Power")


def mounting_hole_name(drill: float, names: list[str]) -> str | None:
    """The plain (unplated, no pad ring) library hole for ``drill``, if there is one.

    KiCad names them ``MountingHole_<d>mm`` with an ``_M<n>`` suffix for the
    screw it clears (``MountingHole_3.2mm_M3``); the suffixed one is preferred.
    """
    best: str | None = None
    for name in sorted(names):
        parts = name.split("_")
        if len(parts) not in (2, 3) or parts[0] != "MountingHole" or not parts[1].endswith("mm"):
            continue
        if len(parts) == 3 and not parts[2].startswith("M"):
            continue
        try:
            size = float(parts[1][:-2])
        except ValueError:
            continue
        if abs(size - drill) < 0.05 and (best is None or len(parts) == 3):
            best = name
    return best


def corner_holes(
    drill: float, x0: float, y0: float, w: float, h: float
) -> list[tuple[float, float, float]]:
    """The four corner holes of ``drill``, inset so the hole clears the edge."""
    inset = drill / 2 + 2.0
    return [
        (x0 + inset, y0 + inset, drill),
        (x0 + w - inset, y0 + inset, drill),
        (x0 + inset, y0 + h - inset, drill),
        (x0 + w - inset, y0 + h - inset, drill),
    ]


def _hole_footprint(board: Any, fp_root: str, drill: float) -> Any:
    lib = os.path.join(fp_root, "MountingHole.pretty")
    names = (
        [f[: -len(".kicad_mod")] for f in os.listdir(lib) if f.endswith(".kicad_mod")]
        if os.path.isdir(lib)
        else []
    )
    name = mounting_hole_name(drill, names)
    fp = pcbnew.FootprintLoad(lib, name) if name else None
    if fp is None:
        fp = pcbnew.FOOTPRINT(board)
        pad = pcbnew.PAD(fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetDrillSize(pcbnew.VECTOR2I(MM(drill), MM(drill)))
        pad.SetSize(pcbnew.VECTOR2I(MM(drill), MM(drill)))
        fp.Add(pad)
        # FORGE-691: a footprint with no library id goes into the Specctra
        # export as a nameless component, and KiCad then refuses the whole
        # routed session on import.
        fp.SetFPID(pcbnew.LIB_ID("MountingHole", f"MountingHole_{drill:g}mm"))
    # Board-only: not in the schematic, the BOM or the placement file, so
    # schematic parity does not count it as an extra footprint.
    fp.SetBoardOnly(True)
    fp.SetExcludedFromBOM(True)
    fp.SetExcludedFromPosFiles(True)
    return fp


def hole_keepout(cx: float, cy: float, drill: float) -> tuple[float, float, float, float]:
    """The square a hole keeps clear: its radius plus 1.5 mm for the screw head."""
    r = drill / 2 + 1.5
    return (cx - r, cy - r, cx + r, cy + r)


def _mounting_holes(
    board: Any, fp_root: str, spots: list[tuple[float, float, float]]
) -> list[tuple[float, float, float, float]]:
    """Mounting holes H1.. at ``(x, y, drill)`` in board mm; their keepouts."""
    boxes = []
    for i, (cx, cy, drill) in enumerate(spots):
        fp = _hole_footprint(board, fp_root, drill)
        fp.SetReference(f"H{i + 1}")
        fp.SetPosition(pcbnew.VECTOR2I(MM(cx), MM(cy)))
        board.Add(fp)
        boxes.append(hole_keepout(cx, cy, drill))
    return boxes


# -- placement hints ---------------------------------------------------------------

#: Unit vectors of the board edges, in KiCad's frame (y runs down).
EDGE_VECTORS: dict[str, tuple[int, int]] = {
    "+x": (1, 0),
    "-x": (-1, 0),
    "+y": (0, 1),
    "-y": (0, -1),
}
_EDGE_TEXT = ("pcb edge", "board edge")


def rotate_point(x: float, y: float, deg: float) -> tuple[float, float]:
    """``(x, y)`` turned by a KiCad orientation: positive is counter-clockwise as drawn.

    The board frame has y running down, so a quarter turn takes +x to -y.
    """
    r = math.radians(deg)
    c, s_ = math.cos(r), math.sin(r)
    return (x * c + y * s_, -x * s_ + y * c)


def side_after_rotation(side: str, deg: float) -> str:
    """The board edge a footprint side faces once the footprint is turned by ``deg``."""
    vx, vy = EDGE_VECTORS[side]
    rx, ry = rotate_point(vx, vy, deg)
    if abs(rx) >= abs(ry):
        return "+x" if rx > 0 else "-x"
    return "+y" if ry > 0 else "-y"


def rotation_for_edge(local_side: str, edge: str) -> float:
    """The orientation (0, 90, 180 or 270) that turns ``local_side`` to face ``edge``."""
    for deg in (0.0, 90.0, 180.0, 270.0):
        if side_after_rotation(local_side, deg) == edge:
            return deg
    raise ValueError(f"no rotation takes {local_side} to {edge}")


def nearest_side(box: tuple[float, float, float, float], x: float, y: float) -> str:
    """The side of ``box`` (x1, y1, x2, y2) nearest the point."""
    x1, y1, x2, y2 = box
    gaps = {"+x": abs(x2 - x), "-x": abs(x - x1), "+y": abs(y2 - y), "-y": abs(y - y1)}
    return min(gaps, key=lambda k: (gaps[k], k))


def mating_side(
    box: tuple[float, float, float, float],
    pad_centroid: tuple[float, float] | None,
    edge_marker: tuple[float, float] | None = None,
) -> tuple[str | None, str]:
    """Which side of a connector is its mouth, and how that was decided.

    1. ``pcb_edge_marker``: the library footprint marks where the board edge
       goes (a "PCB Edge" label and line on a user layer), as KiCad's USB-C,
       USB and other edge receptacles do; the mouth is the side nearest it.
    2. ``pad_geometry``: a receptacle's contacts sit at its back, so the
       mouth is the side the body reaches furthest beyond its pads.

    Neither, or a part whose pads sit at the middle of its body: ``None``.
    """
    if edge_marker is not None:
        return nearest_side(box, *edge_marker), "pcb_edge_marker"
    if pad_centroid is None:
        return None, "unknown"
    x1, y1, x2, y2 = box
    dx = (x1 + x2) / 2 - pad_centroid[0]
    dy = (y1 + y2) / 2 - pad_centroid[1]
    if max(abs(dx), abs(dy)) < 0.2:
        return None, "unknown"
    if abs(dx) >= abs(dy):
        return ("+x" if dx > 0 else "-x"), "pad_geometry"
    return ("+y" if dy > 0 else "-y"), "pad_geometry"


def edge_mount_anchor(
    edge: str,
    body: tuple[float, float, float, float],
    x0: float,
    y0: float,
    w: float,
    h: float,
    overhang: float,
    along: float | None = None,
) -> tuple[float, float]:
    """The anchor that puts the body's ``edge`` side ``overhang`` mm beyond that board edge.

    ``body`` is the body's extent relative to the anchor, as it sits (turned).
    ``along`` is the anchor's coordinate along the edge, in board mm; without
    it the body is centred on the edge.
    """
    bx1, by1, bx2, by2 = body
    if edge in ("+x", "-x"):
        cx = x0 + w + overhang - bx2 if edge == "+x" else x0 - overhang - bx1
        cy = along if along is not None else y0 + h / 2 - (by1 + by2) / 2
        return cx, cy
    cy = y0 + h + overhang - by2 if edge == "+y" else y0 - overhang - by1
    cx = along if along is not None else x0 + w / 2 - (bx1 + bx2) / 2
    return cx, cy


def mouth_overhang(
    edge: str,
    body: tuple[float, float, float, float],
    cx: float,
    cy: float,
    x0: float,
    y0: float,
    w: float,
    h: float,
) -> float:
    """How far the body's ``edge`` side reaches beyond that board edge, in mm."""
    bx1, by1, bx2, by2 = body
    return {
        "+x": cx + bx2 - (x0 + w),
        "-x": x0 - (cx + bx1),
        "+y": cy + by2 - (y0 + h),
        "-y": y0 - (cy + by1),
    }[edge]


def boxes_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _rel_box(box: Any, pos: Any) -> tuple[float, float, float, float]:
    return (
        _mm(box.GetX() - pos.x),
        _mm(box.GetY() - pos.y),
        _mm(box.GetRight() - pos.x),
        _mm(box.GetBottom() - pos.y),
    )


def _body(fp: Any) -> tuple[float, float, float, float]:
    """The body's extent relative to the anchor: its fab outline, else its courtyard."""
    pos = fp.GetPosition()
    box = None
    for item in fp.GraphicalItems():
        if item.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab) and not hasattr(item, "GetText"):
            b = item.GetBoundingBox()
            if box is None:
                box = b
            else:
                box.Merge(b)
    if box is None or box.GetWidth() == 0 or box.GetHeight() == 0:
        return _size(fp)
    return _rel_box(box, pos)


def _edge_marker(fp: Any) -> tuple[float, float] | None:
    """Where the footprint marks the board edge (relative to its anchor), if it does."""
    pos = fp.GetPosition()
    user = {pcbnew.Dwgs_User, pcbnew.Cmts_User}
    texts, segments = [], []
    for item in fp.GraphicalItems():
        if item.GetLayer() not in user:
            continue
        if hasattr(item, "GetText"):
            if any(k in str(item.GetText()).lower() for k in _EDGE_TEXT):
                texts.append(item.GetPosition())
        elif item.GetShape() == pcbnew.SHAPE_T_SEGMENT:
            segments.append((item.GetStart(), item.GetEnd()))
    if not texts:
        return None
    tx, ty = texts[0].x, texts[0].y
    if segments:
        # The marker line nearest the label: its midpoint is on the edge.
        def gap(seg: Any) -> float:
            mx, my = (seg[0].x + seg[1].x) / 2, (seg[0].y + seg[1].y) / 2
            return float((mx - tx) ** 2 + (my - ty) ** 2)

        a, b = min(segments, key=gap)
        tx, ty = (a.x + b.x) // 2, (a.y + b.y) // 2
    return _mm(tx - pos.x), _mm(ty - pos.y)


def _pad_centroid(fp: Any) -> tuple[float, float] | None:
    pads = list(fp.Pads())
    if not pads:
        return None
    pos = fp.GetPosition()
    return (
        _mm(sum(p.GetPosition().x for p in pads) // len(pads) - pos.x),
        _mm(sum(p.GetPosition().y for p in pads) // len(pads) - pos.y),
    )


def _set_side(fp: Any, side: str) -> None:
    if (side == "bottom") == bool(fp.IsFlipped()):
        return
    centre = fp.GetPosition()
    try:  # KiCad 9
        fp.Flip(centre, pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    except (AttributeError, TypeError):  # KiCad 8
        fp.Flip(centre, True)


def _facing(fp: Any) -> tuple[str | None, str]:
    return mating_side(_size(fp), _pad_centroid(fp), _edge_marker(fp))


def apply_placement(
    fp: Any, hint: dict[str, Any], x0: float, y0: float, w: float, h: float
) -> tuple[tuple[float, float] | None, dict[str, Any]]:
    """Set the side and rotation ``hint`` names; return the fixed anchor, if any.

    With ``edge_mount`` the footprint is turned so its mating side faces the
    edge (checked on the turned footprint, not assumed from the rotation
    convention) and the anchor puts the mouth ``overhang_mm`` beyond it.
    Without a position or an edge mount the part stays for the auto-placer,
    at the side and rotation given.
    """
    note: dict[str, Any] = {"fixed": False}
    _set_side(fp, str(hint.get("side") or "top"))
    rotation = hint.get("rotation_deg")
    mount = hint.get("edge_mount")
    position = hint.get("position")
    if mount:
        edge = str(mount["edge"])
        if rotation is not None:
            fp.SetOrientationDegrees(float(rotation))
            source = "rotation_deg"
            faces = _facing(fp)[0]
        else:
            fp.SetOrientationDegrees(0.0)
            local, source = _facing(fp)
            if local is None:
                raise ValueError(
                    f"{fp.GetReference()}: cannot tell which side of "
                    f"{fp.GetFPIDAsString()} is its mouth (no PCB Edge marker, and its "
                    "pads sit at the middle of its body); give placement.rotation_deg"
                )
            fp.SetOrientationDegrees(rotation_for_edge(local, edge))
            faces = _facing(fp)[0]
            if faces != edge:
                # Measured, not assumed: try every quarter turn.
                for deg in (0.0, 90.0, 180.0, 270.0):
                    fp.SetOrientationDegrees(deg)
                    faces = _facing(fp)[0]
                    if faces == edge:
                        break
        along = None
        if position:
            along = (
                y0 + float(position["y_mm"])
                if edge in ("+x", "-x")
                else x0 + float(position["x_mm"])
            )
        overhang = float(mount.get("overhang_mm") or 0.0)
        body = _body(fp)
        anchor = edge_mount_anchor(edge, body, x0, y0, w, h, overhang, along)
        note.update(
            {
                "fixed": True,
                "edge_mount": {
                    "edge": edge,
                    "mating_side_source": source,
                    "faces": faces,
                    "overhang_mm": round(mouth_overhang(edge, body, *anchor, x0, y0, w, h), 3),
                },
            }
        )
        return anchor, note
    if rotation is not None:
        fp.SetOrientationDegrees(float(rotation))
    if position:
        note["fixed"] = True
        return (x0 + float(position["x_mm"]), y0 + float(position["y_mm"])), note
    return None, note


def _size(fp: Any) -> tuple[float, float, float, float]:
    """Footprint extent relative to its position (courtyard if it has one)."""
    fp.BuildCourtyardCaches()
    box = None
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        poly = fp.GetCourtyard(layer)
        if poly.OutlineCount():
            box = poly.BBox()
            break
    if box is None or box.GetWidth() == 0:
        box = fp.GetBoundingBox(False, False)
    pos = fp.GetPosition()
    return (
        _mm(box.GetX() - pos.x),
        _mm(box.GetY() - pos.y),
        _mm(box.GetRight() - pos.x),
        _mm(box.GetBottom() - pos.y),
    )


def _place(
    footprints: dict[str, Any],
    net_of: dict[str, str],
    x0: float,
    y0: float,
    w: float,
    h: float,
    keepouts: list[tuple[float, float, float, float]],
    power: set[str] | None = None,
    fixed: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Parts with a fixed anchor where they were put, then connectors on the left
    edge, the largest IC as near the centre as fits, then each part beside the
    part it is most connected to.

    Connection counts shared signal nets first; a net in ``power`` (rails, ground)
    counts by how many pads the neighbour has on it, so a decoupling capacitor
    lands by the IC with the most supply pins rather than wherever a rail
    first appears. Parts with many pads keep a wider margin for fanout.
    """
    edge = 1.0
    power = power or set()
    rel = {ref: _size(fp) for ref, fp in footprints.items()}
    nets_by_ref: dict[str, set[str]] = {}
    pads_on: dict[tuple[str, str], int] = {}
    for node, net in net_of.items():
        if not net.startswith("unconnected-"):
            ref = node.split(".", 1)[0]
            nets_by_ref.setdefault(ref, set()).add(net)
            pads_on[(ref, net)] = pads_on.get((ref, net), 0) + 1
    pads = {ref: len(list(fp.Pads())) for ref, fp in footprints.items()}
    fixed = fixed or {}
    connectors = sorted(r for r in footprints if r.startswith(("J", "P", "CN")) and r not in fixed)
    gaps = {ref: 2.0 if n > 16 and ref not in connectors else 1.0 for ref, n in pads.items()}
    taken: list[tuple[float, float, float, float]] = list(keepouts)
    pos: dict[str, tuple[float, float]] = {}

    def affinity(ref: str, other: str) -> int:
        shared = nets_by_ref.get(ref, set()) & nets_by_ref.get(other, set())
        return sum(100 if n not in power else pads_on.get((other, n), 0) for n in shared)

    def fits(ref: str, cx: float, cy: float) -> bool:
        a, gap = rel[ref], gaps[ref]
        box = (cx + a[0] - gap, cy + a[1] - gap, cx + a[2] + gap, cy + a[3] + gap)
        if (
            box[0] < x0 + edge
            or box[1] < y0 + edge
            or box[2] > x0 + w - edge
            or box[3] > y0 + h - edge
        ):
            return False
        return all(
            box[2] <= t[0] or box[0] >= t[2] or box[3] <= t[1] or box[1] >= t[3] for t in taken
        )

    def put(ref: str, cx: float, cy: float) -> None:
        a, extra = rel[ref], gaps[ref] - 1.0  # a wide-margin part keeps it on all sides
        taken.append((cx + a[0] - extra, cy + a[1] - extra, cx + a[2] + extra, cy + a[3] + extra))
        pos[ref] = (cx, cy)

    def spiral(ref: str, tx: float, ty: float) -> bool:
        step = 0.5
        for ring in range(0, int(max(w, h) / step) + 2):
            pts = (
                [(tx, ty)]
                if ring == 0
                else [
                    (tx + dx * step, ty + dy * step)
                    for dx in range(-ring, ring + 1)
                    for dy in range(-ring, ring + 1)
                    if max(abs(dx), abs(dy)) == ring
                ]
            )
            pts.sort(key=lambda p: (p[0] - tx) ** 2 + (p[1] - ty) ** 2)
            for px, py in pts:
                if fits(ref, px, py):
                    put(ref, px, py)
                    return True
        return False

    warnings: list[str] = []
    for ref, (cx, cy) in sorted(fixed.items()):
        # A fixed part goes where it was put; what it overlaps is reported.
        a = rel[ref]
        box = (cx + a[0], cy + a[1], cx + a[2], cy + a[3])
        if any(boxes_overlap(box, k) for k in keepouts):
            warnings.append(f"{ref}: overlaps a mounting hole keepout")
        clash = [
            other
            for other, (ox, oy) in pos.items()
            if boxes_overlap(
                box,
                (ox + rel[other][0], oy + rel[other][1], ox + rel[other][2], oy + rel[other][3]),
            )
        ]
        if clash:
            warnings.append(f"{ref}: overlaps {', '.join(clash)}")
        put(ref, cx, cy)
    for i, ref in enumerate(connectors):
        a = rel[ref]
        cx = x0 + edge + gaps[ref] - a[0]
        cy = y0 + h * (i + 1) / (len(connectors) + 1)
        if not spiral(ref, cx, cy):
            pass
    ics = sorted((r for r in footprints if r not in pos), key=lambda r: (-pads[r], r))
    if ics and pads[ics[0]] > 16:  # the main IC as near the centre as fits
        a = rel[ics[0]]
        spiral(ics[0], x0 + w / 2 - (a[0] + a[2]) / 2, y0 + h / 2 - (a[1] + a[3]) / 2)
    rest = [r for r in ics if r not in pos]
    unplaced: list[str] = []
    for ref in rest:
        best, score = None, -1
        for placed in pos:
            shared = affinity(ref, placed)
            if shared > score:
                best, score = placed, shared
        tx, ty = pos[best] if best is not None and score > 0 else (x0 + w / 2, y0 + h / 2)
        if not spiral(ref, tx, ty):
            unplaced.append(ref)
    placements = [
        _placement_entry(footprints[ref], ref, cx, cy, x0, y0) for ref, (cx, cy) in pos.items()
    ]
    for entry in placements:
        entry["fixed"] = entry["ref"] in fixed
    return {"unplaced": unplaced, "placements": placements, "warnings": warnings}


def _placement_entry(
    fp: Any, ref: str, cx: float, cy: float, x0: float, y0: float
) -> dict[str, Any]:
    fp.SetPosition(pcbnew.VECTOR2I(MM(cx), MM(cy)))
    return {
        "ref": ref,
        "x_mm": round(cx, 3),
        "y_mm": round(cy, 3),
        "board_x_mm": round(cx - x0, 3),
        "board_y_mm": round(cy - y0, 3),
        "rotation_deg": round(float(fp.GetOrientationDegrees()), 3),
        "side": "bottom" if fp.IsFlipped() else "top",
    }


# -- edit --------------------------------------------------------------------------

#: The operations ``edit`` takes, in the order given.
EDIT_OPS = ("move", "rotate", "place", "set_mounting_hole", "remove_mounting_hole", "strip_routing")


def check_operations(ops: list[dict[str, Any]]) -> list[str]:
    """What is wrong with an operation list, before the board is touched."""
    errors: list[str] = []
    for i, op in enumerate(ops):
        kind = op.get("op")
        where = f"operations[{i}] ({kind})"
        if kind not in EDIT_OPS:
            errors.append(f"operations[{i}]: op must be one of {', '.join(EDIT_OPS)}")
            continue
        if kind in ("move", "rotate", "place", "remove_mounting_hole") and not op.get("ref"):
            errors.append(f"{where}: needs ref")
        if kind == "move" and not op.get("position"):
            errors.append(f"{where}: needs position")
        if kind == "rotate" and op.get("rotation_deg") is None:
            errors.append(f"{where}: needs rotation_deg")
        if kind == "place" and not (
            op.get("position")
            or op.get("edge_mount")
            or op.get("rotation_deg") is not None
            or op.get("side")
        ):
            errors.append(f"{where}: needs position, rotation_deg, side or edge_mount")
        if kind == "set_mounting_hole" and not op.get("position"):
            errors.append(f"{where}: needs position")
    return errors


def _board_frame(board: Any) -> tuple[float, float, float, float]:
    """The outline's top-left corner, width and height in mm, on the line's centre.

    KiCad's edge bounding box includes the outline's line width, so a board
    generated 40 mm wide reads 40.1; the half width is taken back off.
    """
    edge = board.GetBoardEdgesBoundingBox()
    half = max(
        (
            _mm(d.GetWidth()) / 2
            for d in board.GetDrawings()
            if d.GetLayer() == pcbnew.Edge_Cuts and hasattr(d, "GetWidth")
        ),
        default=0.0,
    )
    return (
        _mm(edge.GetX()) + half,
        _mm(edge.GetY()) + half,
        _mm(edge.GetWidth()) - 2 * half,
        _mm(edge.GetHeight()) - 2 * half,
    )


def strip_routing(board: Any) -> dict[str, Any]:
    """Remove every track and via, and the outer-layer copper pours; inner planes stay."""
    tracks = list(board.GetTracks())
    vias = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")
    for t in tracks:
        board.Remove(t)
    pours = [
        z
        for z in list(board.Zones())
        if not z.GetIsRuleArea() and z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu)
    ]
    nets = sorted({z.GetNetname() for z in pours if z.GetNetname()})
    for z in pours:
        board.Remove(z)
    return {"tracks": len(tracks) - vias, "vias": vias, "pours": len(pours), "pour_nets": nets}


def _hole_drill(fp: Any) -> float | None:
    for pad in fp.Pads():
        if pad.HasHole():
            return _mm(pad.GetDrillSize().x)
    return None


def edit(req: dict[str, Any]) -> dict[str, Any]:
    ops: list[dict[str, Any]] = list(req.get("operations") or [])
    errors = check_operations(ops)
    if errors:
        return {"ok": False, "errors": errors}
    path = req["pcb_path"]
    fp_root = req.get("footprint_root", "/usr/share/kicad/footprints")
    board = pcbnew.LoadBoard(path)
    x0, y0, w, h = _board_frame(board)
    applied: list[dict[str, Any]] = []
    stripped: dict[str, Any] | None = None
    touched: set[str] = set()
    for op in ops:
        kind = op["op"]
        if kind == "strip_routing":
            stripped = strip_routing(board)
            applied.append({"op": kind, **stripped})
            continue
        if kind == "set_mounting_hole":
            pos = op["position"]
            cx, cy = x0 + float(pos["x_mm"]), y0 + float(pos["y_mm"])
            ref = str(op.get("ref") or "")
            fp = board.FindFootprintByReference(ref) if ref else None
            drill = op.get("drill_mm")
            if fp is not None and drill is not None and _hole_drill(fp) != float(drill):
                board.Remove(fp)
                fp = None
            if fp is None:
                taken = {f.GetReference() for f in board.GetFootprints()}
                ref = ref or next(f"H{i}" for i in range(1, 1000) if f"H{i}" not in taken)
                fp = _hole_footprint(board, fp_root, float(drill or 3.2))
                fp.SetReference(ref)
                board.Add(fp)
            fp.SetPosition(pcbnew.VECTOR2I(MM(cx), MM(cy)))
            touched.add(ref)
            applied.append({"op": kind, "ref": ref, "drill_mm": _hole_drill(fp)})
            continue
        ref = str(op["ref"])
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            return {"ok": False, "errors": [f"{kind}: no footprint {ref} on the board"]}
        if kind == "remove_mounting_hole":
            if _hole_drill(fp) is None or any(pad.GetNetname() for pad in fp.Pads()):
                return {"ok": False, "errors": [f"{ref} is not a mounting hole"]}
            board.Remove(fp)
            applied.append({"op": kind, "ref": ref})
            continue
        touched.add(ref)
        if kind == "move":
            pos = op["position"]
            if op.get("rotation_deg") is not None:
                fp.SetOrientationDegrees(float(op["rotation_deg"]))
            fp.SetPosition(
                pcbnew.VECTOR2I(MM(x0 + float(pos["x_mm"])), MM(y0 + float(pos["y_mm"])))
            )
            applied.append({"op": kind, "ref": ref})
        elif kind == "rotate":
            fp.SetOrientationDegrees(float(op["rotation_deg"]))
            applied.append({"op": kind, "ref": ref})
        else:  # place
            hint = {k: op.get(k) for k in ("position", "rotation_deg", "edge_mount")}
            hint["side"] = op.get("side") or ("bottom" if fp.IsFlipped() else "top")
            anchor, note = apply_placement(fp, hint, x0, y0, w, h)
            if anchor is not None:
                fp.SetPosition(pcbnew.VECTOR2I(MM(anchor[0]), MM(anchor[1])))
            applied.append({"op": kind, "ref": ref, **note})
    if req.get("strip_routing") and stripped is None:
        stripped = strip_routing(board)
    pcbnew.SaveBoard(path, board)
    boxes = {}
    placements = []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        p = fp.GetPosition()
        cx, cy = _mm(p.x), _mm(p.y)
        a = _size(fp)
        boxes[ref] = (cx + a[0], cy + a[1], cx + a[2], cy + a[3])
        if ref in touched:
            placements.append(_placement_entry(fp, ref, cx, cy, x0, y0))
    overlaps = sorted(
        f"{a} and {b}"
        for a in boxes
        for b in boxes
        if a < b and boxes_overlap(boxes[a], boxes[b]) and (a in touched or b in touched)
    )
    beyond = sorted(
        ref
        for ref in touched
        if boxes[ref][0] < x0
        or boxes[ref][1] < y0
        or boxes[ref][2] > x0 + w
        or boxes[ref][3] > y0 + h
    )
    return {
        "ok": True,
        "path": path,
        "applied": applied,
        "placements": placements,
        "overlaps": overlaps,
        "beyond_board_edge": beyond,
        "routing_stripped": stripped,
        "board_mm": {"width": round(w, 3), "height": round(h, 3)},
    }


# -- route -------------------------------------------------------------------------


def route(req: dict[str, Any]) -> dict[str, Any]:
    path = req["pcb_path"]
    board = pcbnew.LoadBoard(path)
    base = os.path.splitext(path)[0]
    dsn, ses = base + ".dsn", base + ".ses"
    named = _name_unnamed_footprints(board)
    fanout = _fanout_planes(board)
    if fanout["vias"] or named:
        pcbnew.SaveBoard(path, board)
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        return {"ok": False, "errors": ["Specctra DSN export failed"]}
    router = req.get("router_cmd") or ["java", "-jar", "/opt/freerouting/freerouting.jar"]
    cmd = [*router, "-de", dsn, "-do", ses, "-mp", str(int(req.get("passes", 30))), "-l", "en"]
    started = time.monotonic()
    try:
        proc = subprocess.run(  # cwd: Freerouting writes <cwd>/en/freerouting.log
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.abspath(path)),
            timeout=float(req.get("timeout_s", 600)),
        )
        log, rc = (proc.stdout + proc.stderr), proc.returncode
    except subprocess.TimeoutExpired as exc:
        log, rc = str(exc.stdout or "") + str(exc.stderr or ""), -9
    elapsed = round(time.monotonic() - started, 2)
    if not os.path.isfile(ses):
        return {
            "ok": False,
            "errors": [f"Freerouting produced no session (exit {rc})"],
            "log_tail": log[-3000:],
            "router_time_s": elapsed,
        }
    board = pcbnew.LoadBoard(path)
    if not pcbnew.ImportSpecctraSES(board, ses):
        return {
            "ok": False,
            "errors": [
                "KiCad could not import Freerouting's session, so none of the routing reached "
                "the board: the board file on disk is unrouted. This is an adapter fault, not a "
                "routing result; the router's own unrouted count in log_tail does not describe "
                "the board"
            ],
            "router_time_s": elapsed,
            "log_tail": log[-3000:],
        }
    pour = req.get("ground_pour")
    if pour:
        _pour(board, pour)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(path, board)
    tracks = [t for t in board.GetTracks()]
    vias = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")
    length = sum(_mm(t.GetLength()) for t in tracks if t.GetClass() == "PCB_TRACK")
    return {
        "ok": True,
        "path": path,
        "router_exit": rc,
        "router_time_s": elapsed,
        "tracks": len(tracks) - vias,
        "vias": vias,
        "track_length_mm": round(length, 1),
        "plane_fanout": fanout,
        "log_tail": log[-1500:],
    }


def _name_unnamed_footprints(board: Any) -> int:
    """Give every footprint without a library id one, so the session imports back.

    Covers boards generated before FORGE-691, whose non-M3 mounting holes had
    none. The name is only what the Specctra export calls the part.
    """
    named = 0
    for fp in board.GetFootprints():
        if not fp.GetFPIDAsString().strip(":"):
            fp.SetFPID(pcbnew.LIB_ID("MetaForge", f"Unnamed_{fp.GetReference() or named}"))
            named += 1
    return named


def _fanout_planes(board: Any) -> dict[str, Any]:
    """A via beside every SMD pad on an inner-plane net, joined by a short track.

    Freerouting reaches a plane only by dropping a via of its own, and on a
    dense board it leaves many supply pins unconnected doing so. Fanning them
    out first -- under an IC's body where there is room, so the signal pins
    beside them keep their way out, else away from the part -- leaves the
    router the signals alone. The fanout is locked, so the router keeps it.
    """
    planes = {
        z.GetNetCode()
        for z in board.Zones()
        if z.GetLayer() in (pcbnew.In1_Cu, pcbnew.In2_Cu)
        and board.GetLayerType(z.GetLayer()) == pcbnew.LT_POWER
    }
    if not planes:
        return {"vias": 0, "skipped": []}
    nc = board.GetDesignSettings().m_NetSettings.GetDefaultNetclass()
    clear, track_w = nc.GetClearance(), nc.GetTrackWidth()
    via_d, drill = nc.GetViaDiameter(), nc.GetViaDrill()
    hole_clear = board.GetDesignSettings().m_HoleClearance
    edge = board.GetBoardEdgesBoundingBox()
    margin = via_d // 2 + clear + MM(0.3)
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    # Rule areas, the board's and the footprints' own (a sensor's no-via zone).
    keepouts = [
        z
        for z in [*board.Zones(), *(z for fp in board.GetFootprints() for z in fp.Zones())]
        if z.GetIsRuleArea()
    ]
    placed: list[Any] = []
    skipped: list[str] = []

    def free(item: Any, net: int, is_via: bool) -> bool:
        for z in keepouts:
            barred = z.GetDoNotAllowVias() if is_via else z.GetDoNotAllowTracks()
            if barred and (is_via or z.IsOnLayer(pcbnew.F_Cu)):
                if z.Outline().Collide(item.GetEffectiveShape(pcbnew.F_Cu), clear):
                    return False
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            shape = item.GetEffectiveShape(layer)
            for p in pads:
                if p.GetNetCode() != net and p.IsOnLayer(layer):
                    if shape.Collide(p.GetEffectiveShape(layer), clear):
                        return False
            for t in placed:
                if t.GetNetCode() != net and shape.Collide(t.GetEffectiveShape(layer), clear):
                    return False
        if is_via:
            hole = item.GetEffectiveHoleShape()
            for p in pads:
                if p.HasHole() and hole.Collide(p.GetEffectiveHoleShape(), hole_clear):
                    return False
            for t in placed:
                if t.GetClass() == "PCB_VIA" and hole.Collide(
                    t.GetEffectiveHoleShape(), hole_clear
                ):
                    return False
        return True

    for fp in board.GetFootprints():
        centre = fp.GetPosition()
        many = len(list(fp.Pads())) > 16
        for pad in fp.Pads():
            net = pad.GetNetCode()
            if net not in planes or pad.HasHole() or not pad.IsOnLayer(pcbnew.F_Cu):
                continue
            pos = pad.GetPosition()
            dx, dy = pos.x - centre.x, pos.y - centre.y
            # The pad's outward axis, from the footprint's centre.
            ax = (1 if dx > 0 else -1, 0) if abs(dx) >= abs(dy) else (0, 1 if dy > 0 else -1)
            if ax == (0, 0) or (dx == 0 and dy == 0):
                ax = (0, -1)
            out, back = ax, (-ax[0], -ax[1])
            side1, side2 = (ax[1], ax[0]), (-ax[1], -ax[0])
            dirs = [back, out, side1, side2] if many else [out, side1, side2, back]
            size = pad.GetBoundingBox()
            half = max(size.GetWidth(), size.GetHeight()) // 2
            done = False
            for d in dirs:
                for step in range(0, 12):
                    dist = half + via_d // 2 + clear + MM(0.1) + step * MM(0.25)
                    at = pcbnew.VECTOR2I(pos.x + d[0] * dist, pos.y + d[1] * dist)
                    if not (
                        edge.GetX() + margin < at.x < edge.GetRight() - margin
                        and edge.GetY() + margin < at.y < edge.GetBottom() - margin
                    ):
                        break
                    via = pcbnew.PCB_VIA(board)
                    via.SetPosition(at)
                    via.SetWidth(pcbnew.F_Cu, via_d)
                    via.SetDrill(drill)
                    via.SetNetCode(net)
                    track = pcbnew.PCB_TRACK(board)
                    track.SetStart(pos)
                    track.SetEnd(at)
                    track.SetWidth(track_w)
                    track.SetLayer(pcbnew.F_Cu)
                    track.SetNetCode(net)
                    if free(via, net, True) and free(track, net, False):
                        for item in (track, via):
                            item.SetLocked(True)
                            board.Add(item)
                            placed.append(item)
                        done = True
                        break
                if done:
                    break
            if not done:
                skipped.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    return {"vias": len(placed) // 2, "skipped": skipped}


def _pour(board: Any, net_name: str) -> None:
    net = board.FindNet(net_name)
    if net is None:
        return
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        board.Add(_zone(board, layer, net, inset=0.5))


def _zone(board: Any, layer: int, net: Any, inset: float) -> Any:
    """A solid copper zone over the whole board, ``inset`` mm inside the edge."""
    edge = board.GetBoardEdgesBoundingBox()
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNetCode(net.GetNetCode())
    zone.SetLocalClearance(MM(0.3))
    zone.SetMinThickness(MM(0.25))
    # Solid: thermal spokes to small SMD pads starve (DRC starved_thermal).
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    outline = zone.Outline()
    outline.NewOutline()
    d = MM(inset)
    for x, y in (
        (edge.GetX() + d, edge.GetY() + d),
        (edge.GetRight() - d, edge.GetY() + d),
        (edge.GetRight() - d, edge.GetBottom() - d),
        (edge.GetX() + d, edge.GetBottom() - d),
    ):
        outline.Append(x, y)
    return zone


def main() -> None:
    req = json.load(sys.stdin)
    action = req.get("action")
    try:
        if action == "generate":
            result = generate(req)
        elif action == "route":
            result = route(req)
        elif action == "edit":
            result = edit(req)
        else:
            result = {"ok": False, "errors": [f"unknown action {action!r}"]}
    except Exception as exc:  # noqa: BLE001 -- report, never a traceback on stdout
        result = {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    sys.stdout.write("\n@@RESULT@@" + json.dumps(result, default=str) + "\n")


if __name__ == "__main__":
    main()
    _ = math  # imported for callers that extend placement
