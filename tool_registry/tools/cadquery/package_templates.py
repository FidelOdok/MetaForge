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

from dataclasses import dataclass, field
from typing import Any

TEMPLATES = ("BOX", "CHIP", "NOLEAD", "GULL2", "GULL4", "BGA", "CYLINDER", "BOARD")
EDGES = ("top", "bottom", "left", "right")


class TemplateError(ValueError):
    """Sizes or pins a template cannot build; the message says which."""


@dataclass
class Built:
    script: str
    bbox: tuple[float, float, float]
    pins: int
    notes: list[str] = field(default_factory=list)
    #: The pieces this part is made of, each ``{name, kind, bbox_mm}`` (R14).
    parts: list[dict[str, Any]] = field(default_factory=list)


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
    lines = [_HEAD, f"result = {_blk(length, width, thick)}\n"]
    if radius:
        lines.append(f"result = result.edges('|Z').fillet({radius})\n")
    parts: list[dict[str, Any]] = [
        {"name": "board", "kind": "pcb", "bbox_mm": [length, width, thick]}
    ]
    for i, hole in enumerate(holes):
        d = _num(hole, "diameter")
        x, y = _num(hole, "x"), _num(hole, "y")
        if not (d / 2 < x < length - d / 2 and d / 2 < y < width - d / 2):
            raise TemplateError(f"hole {i + 1} at ({x}, {y}) does not fit on the board")
        lines.append(
            f"result = result.cut(cq.Workplane('XY').circle({d / 2}).extrude({thick})"
            f".translate(({ox + x}, {oy + y}, 0)))\n"
        )
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
        span = (n - 1) * pitch + pitch  # housing reaches half a pitch past each end
        depth = rows * pitch
        hx = (depth if along_y else span) / 2
        hy = (span if along_y else depth) / 2
        cx = x + ((rows - 1) * pitch / 2 if along_y else (n - 1) * pitch / 2)
        cy = y + ((n - 1) * pitch / 2 if along_y else (rows - 1) * pitch / 2)
        eps = 1e-6  # a header flush with the edge must not fail on rounding
        if cx - hx < -eps or cx + hx > length + eps or cy - hy < -eps or cy + hy > width + eps:
            raise TemplateError(f"header {i + 1} does not fit on the board")
        bx, by = 2 * hx, 2 * hy
        lines.append(
            f"result = result.union({_blk(bx, by, _HEADER_BODY, (ox + cx, oy + cy, thick))})\n"
        )
        pts = [
            (
                round(ox + x + (r * pitch if along_y else k * pitch), 6),
                round(oy + y + (k * pitch if along_y else r * pitch), 6),
            )
            for k in range(n)
            for r in range(rows)
        ]
        lines.append(
            f"result = result.union(cq.Workplane('XY').pushPoints({pts})"
            f".rect({_HEADER_PIN}, {_HEADER_PIN}).extrude({tall}).translate((0, 0, {thick})))\n"
        )
        total += n * rows
        top = max(top, thick + tall)
        parts.append(
            {
                "name": str(head.get("name") or f"J{i + 1}"),
                "kind": "pin_header",
                "pins": n * rows,
                "pitch_mm": pitch,
                "bbox_mm": [round(bx, 6), round(by, 6), tall],
            }
        )
    _check_pins(total, pin_count)
    notes = [f"{len(holes)} mounting hole(s)", f"{len(headers)} header(s), {total} pins"]
    return Built("".join(lines), (length, width, round(top, 6)), total, notes, parts)


_BUILDERS = {
    "BOX": box,
    "CHIP": chip,
    "NOLEAD": nolead,
    "GULL2": gull2,
    "GULL4": gull4,
    "BGA": bga,
    "CYLINDER": cylinder,
    "BOARD": board,
}


def build(
    template: str, dims: dict[str, Any], pins: dict[str, Any] | None, pin_count: int | None
) -> Built:
    """The script and the box it must make, or :class:`TemplateError`."""
    builder = _BUILDERS.get(str(template).upper())
    if builder is None:
        raise TemplateError(f"template is one of {', '.join(TEMPLATES)}")
    return builder(dims or {}, pins or {}, pin_count)
