"""Board generation and autorouting with KiCad's pcbnew (FORGE-683).

Runs under the system Python that ships KiCad's ``pcbnew`` module, not the
adapter's interpreter, so it imports nothing from MetaForge: it reads one JSON
request on stdin and writes one JSON result on stdout.

``generate``: a ``.kicad_pcb`` from the circuit -- footprints from the KiCad
libraries with their nets, the outline, design rules and net classes, corner
mounting holes, inner planes on a 4-layer board, and a deterministic
placement that puts connectors on the left edge, the main IC at the centre
and every other part next to the part it is most connected to. Placement is
a reviewable starting point, not an optimum.

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
    if hole:
        holes = _mounting_holes(board, fp_root, float(hole), x0, y0, w, h)
        keepouts.extend(holes)

    power = {*board_spec.get("power_nets", []), *(board_spec.get("inner_planes") or [])}
    if board_spec.get("ground_pour"):
        power.add(board_spec["ground_pour"])
    placement = _place(footprints, net_of, x0, y0, w, h, keepouts, power)
    pcbnew.SaveBoard(out, board)
    return {
        "ok": True,
        "path": out,
        "footprints": len(footprints),
        "nets": len(nets),
        "unplaced": placement["unplaced"],
        "placements": placement["placements"],
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


def _mounting_holes(
    board: Any, fp_root: str, drill: float, x0: float, y0: float, w: float, h: float
) -> list[tuple[float, float, float, float]]:
    lib = os.path.join(fp_root, "MountingHole.pretty")
    name = "MountingHole_3.2mm_M3" if abs(drill - 3.2) < 0.05 else None
    boxes = []
    inset = drill / 2 + 2.0
    for i, (cx, cy) in enumerate(
        (
            (x0 + inset, y0 + inset),
            (x0 + w - inset, y0 + inset),
            (x0 + inset, y0 + h - inset),
            (x0 + w - inset, y0 + h - inset),
        )
    ):
        fp = pcbnew.FootprintLoad(lib, name) if name else None
        if fp is None:
            fp = pcbnew.FOOTPRINT(board)
            pad = pcbnew.PAD(fp)
            pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetDrillSize(pcbnew.VECTOR2I(MM(drill), MM(drill)))
            pad.SetSize(pcbnew.VECTOR2I(MM(drill), MM(drill)))
            fp.Add(pad)
        fp.SetReference(f"H{i + 1}")
        # Board-only: not in the schematic, the BOM or the placement file, so
        # schematic parity does not count it as an extra footprint.
        fp.SetBoardOnly(True)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        fp.SetPosition(pcbnew.VECTOR2I(MM(cx), MM(cy)))
        board.Add(fp)
        r = drill / 2 + 1.5
        boxes.append((cx - r, cy - r, cx + r, cy + r))
    return boxes


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
) -> dict[str, Any]:
    """Connectors on the left edge, the largest IC as near the centre as fits,
    then each part beside the part it is most connected to.

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
    connectors = sorted(r for r in footprints if r.startswith(("J", "P", "CN")))
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
    placements = []
    for ref, (cx, cy) in pos.items():
        footprints[ref].SetPosition(pcbnew.VECTOR2I(MM(cx), MM(cy)))
        placements.append({"ref": ref, "x_mm": round(cx, 3), "y_mm": round(cy, 3)})
    return {"unplaced": unplaced, "placements": placements}


# -- route -------------------------------------------------------------------------


def route(req: dict[str, Any]) -> dict[str, Any]:
    path = req["pcb_path"]
    board = pcbnew.LoadBoard(path)
    base = os.path.splitext(path)[0]
    dsn, ses = base + ".dsn", base + ".ses"
    fanout = _fanout_planes(board)
    if fanout["vias"]:
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
        return {"ok": False, "errors": ["Specctra session import failed"], "log_tail": log[-3000:]}
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
        else:
            result = {"ok": False, "errors": [f"unknown action {action!r}"]}
    except Exception as exc:  # noqa: BLE001 -- report, never a traceback on stdout
        result = {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    sys.stdout.write("\n@@RESULT@@" + json.dumps(result, default=str) + "\n")


if __name__ == "__main__":
    main()
    _ = math  # imported for callers that extend placement
