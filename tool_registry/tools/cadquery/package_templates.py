"""Package templates for ``geometry.build_from_estimate`` (FORGE-928, R6).

Each template turns resolved sizes (millimetres) into a CadQuery script and the
bounding box that script must produce. Pure: no CadQuery here, so a template is
tested without it, and the adapter runs the script in its isolated child and checks
the built shape against the box (over every solid, FORGE-930).

Axes: x is the body's length, y its width, z up from the seating plane (z = 0).

* ``BOX``: a plain body (a module, a can).
* ``CHIP``: a two-terminal chip (0402 to 2512 resistors and capacitors) with end
  terminations.
* ``NOLEAD``: QFN / DFN: body, pads on the underside along the edges (a count per
  edge: ``top``, ``bottom``, ``left``, ``right``), an optional exposed pad.
* ``GULL2``: SOIC / SOT / TSSOP: gull-wing leads on the two long sides (``left`` and
  ``right`` counts, or ``per_side``), a lead-tip span across the width.
* ``GULL4``: QFP: gull-wing leads on all four sides.
* ``BGA``: body on a grid of balls.
* ``CYLINDER``: a round can (electrolytic, crystal can) standing on its base.
* ``BOARD``: a breakout or module PCB: outline (optional corner radius), mounting
  holes and pin headers, positioned in millimetres from the board's lower-left
  corner as a photo is read. Lists its subparts (the board, each header).

A pin count that disagrees with the pads per edge fails the build, never adjusts it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

TEMPLATES = (
    "BOX",
    "CHIP",
    "NOLEAD",
    "GULL2",
    "GULL4",
    "BGA",
    "CYLINDER",
    "BOARD",
    "SHIELDED_MODULE",
    "SPEAKER",
)
EDGES = ("top", "bottom", "left", "right")


class TemplateError(ValueError):
    """Sizes or pins a template cannot build; the message says which."""


@dataclass
class Built:
    script: str
    bbox: tuple[float, float, float]
    pins: int
    notes: list[str] = field(default_factory=list)
    #: The subpart tree (R14.1): root nodes ``{name, kind, shape, pose, size, children}``,
    #: pose relative to the parent, millimetres.
    parts: list[dict[str, Any]] = field(default_factory=list)
    #: One ``{name, script, location}`` per separate solid, for a named STEP assembly.
    solids: list[dict[str, Any]] = field(default_factory=list)


def _num(dims: dict[str, Any], key: str, default: float | None = None) -> float:
    value = dims.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise TemplateError(f"{key} must be a positive number of millimetres")
    return float(value)


def _count(pins: dict[str, Any], key: str) -> int:
    value = pins.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TemplateError(f"pins.{key} must be a whole number of pads")
    return int(value)


def _check_pins(total: int, pin_count: int | None) -> None:
    if pin_count is not None and pin_count != total:
        raise TemplateError(
            f"pin_count is {pin_count} but the pads per edge add up to {total}: the reading "
            "and the package disagree, so nothing is built"
        )


def _row(n: int, pitch: float) -> list[float]:
    """Centred positions of ``n`` pads at ``pitch``."""
    return [round((i - (n - 1) / 2) * pitch, 6) for i in range(n)]


_HEAD = "import cadquery as cq\n"


def _blk(x: float, y: float, z: float, at: tuple[float, float, float] = (0, 0, 0)) -> str:
    """A CadQuery box ``x`` by ``y`` by ``z``, standing on ``at``'s z, centred on its x, y."""
    expr = f"cq.Workplane('XY').box({x}, {y}, {z}, centered=(True, True, False))"
    return expr if at == (0, 0, 0) else f"{expr}.translate({at})"


def box(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    length, width, height = _num(dims, "length"), _num(dims, "width"), _num(dims, "height")
    script = _HEAD + f"result = {_blk(length, width, height)}\n"
    return Built(script, (length, width, height), 0)


def chip(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    length, width, height = _num(dims, "length"), _num(dims, "width"), _num(dims, "height")
    term = _num(dims, "terminal", round(length * 0.2, 4))
    if 2 * term >= length:
        raise TemplateError("terminal must be under half the length")
    _check_pins(2, pin_count)
    core = length - 2 * term
    script = (
        _HEAD
        + f"body = {_blk(core, width, height)}\n"
        + f"end = {_blk(term, width, height)}\n"
        + f"result = body.union(end.translate(({-(core + term) / 2}, 0, 0)))"
        + f".union(end.translate(({(core + term) / 2}, 0, 0)))\n"
    )
    return Built(script, (length, width, height), 2)


def nolead(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    length, width, height = _num(dims, "length"), _num(dims, "width"), _num(dims, "height")
    pitch = _num(dims, "pitch")
    pad_len, pad_w = _num(dims, "pad_length", 0.4), _num(dims, "pad_width", pitch * 0.5)
    counts = {e: _count(pins, e) for e in EDGES}
    total = sum(counts.values())
    if total == 0:
        raise TemplateError("NOLEAD needs pads on at least one edge (pins.top/bottom/left/right)")
    _check_pins(total + (1 if dims.get("exposed_pad_counts") else 0), pin_count)
    for edge, n in counts.items():
        side = length if edge in ("top", "bottom") else width
        if n and (n - 1) * pitch + pad_w > side:
            raise TemplateError(f"{n} pads at {pitch} mm do not fit along the {edge} edge")
    t = 0.05  # pads sit in the underside, flush: the body keeps its own box
    lines = [
        _HEAD,
        f"result = {_blk(length, width, height)}\n",
    ]
    for edge, n in counts.items():
        for pos in _row(n, pitch):
            if edge in ("top", "bottom"):
                y = (width - pad_len) / 2 * (1 if edge == "top" else -1)
                lines.append(f"result = result.union({_blk(pad_w, pad_len, t, (pos, y, 0))})\n")
            else:
                x = (length - pad_len) / 2 * (1 if edge == "right" else -1)
                lines.append(f"result = result.union({_blk(pad_len, pad_w, t, (x, pos, 0))})\n")
    notes = []
    if dims.get("exposed_pad"):
        ep = _num(dims, "exposed_pad")
        if ep >= min(length, width):
            raise TemplateError("exposed_pad must be smaller than the body")
        lines.append(f"result = result.union({_blk(ep, ep, t)})\n")
        notes.append(f"exposed pad {ep} mm square")
    return Built("".join(lines), (length, width, height), total, notes)


def _gull(dims: dict[str, Any], counts: dict[str, int], pin_count: int | None, four: bool) -> Built:
    length, width, height = _num(dims, "length"), _num(dims, "width"), _num(dims, "height")
    pitch = _num(dims, "pitch")
    lead_w = _num(dims, "lead_width", round(pitch * 0.4, 4))
    standoff = _num(dims, "standoff", 0.1)
    lead_t = _num(dims, "lead_thickness", 0.2)
    span_y = _num(dims, "span" if not four else "span_y")
    span_x = _num(dims, "span_x") if four else length
    if span_y <= width or (four and span_x <= length):
        raise TemplateError("the lead-tip span must be wider than the body")
    total = sum(counts.values())
    _check_pins(total, pin_count)
    reach_y = (span_y - width) / 2
    reach_x = (span_x - length) / 2
    # Each lead is a block from the seating plane up into the body's underside, so it
    # touches the body (a lead floating under its part would be a second loose solid).
    lead_h = round(standoff + lead_t, 6)
    lines = [
        _HEAD,
        f"result = {_blk(length, width, height, (0, 0, standoff))}\n",
    ]
    for edge, n in counts.items():
        for pos in _row(n, pitch):
            if edge in ("left", "right"):  # along the length, reaching out across the width
                sign = 1 if edge == "right" else -1
                y = sign * (width / 2 + reach_y / 2 - 0.025)
                lead = _blk(lead_w, reach_y + 0.05, lead_h, (pos, y, 0))
                lines.append(f"result = result.union({lead})\n")
            else:
                sign = 1 if edge == "top" else -1
                x = sign * (length / 2 + reach_x / 2 - 0.025)
                lead = _blk(reach_x + 0.05, lead_w, lead_h, (x, pos, 0))
                lines.append(f"result = result.union({lead})\n")
    rows = [n for e, n in counts.items() if e in ("left", "right") and n]
    along_x = max([(n - 1) * pitch + lead_w for n in rows] or [0.0])
    cols = [n for e, n in counts.items() if e in ("top", "bottom") and n]
    along_y = max([(n - 1) * pitch + lead_w for n in cols] or [0.0])
    bbox = (max(span_x, along_x), max(span_y, along_y), standoff + height)
    return Built("".join(lines), bbox, total)


def gull2(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    per_side = pins.get("per_side")
    if per_side is not None:
        counts = {"left": int(per_side), "right": int(per_side)}
    else:
        counts = {"left": _count(pins, "left"), "right": _count(pins, "right")}
    if not any(counts.values()):
        raise TemplateError("GULL2 needs leads: pins.per_side, or pins.left and pins.right")
    return _gull(dims, counts, pin_count, four=False)


def gull4(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    per_side = pins.get("per_side")
    counts = (
        {e: int(per_side) for e in EDGES}
        if per_side is not None
        else {e: _count(pins, e) for e in EDGES}
    )
    if not all(counts.values()):
        raise TemplateError("GULL4 needs leads on every side")
    return _gull(dims, counts, pin_count, four=True)


def bga(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    length, width, height = _num(dims, "length"), _num(dims, "width"), _num(dims, "height")
    pitch, ball = _num(dims, "pitch"), _num(dims, "ball_diameter")
    rows, cols = _count(pins, "rows"), _count(pins, "cols")
    if not rows or not cols:
        raise TemplateError("BGA needs pins.rows and pins.cols")
    _check_pins(rows * cols, pin_count)
    if (cols - 1) * pitch + ball > length or (rows - 1) * pitch + ball > width:
        raise TemplateError("the ball grid does not fit under the body")
    stand = round(ball * 0.8, 4)
    script = (
        _HEAD
        + f"result = {_blk(length, width, height, (0, 0, stand))}\n"
        + f"pts = [(x, y) for x in {_row(cols, pitch)} for y in {_row(rows, pitch)}]\n"
        + f"balls = cq.Workplane('XY').pushPoints(pts).sphere({ball / 2})"
        + f".translate((0, 0, {ball / 2}))\n"
        + f"result = result.union(balls.intersect({_blk(length * 2, width * 2, stand)}))\n"
    )
    return Built(script, (length, width, stand + height), rows * cols)


def cylinder(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    diameter, height = _num(dims, "diameter"), _num(dims, "height")
    script = _HEAD + f"result = cq.Workplane('XY').circle({diameter / 2}).extrude({height})\n"
    return Built(script, (diameter, diameter, height), int(pin_count or 0))


_HEADER_PIN = 0.64  # square pin, 2.54 mm headers
_HEADER_BODY = 2.5  # plastic housing height


#: Worst first, as the library ranks geometry (twin_core.library GEOMETRY_FIDELITY).
FIDELITY_ORDER = ("outline", "photo_estimate", "kicad_generic", "datasheet_built", "maker_cad")
#: Subpart kinds that decide how a part fits (R14.5): the part's fidelity is the
#: worst among these.
MECHANICAL_KINDS = frozenset({"body", "pcb", "hole", "connector", "frame", "shield"})


def part_fidelity(tree: dict[str, Any]) -> str:
    """The worst fidelity among the tree's mechanically relevant nodes."""
    found: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("kind") in MECHANICAL_KINDS and node.get("fidelity") in FIDELITY_ORDER:
            found.append(node["fidelity"])
        for child in node.get("children") or []:
            walk(child)

    walk(tree)
    return min(found, key=FIDELITY_ORDER.index) if found else "outline"


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    """Two footprints (x0, y0, x1, y1) share area."""
    eps = 1e-6
    return a[0] < b[2] - eps and b[0] < a[2] - eps and a[1] < b[3] - eps and b[1] < a[3] - eps


def board(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    length, width = _num(dims, "length"), _num(dims, "width")
    thick = _num(dims, "thickness", 1.6)
    radius = float(dims.get("corner_radius") or 0)
    if radius < 0 or 2 * radius >= min(length, width):
        raise TemplateError("corner_radius must be under half the board's smaller side")
    holes = dims.get("holes") or []
    headers = pins.get("headers") or []
    if not isinstance(holes, list) or not isinstance(headers, list):
        raise TemplateError("dims.holes and pins.headers are lists")
    ox, oy = -length / 2, -width / 2  # lower-left corner to centred coordinates
    pcb = [f"result = {_blk(length, width, thick)}\n"]
    if radius:
        pcb.append(f"result = result.edges('|Z').fillet({radius})\n")
    children: list[dict[str, Any]] = []
    footprints: list[tuple[str, tuple[float, float, float, float]]] = []
    for i, hole in enumerate(holes):
        d = _num(hole, "diameter")
        x, y = _num(hole, "x"), _num(hole, "y")
        if not (d / 2 < x < length - d / 2 and d / 2 < y < width - d / 2):
            raise TemplateError(f"hole {i + 1} at ({x}, {y}) does not fit on the board")
        pcb.append(
            f"result = result.cut(cq.Workplane('XY').circle({d / 2}).extrude({thick})"
            f".translate(({ox + x}, {oy + y}, 0)))\n"
        )
        name = str(hole.get("name") or f"H{i + 1}")
        children.append(
            {
                "name": name,
                "kind": "hole",
                "shape": "cylinder",
                "pose": [round(ox + x, 6), round(oy + y, 6), 0.0],
                "size": [d, d, thick],
                "children": [],
            }
        )
        footprints.append((f"hole {name}", (x - d / 2, y - d / 2, x + d / 2, y + d / 2)))
    solids: list[dict[str, Any]] = [
        {"name": "board", "script": _HEAD + "".join(pcb), "location": {"x": 0, "y": 0, "z": 0}}
    ]
    merged = [_HEAD, *pcb]
    top = thick
    total = 0
    for i, head in enumerate(headers):
        n = _count(head, "count")
        rows = _count(head, "rows") or 1
        if n == 0 or rows not in (1, 2):
            raise TemplateError(f"header {i + 1} needs count and 1 or 2 rows")
        pitch = _num(head, "pitch", 2.54)
        tall = _num(head, "height", 8.5)  # pin tip above the board
        along_y = str(head.get("axis") or "x") == "y"
        x, y = _num(head, "x"), _num(head, "y")  # first pin, from the lower-left corner
        span, depth = n * pitch, rows * pitch  # housing reaches half a pitch past each end
        bx, by = (depth, span) if along_y else (span, depth)
        cx = x + ((rows - 1) * pitch / 2 if along_y else (n - 1) * pitch / 2)
        cy = y + ((n - 1) * pitch / 2 if along_y else (rows - 1) * pitch / 2)
        foot = (cx - bx / 2, cy - by / 2, cx + bx / 2, cy + by / 2)
        eps = 1e-6  # a header flush with the edge must not fail on rounding
        if foot[0] < -eps or foot[1] < -eps or foot[2] > length + eps or foot[3] > width + eps:
            raise TemplateError(f"header {i + 1} does not fit on the board")
        name = str(head.get("name") or f"J{i + 1}")
        for other, other_foot in footprints:
            if _overlap(foot, other_foot):
                raise TemplateError(f"header {name} overlaps {other}")
        footprints.append((f"header {name}", foot))
        # Local frame: the housing's centre on the seating plane.
        local_pts = [
            (
                round((r * pitch if along_y else k * pitch) - (cx - x), 6),
                round((k * pitch if along_y else r * pitch) - (cy - y), 6),
            )
            for k in range(n)
            for r in range(rows)
        ]
        local = (
            f"result = {_blk(bx, by, _HEADER_BODY)}\n"
            f"result = result.union(cq.Workplane('XY').pushPoints({local_pts})"
            f".rect({_HEADER_PIN}, {_HEADER_PIN}).extrude({tall}))\n"
        )
        at = {"x": round(ox + cx, 6), "y": round(oy + cy, 6), "z": thick}
        solids.append({"name": name, "script": _HEAD + local, "location": at})
        merged.append(local.replace("result", f"h{i}"))
        merged.append(f"result = result.union(h{i}.translate(({at['x']}, {at['y']}, {at['z']})))\n")
        total += n * rows
        top = max(top, thick + tall)
        children.append(
            {
                "name": name,
                "kind": "connector",
                "shape": "pin_header",
                "pose": [at["x"], at["y"], at["z"]],
                "size": [round(bx, 6), round(by, 6), tall],
                "pins": n * rows,
                "pitch_mm": pitch,
                "children": [],
            }
        )
    _check_pins(total, pin_count)
    tree = {
        "name": "board",
        "kind": "pcb",
        "shape": "box",
        "pose": [0.0, 0.0, 0.0],
        "size": [length, width, thick],
        "children": children,
    }
    notes = [f"{len(holes)} mounting hole(s)", f"{len(headers)} header(s), {total} pins"]
    built = Built("".join(merged), (length, width, round(top, 6)), total, notes, [tree])
    built.solids = solids
    return built


#: R14.2: the subparts each part type is expected to have, ``name -> kind``. The
#: reading says, for every one, its sizes or ``visible: false``; a subpart it does
#: not mention is refused, never invented.
EXPECTED_SUBPARTS: dict[str, dict[str, str]] = {
    "SHIELDED_MODULE": {"board": "pcb", "can": "shield", "antenna": "antenna", "pads": "terminal"},
    "SPEAKER": {
        "frame": "frame",
        "magnet": "magnet",
        "cone": "cone",
        "gasket": "seal",
        "terminals": "terminal",
    },
}


def read_subparts(template: str, dims: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The reading of each expected subpart; refuses a missing, unknown or vague one."""
    expected = EXPECTED_SUBPARTS[template]
    given = dims.get("subparts")
    if not isinstance(given, dict):
        raise TemplateError(
            f"{template} needs dims.subparts with each of {', '.join(expected)}: its sizes, "
            "or visible: false (a hidden subpart is never invented)"
        )
    unknown = sorted(set(given) - set(expected))
    if unknown:
        raise TemplateError(f"{template} has no subpart {', '.join(unknown)}")
    missing = [name for name in expected if name not in given]
    if missing:
        raise TemplateError(
            f"the reading does not say whether {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} visible: give the sizes or "
            "visible: false; a hidden subpart is never invented"
        )
    out: dict[str, dict[str, Any]] = {}
    for name, reading in given.items():
        if not isinstance(reading, dict) or not isinstance(reading.get("visible"), bool):
            raise TemplateError(f"subparts.{name} needs visible: true or false")
        out[name] = reading
    return out


def _hidden(name: str, kind: str) -> dict[str, Any]:
    return {"name": name, "kind": kind, "status": "not_visible", "children": []}


def _merge(solids: list[dict[str, Any]]) -> str:
    """One script uniting every solid at its location, for the size check."""
    lines = [_HEAD]
    for i, solid in enumerate(solids):
        body = solid["script"].replace(_HEAD, "", 1).replace("result", f"s{i}")
        at = solid["location"]
        lines.append(body)
        moved = f"s{i}.translate(({at['x']}, {at['y']}, {at['z']}))"
        lines.append(f"result = {moved}\n" if i == 0 else f"result = result.union({moved})\n")
    return "".join(lines)


def shielded_module(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    seen = read_subparts("SHIELDED_MODULE", dims)
    kinds = EXPECTED_SUBPARTS["SHIELDED_MODULE"]
    if not seen["board"]["visible"]:
        raise TemplateError("the board is the module's outline: it must be visible")
    length, width = _num(dims, "length"), _num(dims, "width")
    thick = _num(seen["board"], "thickness", 0.8)
    ox, oy = -length / 2, -width / 2
    board_node: dict[str, Any] = {
        "name": "board",
        "kind": "pcb",
        "shape": "box",
        "pose": [0.0, 0.0, 0.0],
        "size": [length, width, thick],
        "children": [],
    }
    solids = [
        {
            "name": "board",
            "script": _HEAD + f"result = {_blk(length, width, thick)}\n",
            "location": {"x": 0, "y": 0, "z": 0},
        }
    ]
    top = thick
    antenna_foot = None
    antenna = seen["antenna"]
    if antenna["visible"]:
        end = str(antenna.get("end") or "top")
        depth = _num(antenna, "depth")
        span: dict[str, tuple[float, float, float, float]] = {
            "top": (0.0, width - depth, length, width),
            "bottom": (0.0, 0.0, length, depth),
            "left": (0.0, 0.0, depth, width),
            "right": (length - depth, 0.0, length, width),
        }
        if end not in span or depth >= (width if end in ("top", "bottom") else length):
            raise TemplateError("antenna.end is top, bottom, left or right, depth inside the board")
        antenna_foot = span[end]
        a = antenna_foot
        board_node["children"].append(
            {
                "name": "antenna",
                "kind": "antenna",
                "shape": "area",
                "pose": [round(ox + (a[0] + a[2]) / 2, 6), round(oy + (a[1] + a[3]) / 2, 6), thick],
                "size": [round(a[2] - a[0], 6), round(a[3] - a[1], 6), 0.0],
                "note": "a keep-out area on the board, not a solid",
                "children": [],
            }
        )
    else:
        board_node["children"].append(_hidden("antenna", kinds["antenna"]))
    can = seen["can"]
    if can["visible"]:
        cl, cw, ch = _num(can, "length"), _num(can, "width"), _num(can, "height")
        cx0, cy0 = float(can.get("x") or 0.0), float(can.get("y") or 0.0)
        foot = (cx0, cy0, cx0 + cl, cy0 + cw)
        eps = 1e-6
        if foot[0] < -eps or foot[1] < -eps or foot[2] > length + eps or foot[3] > width + eps:
            raise TemplateError("the can does not fit on the board")
        if antenna_foot and _overlap(foot, antenna_foot):
            raise TemplateError("the can covers the antenna area")
        at = {"x": round(ox + cx0 + cl / 2, 6), "y": round(oy + cy0 + cw / 2, 6), "z": thick}
        solids.append(
            {"name": "can", "script": _HEAD + f"result = {_blk(cl, cw, ch)}\n", "location": at}
        )
        board_node["children"].append(
            {
                "name": "can",
                "kind": "shield",
                "shape": "box",
                "pose": [at["x"], at["y"], at["z"]],
                "size": [cl, cw, ch],
                "children": [],
            }
        )
        top = thick + ch
    else:
        board_node["children"].append(_hidden("can", kinds["can"]))
    pads = seen["pads"]
    total = 0
    if pads["visible"]:
        pitch = _num(pads, "pitch")
        per_edge = pads.get("per_edge") or {}
        for edge in per_edge:
            if edge not in EDGES:
                raise TemplateError("pads.per_edge names top, bottom, left or right")
        for edge, n in per_edge.items():
            n = _count(per_edge, edge)
            side = length if edge in ("top", "bottom") else width
            if n and (n - 1) * pitch >= side:
                raise TemplateError(f"{n} pads at {pitch} mm do not fit along the {edge} edge")
            total += n
        board_node["children"].append(
            {
                "name": "pads",
                "kind": "terminal",
                "shape": "castellated_pads",
                "per_edge": dict(per_edge),
                "pitch_mm": pitch,
                "note": "plated half-holes in the board's edge, part of the board solid",
                "children": [],
            }
        )
        _check_pins(total, pin_count)
    else:
        board_node["children"].append(_hidden("pads", kinds["pads"]))
    notes = [f"{name} not visible in the reading" for name, r in seen.items() if not r["visible"]]
    built = Built(_merge(solids), (length, width, round(top, 6)), total, notes, [board_node])
    built.solids = solids
    return built


def speaker(dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None) -> Built:
    seen = read_subparts("SPEAKER", dims)
    kinds = EXPECTED_SUBPARTS["SPEAKER"]
    frame = seen["frame"]
    if not frame["visible"]:
        raise TemplateError("the frame is the speaker's outline: it must be visible")
    fd, fh = _num(frame, "diameter"), _num(frame, "height")
    magnet, cone = seen["magnet"], seen["cone"]
    gasket, terms = seen["gasket"], seen["terminals"]
    md = mh = 0.0
    if magnet["visible"]:
        md, mh = _num(magnet, "diameter"), _num(magnet, "height")
        if md >= fd:
            raise TemplateError("the magnet must be narrower than the frame")
    th = 1.5 if terms["visible"] else 0.0
    base = max(mh, th)  # the frame's back face; the magnet and terminals sit below it
    solids: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    cone_d = _num(cone, "diameter") if cone["visible"] else round(fd * 0.8, 4)
    if cone_d >= fd:
        raise TemplateError("the cone must be narrower than the frame")
    depth = round(fh * 0.6, 4)
    cup = (
        f"result = cq.Workplane('XY').circle({fd / 2}).extrude({fh})"
        f".cut(cq.Workplane('XY').circle({cone_d / 2}).extrude({depth})"
        f".translate((0, 0, {round(fh - depth, 6)})))\n"
    )
    solids.append({"name": "frame", "script": _HEAD + cup, "location": {"x": 0, "y": 0, "z": base}})
    frame_node: dict[str, Any] = {
        "name": "frame",
        "kind": "frame",
        "shape": "cup",
        "pose": [0.0, 0.0, base],
        "size": [fd, fd, fh],
        "children": children,
    }
    top = base + fh
    if magnet["visible"]:
        solids.append(
            {
                "name": "magnet",
                "script": _HEAD + f"result = cq.Workplane('XY').circle({md / 2}).extrude({mh})\n",
                "location": {"x": 0, "y": 0, "z": round(base - mh, 6)},
            }
        )
        children.append(
            {
                "name": "magnet",
                "kind": "magnet",
                "shape": "cylinder",
                "pose": [0.0, 0.0, round(-mh, 6)],
                "size": [md, md, mh],
                "children": [],
            }
        )
    else:
        children.append(_hidden("magnet", kinds["magnet"]))
    if cone["visible"]:
        cone_h = round(depth * 0.8, 4)
        script = (
            _HEAD
            + "result = cq.Workplane('XY').add(cq.Solid.makeCone("
            + f"{round(cone_d * 0.15, 4)}, {round(cone_d / 2 - 0.05, 4)}, {cone_h}))\n"
        )
        z = round(base + fh - depth, 6)
        solids.append({"name": "cone", "script": script, "location": {"x": 0, "y": 0, "z": z}})
        children.append(
            {
                "name": "cone",
                "kind": "cone",
                "shape": "cone",
                "pose": [0.0, 0.0, round(fh - depth, 6)],
                "size": [cone_d, cone_d, cone_h],
                "children": [],
            }
        )
    else:
        children.append(_hidden("cone", kinds["cone"]))
    if gasket["visible"]:
        gt, gw = _num(gasket, "thickness"), _num(gasket, "width")
        inner = fd - 2 * gw
        if inner <= cone_d - 1e-6:
            raise TemplateError("the gasket ring would cover the cone")
        ring = f"result = cq.Workplane('XY').circle({fd / 2}).circle({inner / 2}).extrude({gt})\n"
        solids.append(
            {"name": "gasket", "script": _HEAD + ring, "location": {"x": 0, "y": 0, "z": top}}
        )
        children.append(
            {
                "name": "gasket",
                "kind": "seal",
                "shape": "ring",
                "pose": [0.0, 0.0, fh],
                "size": [fd, fd, gt],
                "children": [],
            }
        )
        top += gt
    else:
        children.append(_hidden("gasket", kinds["gasket"]))
    total = 0
    if terms["visible"]:
        count = _count(terms, "count") or 2
        r = (max(md, cone_d * 0.3) / 2 + fd / 2) / 2
        if md and r - 1.0 <= md / 2:
            raise TemplateError("no room for the terminals between the magnet and the rim")
        for k in range(count):
            angle = 2 * math.pi * k / count
            x, y = round(r * math.cos(angle), 6), round(r * math.sin(angle), 6)
            solids.append(
                {
                    "name": f"terminal_{k + 1}",
                    "script": _HEAD + f"result = {_blk(2.0, 1.0, th)}\n",
                    "location": {"x": x, "y": y, "z": round(base - th, 6)},
                }
            )
        children.append(
            {
                "name": "terminals",
                "kind": "terminal",
                "shape": "tabs",
                "count": count,
                "pose": [0.0, 0.0, round(-th, 6)],
                "size": [2.0, 1.0, th],
                "children": [],
            }
        )
        total = count
        _check_pins(total, pin_count)
    else:
        children.append(_hidden("terminals", kinds["terminals"]))
    notes = [f"{name} not visible in the reading" for name, rd in seen.items() if not rd["visible"]]
    if not cone["visible"]:
        notes.append("the cone opening is drawn at 80% of the frame, an assumption")
    built = Built(_merge(solids), (fd, fd, round(top, 6)), total, notes, [frame_node])
    built.solids = solids
    return built


_BUILDERS = {
    "BOX": box,
    "CHIP": chip,
    "NOLEAD": nolead,
    "GULL2": gull2,
    "GULL4": gull4,
    "BGA": bga,
    "CYLINDER": cylinder,
    "BOARD": board,
    "SHIELDED_MODULE": shielded_module,
    "SPEAKER": speaker,
}


def build(
    template: str, dims: dict[str, Any], pins: dict[str, Any] | None, pin_count: int | None
) -> Built:
    """The script and the box it must make, or :class:`TemplateError`."""
    builder = _BUILDERS.get(str(template).upper())
    if builder is None:
        raise TemplateError(f"template is one of {', '.join(TEMPLATES)}")
    return builder(dims or {}, pins or {}, pin_count)
