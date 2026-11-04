"""Draw a labelled concept sketch from a block layout (FORGE-760).

The concept phase asks for a labelled sketch per concept, and until now the
only way to get one was for the model to hand-write SVG for
``twin.commit_design_sketch``. That gave sketches whose views did not agree
with each other, whose dimension text did not match the boxes drawn, and
whose labels said nothing about how sure anyone was of a part.

This module draws the sketch instead, from a block layout: named boxes and
cylinders with a size and position in mm, a provenance tag on each, and the
joints between them. Out comes one self-contained SVG with front, side and
top orthographic views (sketch type B) carrying dimension lines, an
isometric view (C) and an exploded isometric view (D) with leader-line
labels. No image model, no external reference, no script, and no clock:
the same layout always gives byte-identical SVG, so a re-render of an
unchanged concept is visibly unchanged in the twin.

A part's ``position_mm`` is the minimum corner of its axis-aligned bounding
box and ``size_mm`` its extents (or ``bbox_mm`` gives both corners), which
is exactly what a CAD assembly reports per part. Sketch type E (from CAD)
can therefore feed this renderer its parts' bounding boxes unchanged.

Coordinates: x is width (left to right), y is depth (front to back), z is
height (up). Pure stdlib; tool_registry may not import twin_core, and a
sketch needs nothing from it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.twin.concept_sketch")

#: How sure anyone is of a part. ``verified`` is a size checked against a
#: source (a datasheet, a measured part, CAD); ``intended`` is the design
#: intent; ``assumption`` is assumed and not checked; ``open`` is an
#: unresolved question. Each one is drawn differently, so a reader can tell
#: at a glance what in the sketch is still a guess.
PROVENANCE_TAGS: tuple[str, ...] = ("verified", "intended", "assumption", "open")
SHAPES: tuple[str, ...] = ("box", "cylinder")
AXES: tuple[str, ...] = ("x", "y", "z")
EXPLODE_DIRECTIONS: tuple[str, ...] = ("auto", "none", "+x", "-x", "+y", "-y", "+z", "-z")
VIEWS: tuple[str, ...] = ("front", "side", "top", "isometric", "exploded")
MAX_PARTS = 60
MAX_NAME_LENGTH = 80
DISCLAIMER = "Illustrative concept sketch, not a design record"

Vec = tuple[float, float, float]
Point = tuple[float, float]

_PROVENANCE_MEANING = {
    "verified": "checked against a source",
    "intended": "design intent",
    "assumption": "assumed, not checked",
    "open": "unresolved question",
}


@dataclass(frozen=True)
class SketchPart:
    """One block of the layout: an axis-aligned box or cylinder."""

    name: str
    shape: str
    size: Vec
    position: Vec
    provenance: str
    axis: str = "z"
    explode: str = "auto"

    @property
    def upper(self) -> Vec:
        return (
            self.position[0] + self.size[0],
            self.position[1] + self.size[1],
            self.position[2] + self.size[2],
        )

    @property
    def centre(self) -> Vec:
        return (
            self.position[0] + self.size[0] / 2,
            self.position[1] + self.size[1] / 2,
            self.position[2] + self.size[2] / 2,
        )

    @property
    def volume(self) -> float:
        box = self.size[0] * self.size[1] * self.size[2]
        return box * math.pi / 4 if self.shape == "cylinder" else box


@dataclass(frozen=True)
class SketchConnection:
    """A joint between two parts, by name."""

    a: str
    b: str
    joint: str
    provenance: str = "intended"


@dataclass(frozen=True)
class ConceptLayout:
    """A validated block layout, ready to draw."""

    concept_name: str
    parts: tuple[SketchPart, ...]
    connections: tuple[SketchConnection, ...] = ()
    notes: str = ""

    def bounds(self) -> tuple[Vec, Vec]:
        lo = tuple(min(p.position[i] for p in self.parts) for i in range(3))
        hi = tuple(max(p.upper[i] for p in self.parts) for i in range(3))
        return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])

    def overall(self) -> Vec:
        lo, hi = self.bounds()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])


# -- validation ---------------------------------------------------------------


def _text_field(value: Any, field: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _vec(value: Any, field: str, *, positive: bool) -> Vec:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{field} must be a list of three numbers in mm, got {value!r}")
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{field} must be three finite numbers in mm, got {value!r}")
        if positive and v <= 0:
            raise ValueError(f"{field} must be positive on every axis, got {value!r}")
        out.append(float(v))
    return (out[0], out[1], out[2])


def _provenance(value: Any, field: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if value not in PROVENANCE_TAGS:
        raise ValueError(f"{field} must be one of {', '.join(PROVENANCE_TAGS)}, got {value!r}")
    return str(value)


def _parse_part(raw: Any, index: int) -> SketchPart:
    where = f"parts[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    name = _text_field(raw.get("name"), f"{where}.name")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"{where}.name is longer than {MAX_NAME_LENGTH} characters")
    where = f"part {name!r}"
    shape = raw.get("shape", "box")
    if shape not in SHAPES:
        raise ValueError(f"{where}: shape must be one of {', '.join(SHAPES)}, got {shape!r}")
    if "bbox_mm" in raw:
        # The CAD path (sketch type E): a part's bounding box, as reported.
        bbox = raw["bbox_mm"]
        if "size_mm" in raw or "position_mm" in raw:
            raise ValueError(f"{where}: give bbox_mm or size_mm/position_mm, not both")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 2:
            raise ValueError(f"{where}: bbox_mm must be [[xmin, ymin, zmin], [xmax, ymax, zmax]]")
        lo = _vec(bbox[0], f"{where}.bbox_mm min", positive=False)
        hi = _vec(bbox[1], f"{where}.bbox_mm max", positive=False)
        size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
        if min(size) <= 0:
            raise ValueError(f"{where}: bbox_mm max must exceed min on every axis, got {bbox!r}")
        position = lo
    else:
        if "size_mm" not in raw:
            raise ValueError(f"{where}: size_mm is required")
        size = _vec(raw["size_mm"], f"{where}.size_mm", positive=True)
        position = _vec(raw.get("position_mm", [0, 0, 0]), f"{where}.position_mm", positive=False)
    provenance = _provenance(raw.get("provenance"), f"{where}.provenance")
    axis = raw.get("axis", "z")
    if axis not in AXES:
        raise ValueError(f"{where}: axis must be one of x, y, z, got {axis!r}")
    explode = raw.get("explode", "auto")
    if explode not in EXPLODE_DIRECTIONS:
        raise ValueError(
            f"{where}: explode must be one of {', '.join(EXPLODE_DIRECTIONS)}, got {explode!r}"
        )
    return SketchPart(
        name=name,
        shape=str(shape),
        size=size,
        position=position,
        provenance=provenance,
        axis=str(axis),
        explode=str(explode),
    )


def parse_layout(data: dict[str, Any]) -> ConceptLayout:
    """Validate a block layout, raising ``ValueError`` that names what is wrong."""
    if not isinstance(data, dict):
        raise ValueError("the layout must be an object")
    concept_name = _text_field(data.get("concept_name"), "concept_name")
    raw_parts = data.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("parts must be a non-empty list")
    if len(raw_parts) > MAX_PARTS:
        raise ValueError(f"at most {MAX_PARTS} parts can be sketched, got {len(raw_parts)}")
    parts = tuple(_parse_part(raw, i) for i, raw in enumerate(raw_parts))
    names = [p.name for p in parts]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"part names must be unique, repeated: {', '.join(duplicates)}")

    raw_connections = data.get("connections") or []
    if not isinstance(raw_connections, list):
        raise ValueError("connections must be a list")
    known = set(names)
    connections: list[SketchConnection] = []
    for i, raw in enumerate(raw_connections):
        where = f"connections[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object")
        a = _text_field(raw.get("a"), f"{where}.a")
        b = _text_field(raw.get("b"), f"{where}.b")
        for end in (a, b):
            if end not in known:
                raise ValueError(f"{where} names a part that is not in the layout: {end!r}")
        if a == b:
            raise ValueError(f"{where} joins {a!r} to itself")
        joint = _text_field(raw.get("joint"), f"{where}.joint")
        provenance = _provenance(raw.get("provenance"), f"{where}.provenance", "intended")
        connections.append(SketchConnection(a=a, b=b, joint=joint, provenance=provenance))

    notes = data.get("notes") or ""
    if not isinstance(notes, str):
        raise ValueError("notes must be a string")
    return ConceptLayout(
        concept_name=concept_name,
        parts=parts,
        connections=tuple(connections),
        notes=notes.strip(),
    )


# -- formatting ---------------------------------------------------------------


def _n(v: float) -> str:
    """A coordinate: two decimals at most, never ``-0``, so output is stable."""
    s = f"{v:.2f}"
    if s == "-0.00":
        s = "0.00"
    return s.rstrip("0").rstrip(".")


def fmt_mm(v: float) -> str:
    """A dimension value as printed on the sketch: whole mm, or one decimal."""
    s = f"{v:.1f}"
    if s == "-0.0":
        s = "0.0"
    return s[:-2] if s.endswith(".0") else s


def _short(text: str, limit: int) -> str:
    """Shorten a name to fit a label; the part schedule carries it in full."""
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _t(text: str, limit: int | None = None) -> str:
    """Escape user text for SVG, dropping control characters XML cannot carry."""
    clean = "".join(ch for ch in text if ch >= " " or ch == "\t")
    if limit is not None:
        clean = _short(clean, limit)
    return escape(clean, {'"': "&quot;"})


def _poly(points: list[Point]) -> str:
    return " ".join(f"{_n(x)},{_n(y)}" for x, y in points)


_STYLE = """
.bg{fill:#ffffff}
.panel{fill:none;stroke:#9aa4ae;stroke-width:1}
.head{font:700 18px sans-serif;fill:#1d252c}
.sub{font:13px sans-serif;fill:#8a1c1c}
.view-title{font:600 13px sans-serif;fill:#1d252c}
.small{font:11px sans-serif;fill:#3b444c}
.face-top{fill:#eef1f4}
.face-front{fill:#dde2e7}
.face-right{fill:#c9d0d7}
.face-ortho{fill:#e6eaee;fill-opacity:0.85}
.cap{fill:#f4f6f8}
.s-verified{stroke:#1b6e3a;stroke-width:1.8;stroke-linejoin:round}
.s-intended{stroke:#1f4e8c;stroke-width:1.3;stroke-linejoin:round}
.s-assumption{stroke:#a35c00;stroke-width:1.3;stroke-dasharray:6 3;stroke-linejoin:round}
.s-open{stroke:#c62828;stroke-width:1.3;stroke-dasharray:2 3;stroke-linejoin:round}
.swatch{fill:none}
.t-verified{font:600 11px sans-serif;fill:#1b6e3a}
.t-intended{font:11px sans-serif;fill:#1f4e8c}
.t-assumption{font:italic 11px sans-serif;fill:#a35c00}
.t-open{font:11px sans-serif;fill:#c62828}
.tag{font-weight:600}
.open-mark{font:700 13px sans-serif;fill:#c62828}
.leader{stroke:#6b747c;stroke-width:0.7;fill:none}
.anchor{fill:#3b444c}
.centre-line{stroke:#6b747c;stroke-width:0.6;stroke-dasharray:8 2 2 2;fill:none}
.dim-line{stroke:#4a525a;stroke-width:0.8;fill:none}
.arrow{fill:#4a525a}
.dim{font:11px sans-serif;fill:#1d252c}
.joint{stroke:#6b747c;stroke-width:1;stroke-dasharray:4 3;fill:none}
.joint-text{font:italic 10px sans-serif;fill:#3b444c;
stroke:#ffffff;stroke-width:3;paint-order:stroke}
.rule{stroke:#c3cad1;stroke-width:0.7}
.th{font:600 11px sans-serif;fill:#1d252c}
.block{fill:#fbfbf8;stroke:#1d252c;stroke-width:1.2}
"""


# -- orthographic views (sketch type B) ---------------------------------------

# Per view: the horizontal axis, the vertical axis (up on the page), the axis
# the viewer looks along, and the sign that sorts the farthest part first.
_ORTHO: dict[str, tuple[int, int, int, int, str]] = {
    "front": (0, 2, 1, -1, "FRONT (looking along +y)"),
    "side": (1, 2, 0, 1, "SIDE (from +x)"),
    "top": (0, 1, 2, 1, "TOP (from +z)"),
}
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
_ORTHO_MARGIN = (58.0, 58.0, 74.0, 46.0)  # left, right, top, bottom


def _arrow(x: float, y: float, dx: float, dy: float) -> str:
    # A filled arrowhead at (x, y) pointing along (dx, dy), drawn inline so
    # the SVG carries no marker ids to collide with another sketch on a page.
    px, py = -dy, dx
    pts = [(x, y), (x - 7 * dx + 2.5 * px, y - 7 * dy + 2.5 * py)]
    pts.append((x - 7 * dx - 2.5 * px, y - 7 * dy - 2.5 * py))
    return f'<polygon class="arrow" points="{_poly(pts)}"/>'


def _label_width(label: str) -> float:
    # A rough width for 11px sans-serif, enough to tell whether the text
    # fits between a dimension's arrows. No font metrics: stays stdlib.
    return 6.2 * len(label) + 16


def _dim_h(x1: float, x2: float, y_geom: float, y_dim: float, label: str) -> list[str]:
    out = ['<g class="dimension">']
    beyond = 4.0 if y_dim > y_geom else -4.0
    for x in (x1, x2):
        out.append(
            f'<line class="dim-line" x1="{_n(x)}" y1="{_n(y_geom)}" '
            f'x2="{_n(x)}" y2="{_n(y_dim + beyond)}"/>'
        )
    out.append(
        f'<line class="dim-line" x1="{_n(x1)}" y1="{_n(y_dim)}" x2="{_n(x2)}" y2="{_n(y_dim)}"/>'
    )
    out.append(_arrow(x1, y_dim, -1, 0))
    out.append(_arrow(x2, y_dim, 1, 0))
    if abs(x2 - x1) >= _label_width(label):
        x_text, anchor = (x1 + x2) / 2, "middle"
    else:
        x_text, anchor = max(x1, x2) + 6, "start"
    out.append(
        f'<text class="dim" x="{_n(x_text)}" y="{_n(y_dim - 3)}" '
        f'text-anchor="{anchor}">{_t(label)}</text>'
    )
    out.append("</g>")
    return out


def _dim_v(y1: float, y2: float, x_geom: float, x_dim: float, label: str) -> list[str]:
    out = ['<g class="dimension">']
    beyond = 4.0 if x_dim > x_geom else -4.0
    for y in (y1, y2):
        out.append(
            f'<line class="dim-line" x1="{_n(x_geom)}" y1="{_n(y)}" '
            f'x2="{_n(x_dim + beyond)}" y2="{_n(y)}"/>'
        )
    out.append(
        f'<line class="dim-line" x1="{_n(x_dim)}" y1="{_n(y1)}" x2="{_n(x_dim)}" y2="{_n(y2)}"/>'
    )
    top, bottom = min(y1, y2), max(y1, y2)
    out.append(_arrow(x_dim, top, 0, -1))
    out.append(_arrow(x_dim, bottom, 0, 1))
    mid = (y1 + y2) / 2
    if bottom - top >= _label_width(label):
        out.append(
            f'<text class="dim" x="{_n(x_dim - 3)}" y="{_n(mid)}" text-anchor="middle" '
            f'transform="rotate(-90 {_n(x_dim - 3)} {_n(mid)})">{_t(label)}</text>'
        )
    else:
        # Too short to write along: the text goes beside the line, away
        # from the part, so it never sits on the arrows.
        side = 1.0 if x_dim > x_geom else -1.0
        anchor = "start" if side > 0 else "end"
        out.append(
            f'<text class="dim" x="{_n(x_dim + 6 * side)}" y="{_n(mid + 4)}" '
            f'text-anchor="{anchor}">{_t(label)}</text>'
        )
    out.append("</g>")
    return out


def _main_parts(layout: ConceptLayout) -> list[SketchPart]:
    """The two largest parts by volume, whose own sizes are dimensioned too."""
    if len(layout.parts) < 2:
        return []
    ranked = sorted(enumerate(layout.parts), key=lambda ip: (-ip[1].volume, ip[0]))
    return [p for _, p in ranked[:2]]


def _ortho_scale(layout: ConceptLayout, width: float, height: float) -> float:
    overall = layout.overall()
    aw = width - _ORTHO_MARGIN[0] - _ORTHO_MARGIN[1]
    ah = height - _ORTHO_MARGIN[2] - _ORTHO_MARGIN[3]
    scales = []
    for h_axis, v_axis, *_ in _ORTHO.values():
        scales.append(min(aw / overall[h_axis], ah / overall[v_axis]))
    return min(scales)


def _ortho_view(
    layout: ConceptLayout,
    view: str,
    box: tuple[float, float, float, float],
    scale: float,
) -> list[str]:
    px, py, pw, ph = box
    h_axis, v_axis, view_axis, depth_sign, title = _ORTHO[view]
    lo, hi = layout.bounds()
    ext_h = (hi[h_axis] - lo[h_axis]) * scale
    ext_v = (hi[v_axis] - lo[v_axis]) * scale
    left_m, right_m, top_m, bottom_m = _ORTHO_MARGIN
    area_w = pw - left_m - right_m
    area_h = ph - top_m - bottom_m
    x0 = px + left_m + (area_w - ext_h) / 2
    y_base = py + top_m + (area_h + ext_v) / 2

    def sx(v: float) -> float:
        return x0 + (v - lo[h_axis]) * scale

    def sy(v: float) -> float:
        return y_base - (v - lo[v_axis]) * scale

    out = [
        f'<g class="view" data-view="{view}">',
        f'<rect class="panel" x="{_n(px)}" y="{_n(py)}" width="{_n(pw)}" height="{_n(ph)}"/>',
        f'<text class="view-title" x="{_n(px + 10)}" y="{_n(py + 20)}">{title}</text>',
    ]
    order = sorted(
        enumerate(layout.parts), key=lambda ip: (depth_sign * ip[1].centre[view_axis], ip[0])
    )
    for _, part in order:
        x1, x2 = sx(part.position[h_axis]), sx(part.upper[h_axis])
        y1, y2 = sy(part.upper[v_axis]), sy(part.position[v_axis])
        stroke = f"s-{part.provenance}"
        if part.shape == "cylinder" and _AXIS_INDEX[part.axis] == view_axis:
            out.append(
                f'<ellipse class="face-ortho {stroke}" cx="{_n((x1 + x2) / 2)}" '
                f'cy="{_n((y1 + y2) / 2)}" rx="{_n((x2 - x1) / 2)}" ry="{_n((y2 - y1) / 2)}"/>'
            )
        else:
            out.append(
                f'<rect class="face-ortho {stroke}" x="{_n(x1)}" y="{_n(y1)}" '
                f'width="{_n(x2 - x1)}" height="{_n(y2 - y1)}"/>'
            )
            if part.shape == "cylinder":
                # The axis of a cylinder seen side-on: a dash-dot centre line.
                if _AXIS_INDEX[part.axis] == h_axis:
                    ym = (y1 + y2) / 2
                    line = (x1 - 4, ym, x2 + 4, ym)
                else:
                    xm = (x1 + x2) / 2
                    line = (xm, y1 - 4, xm, y2 + 4)
                out.append(
                    f'<line class="centre-line" x1="{_n(line[0])}" y1="{_n(line[1])}" '
                    f'x2="{_n(line[2])}" y2="{_n(line[3])}"/>'
                )

    left, right = sx(lo[h_axis]), sx(hi[h_axis])
    top, bottom = sy(hi[v_axis]), sy(lo[v_axis])
    overall = layout.overall()
    # Overall size of the assembly in this view: horizontal below, vertical left.
    out += _dim_h(left, right, bottom + 4, bottom + 24, fmt_mm(overall[h_axis]))
    out += _dim_v(top, bottom, left - 4, left - 24, fmt_mm(overall[v_axis]))
    # The main parts' own sizes: widths above the view, heights to the right.
    # The top view would only repeat the front and side, so it carries none.
    if view != "top":
        for i, part in enumerate(_main_parts(layout)):
            name = _short(part.name, 24)
            out += _dim_h(
                sx(part.position[h_axis]),
                sx(part.upper[h_axis]),
                sy(part.upper[v_axis]) - 4,
                top - 18 - 18 * i,
                f"{name} {fmt_mm(part.size[h_axis])}",
            )
            if view == "front":
                out += _dim_v(
                    sy(part.upper[v_axis]),
                    sy(part.position[v_axis]),
                    sx(part.upper[h_axis]) + 4,
                    right + 20 + 18 * i,
                    f"{name} {fmt_mm(part.size[v_axis])}",
                )
    out.append("</g>")
    return out


# -- isometric views (sketch types C and D) -----------------------------------

_COS30 = math.sqrt(3) / 2
# The viewer sits toward (+x, -y, +z): the front, right and top faces show.
_VIEW_DIR: Vec = (1.0, -1.0, 1.0)
_LABEL_COLUMN = 220.0
_CYLINDER_SEGMENTS = 24


def _iso(p: Vec) -> Point:
    return ((p[0] + p[1]) * _COS30, (p[0] - p[1]) * 0.5 - p[2])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _corners(part: SketchPart, offset: Vec) -> list[Vec]:
    lo, hi = _add(part.position, offset), _add(part.upper, offset)
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _hull(points: list[Point]) -> list[Point]:
    """Convex hull (monotone chain); the outline of a cylinder seen at an angle."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def explode_offsets(layout: ConceptLayout) -> list[Vec]:
    """How far each part moves out along its assembly axis in the exploded view.

    ``explode`` names the axis a part assembles along. ``auto`` takes the axis
    on which the part sits farthest from the assembly's centre, relative to the
    assembly's size on that axis, and moves it outward on that side; a part at
    the centre (the base everything joins) stays put.
    """
    lo, hi = layout.bounds()
    centre = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    extent = layout.overall()
    span = max(extent)
    offsets: list[Vec] = []
    for part in layout.parts:
        if part.explode == "none":
            offsets.append((0.0, 0.0, 0.0))
            continue
        if part.explode == "auto":
            rel = [
                (part.centre[i] - centre[i]) / extent[i] if extent[i] > 0 else 0.0 for i in range(3)
            ]
            # Height first, then width, then depth: most products assemble
            # top-down, so a tie explodes vertically.
            axis = max((2, 0, 1), key=lambda i: abs(rel[i]))
            if abs(rel[axis]) < 1e-9:
                offsets.append((0.0, 0.0, 0.0))
                continue
            sign = 1.0 if rel[axis] > 0 else -1.0
        else:
            axis = _AXIS_INDEX[part.explode[1]]
            sign = 1.0 if part.explode[0] == "+" else -1.0
        distance = 0.8 * abs(part.centre[axis] - centre[axis]) + 0.35 * span
        moved = [0.0, 0.0, 0.0]
        moved[axis] = sign * distance
        offsets.append((moved[0], moved[1], moved[2]))
    return offsets


def _part_faces(
    part: SketchPart, offset: Vec, to_page: Callable[[Vec], Point]
) -> list[tuple[str, list[Point]]]:
    """A part's visible faces in the isometric view, as (face class, outline)."""
    if part.shape == "box":
        lo, hi = _add(part.position, offset), _add(part.upper, offset)
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        faces = (
            ("face-front", [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
            ("face-right", [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
            ("face-top", [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
        )
        return [(cls, [to_page(c) for c in pts]) for cls, pts in faces]
    a = _AXIS_INDEX[part.axis]
    u, v = (i for i in range(3) if i != a)
    c = _add(part.centre, offset)
    ru, rv, half = part.size[u] / 2, part.size[v] / 2, part.size[a] / 2
    caps: dict[float, list[Vec]] = {}
    for end in (-1.0, 1.0):
        ring: list[Vec] = []
        for k in range(_CYLINDER_SEGMENTS):
            theta = 2 * math.pi * k / _CYLINDER_SEGMENTS
            p = [c[0], c[1], c[2]]
            p[u] += ru * math.cos(theta)
            p[v] += rv * math.sin(theta)
            p[a] += end * half
            ring.append((p[0], p[1], p[2]))
        caps[end] = ring
    body = _hull([to_page(p) for ring in caps.values() for p in ring])
    visible = 1.0 if _VIEW_DIR[a] > 0 else -1.0
    return [("face-right", body), ("cap", [to_page(p) for p in caps[visible]])]


def _iso_part(part: SketchPart, offset: Vec, to_page: Callable[[Vec], Point]) -> list[str]:
    stroke = f"s-{part.provenance}"
    return [
        f'<polygon class="{cls} {stroke}" points="{_poly(pts)}"/>'
        for cls, pts in _part_faces(part, offset, to_page)
    ]


def _painter_order(layout: ConceptLayout, offsets: list[Vec]) -> list[int]:
    # Farthest from the viewer first.
    return sorted(
        range(len(layout.parts)),
        key=lambda i: (
            sum(_add(layout.parts[i].centre, offsets[i])[k] * _VIEW_DIR[k] for k in range(3)),
            i,
        ),
    )


def iso_faces(
    layout: ConceptLayout, width: float, height: float, margin: float = 0.08
) -> list[tuple[str, str, list[Point]]]:
    """The isometric view as filled polygons in a ``width`` x ``height`` page.

    Each entry is ``(part name, face class, outline)``, in painter's order
    (draw them in order and nearer faces cover farther ones). Face classes
    are ``face-front``, ``face-right``, ``face-top`` and ``cap``. This is
    what a raster backend draws to condition an image model on the layout
    (FORGE-761): the same projection as the sketch's isometric view, so the
    picture and the sketch agree on proportions.
    """
    zero: list[Vec] = [(0.0, 0.0, 0.0)] * len(layout.parts)
    projected = [_iso(c) for part in layout.parts for c in _corners(part, (0.0, 0.0, 0.0))]
    min_x = min(p[0] for p in projected)
    max_x = max(p[0] for p in projected)
    min_y = min(p[1] for p in projected)
    max_y = max(p[1] for p in projected)
    area_w, area_h = width * (1 - 2 * margin), height * (1 - 2 * margin)
    scale = min(area_w / max(max_x - min_x, 1e-9), area_h / max(max_y - min_y, 1e-9))
    ox = (width - (max_x - min_x) * scale) / 2
    oy = (height - (max_y - min_y) * scale) / 2

    def to_page(p: Vec) -> Point:
        ix, iy = _iso(p)
        return (ox + (ix - min_x) * scale, oy + (iy - min_y) * scale)

    out: list[tuple[str, str, list[Point]]] = []
    for i in _painter_order(layout, zero):
        part = layout.parts[i]
        out += [(part.name, cls, pts) for cls, pts in _part_faces(part, zero[i], to_page)]
    return out


#: The straight-on views an image can be conditioned on, as
#: ``view -> (horizontal axis, horizontal sign, looking axis, looking sign)``.
#: The vertical axis is always z (up). The horizontal axis runs to the right
#: of the page; its sign is the viewer's right-hand direction, so each view is
#: what a person standing on that side sees, not a mirror image:
#:
#: * ``front`` stands at y < 0 and looks along +y: x to the right.
#: * ``back`` stands past the far side and looks along -y: x mirrored.
#: * ``left`` stands at x < 0 and looks along +x: the front (y = 0) on the right.
#: * ``right`` stands past x max and looks along -x: the front on the left.
ORTHO_IMAGE_VIEWS: dict[str, tuple[int, float, int, float]] = {
    "front": (0, 1.0, 1, 1.0),
    "left": (1, -1.0, 0, 1.0),
    "back": (0, -1.0, 1, -1.0),
    "right": (1, 1.0, 0, -1.0),
}
#: Every view ``twin.render_concept_image`` can draw, isometric first.
IMAGE_VIEWS: tuple[str, ...] = ("isometric", *ORTHO_IMAGE_VIEWS)
_ELLIPSE_SEGMENTS = 48


def ortho_faces(
    layout: ConceptLayout, view: str, width: float, height: float, margin: float = 0.08
) -> list[tuple[str, str, list[Point]]]:
    """A straight-on orthographic view as filled polygons in a page.

    The same shape as :func:`iso_faces`: ``(part name, face class, outline)``
    in painter's order (farthest from the viewer first), in pixel
    coordinates. A box, and a cylinder seen side-on, is its silhouette
    rectangle (``face-front``); a cylinder seen end-on is an ellipse
    (``cap``). Every orthographic view shares one scale, fitted so the
    widest of the four still fits, so the product is the same height in the
    front and the side view and a multiview model sees consistent sizes.
    """
    if view not in ORTHO_IMAGE_VIEWS:
        raise ValueError(
            f"unknown orthographic view {view!r}; one of {', '.join(ORTHO_IMAGE_VIEWS)}"
        )
    h_axis, h_sign, look_axis, look_sign = ORTHO_IMAGE_VIEWS[view]
    lo, hi = layout.bounds()
    overall = layout.overall()
    area_w, area_h = width * (1 - 2 * margin), height * (1 - 2 * margin)
    widest = max(overall[0], overall[1], 1e-9)
    scale = min(area_w / widest, area_h / max(overall[2], 1e-9))
    # The page position of the horizontal coordinate: u grows to the right.
    u_lo = min(h_sign * lo[h_axis], h_sign * hi[h_axis])
    ox = (width - overall[h_axis] * scale) / 2
    oy = (height - overall[2] * scale) / 2

    def page(u: float, z: float) -> Point:
        return (ox + (u - u_lo) * scale, oy + (hi[2] - z) * scale)

    order = sorted(
        range(len(layout.parts)),
        # Farthest first: the larger the coordinate along the look, the farther.
        key=lambda i: (-look_sign * layout.parts[i].centre[look_axis], i),
    )
    out: list[tuple[str, str, list[Point]]] = []
    for i in order:
        part = layout.parts[i]
        u1, u2 = sorted((h_sign * part.position[h_axis], h_sign * part.upper[h_axis]))
        z1, z2 = part.position[2], part.upper[2]
        if part.shape == "cylinder" and _AXIS_INDEX[part.axis] == look_axis:
            cu, cz = (u1 + u2) / 2, (z1 + z2) / 2
            ru, rz = (u2 - u1) / 2, (z2 - z1) / 2
            ring = [
                page(
                    cu + ru * math.cos(2 * math.pi * k / _ELLIPSE_SEGMENTS),
                    cz + rz * math.sin(2 * math.pi * k / _ELLIPSE_SEGMENTS),
                )
                for k in range(_ELLIPSE_SEGMENTS)
            ]
            out.append((part.name, "cap", ring))
        else:
            rect = [page(u1, z1), page(u2, z1), page(u2, z2), page(u1, z2)]
            out.append((part.name, "face-front", rect))
    return out


def layout_to_dict(layout: ConceptLayout) -> dict[str, Any]:
    """A parsed layout back as tool input, so a stored sketch can be redrawn.

    ``parse_layout(layout_to_dict(x))`` gives ``x`` again, and therefore the
    same SVG. Kept on the sketch's metadata so a later tool (the AI concept
    image, FORGE-761) can start from exactly the layout the sketch shows.
    """
    return {
        "concept_name": layout.concept_name,
        "parts": [
            {
                "name": p.name,
                "shape": p.shape,
                "size_mm": list(p.size),
                "position_mm": list(p.position),
                "provenance": p.provenance,
                "axis": p.axis,
                "explode": p.explode,
            }
            for p in layout.parts
        ],
        "connections": [
            {"a": c.a, "b": c.b, "joint": c.joint, "provenance": c.provenance}
            for c in layout.connections
        ],
        "notes": layout.notes,
    }


def _iso_anchor(part: SketchPart, offset: Vec) -> Vec:
    # Where a leader line lands: the middle of the part's top face.
    c = _add(part.centre, offset)
    return (c[0], c[1], c[2] + part.size[2] / 2)


def _label(part: SketchPart, anchor: Point, lx: float, ly: float) -> list[str]:
    tag = part.provenance
    leader = _poly([anchor, (lx - 10, ly - 4), (lx - 2, ly - 4)])
    out = [
        f'<g class="label" data-part="{_t(part.name)}" data-provenance="{tag}">',
        f'<polyline class="leader" points="{leader}"/>',
        f'<circle class="anchor" cx="{_n(anchor[0])}" cy="{_n(anchor[1])}" r="2"/>',
        f'<line class="swatch s-{tag}" x1="{_n(lx)}" y1="{_n(ly - 4)}" '
        f'x2="{_n(lx + 18)}" y2="{_n(ly - 4)}"/>',
    ]
    if tag == "open":
        out.append(f'<text class="open-mark" x="{_n(lx + 22)}" y="{_n(ly + 1)}">?</text>')
    out.append(
        f'<text class="t-{tag}" x="{_n(lx + 34)}" y="{_n(ly)}">{_t(part.name, 26)} '
        f'<tspan class="tag">[{tag}]</tspan></text>'
    )
    out.append("</g>")
    return out


def _iso_view(
    layout: ConceptLayout,
    view: str,
    box: tuple[float, float, float, float],
    offsets: list[Vec],
    title: str,
) -> list[str]:
    px, py, pw, ph = box
    projected = [
        _iso(c)
        for part, off in zip(layout.parts, offsets, strict=True)
        for c in _corners(part, off)
    ]
    min_x = min(p[0] for p in projected)
    max_x = max(p[0] for p in projected)
    min_y = min(p[1] for p in projected)
    max_y = max(p[1] for p in projected)
    area_x, area_y = px + 20, py + 40
    area_w, area_h = pw - _LABEL_COLUMN - 40, ph - 60
    scale = min(area_w / max(max_x - min_x, 1e-9), area_h / max(max_y - min_y, 1e-9))
    ox = area_x + (area_w - (max_x - min_x) * scale) / 2
    oy = area_y + (area_h - (max_y - min_y) * scale) / 2

    def to_page(p: Vec) -> Point:
        ix, iy = _iso(p)
        return (ox + (ix - min_x) * scale, oy + (iy - min_y) * scale)

    out = [
        f'<g class="view" data-view="{view}">',
        f'<rect class="panel" x="{_n(px)}" y="{_n(py)}" width="{_n(pw)}" height="{_n(ph)}"/>',
        f'<text class="view-title" x="{_n(px + 10)}" y="{_n(py + 20)}">{title}</text>',
    ]
    # Painter's order: farthest from the viewer first.
    for i in _painter_order(layout, offsets):
        out += _iso_part(layout.parts[i], offsets[i], to_page)

    if view == "exploded":
        by_name = {p.name: i for i, p in enumerate(layout.parts)}
        for conn in layout.connections:
            ia, ib = by_name[conn.a], by_name[conn.b]
            pa = to_page(_add(layout.parts[ia].centre, offsets[ia]))
            pb = to_page(_add(layout.parts[ib].centre, offsets[ib]))
            mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            out.append(
                f'<g class="connection" data-joint="{_t(conn.joint)}">'
                f'<line class="joint" x1="{_n(pa[0])}" y1="{_n(pa[1])}" '
                f'x2="{_n(pb[0])}" y2="{_n(pb[1])}"/>'
                f'<text class="joint-text" x="{_n(mx)}" y="{_n(my)}" text-anchor="middle">'
                f"{_t(conn.joint, 28)}</text></g>"
            )

    anchors = [to_page(_iso_anchor(p, o)) for p, o in zip(layout.parts, offsets, strict=True)]
    slots = sorted(range(len(layout.parts)), key=lambda i: (anchors[i][1], anchors[i][0], i))
    step = min(26.0, (ph - 60) / max(len(slots), 1))
    lx = px + pw - _LABEL_COLUMN + 10
    for row, i in enumerate(slots):
        out += _label(layout.parts[i], anchors[i], lx, py + 46 + row * step)
    out.append("</g>")
    return out


# -- tables, legend and title block -------------------------------------------


def _cell(cls: str, x: float, y: float, text: str) -> str:
    return f'<text class="{cls}" x="{_n(x)}" y="{_n(y)}">{text}</text>'


def _rule(x: float, y: float, width: float = 740) -> str:
    return f'<line class="rule" x1="{_n(x)}" y1="{_n(y)}" x2="{_n(x + width)}" y2="{_n(y)}"/>'


def _tag_cell(tag: str, x: float, y: float) -> str:
    return _cell(f"t-{tag}", x, y, f"? {tag}" if tag == "open" else tag)


def _schedule(layout: ConceptLayout, x: float, y: float) -> tuple[list[str], float]:
    out = ['<g class="schedule">', _cell("view-title", x, y, "PART SCHEDULE (mm)")]
    cols = (0, 26, 240, 310, 450, 600)
    heads = ("#", "Part", "Shape", "W x D x H", "Position (min corner)", "Provenance")
    row_y = y + 22
    out += [_cell("th", x + c, row_y, h) for c, h in zip(cols, heads, strict=True)]
    for i, p in enumerate(layout.parts, start=1):
        row_y += 18
        shape = p.shape if p.shape == "box" else f"cylinder ({p.axis})"
        size = " x ".join(fmt_mm(v) for v in p.size)
        pos = ", ".join(fmt_mm(v) for v in p.position)
        cells = (str(i), _t(p.name, 34), shape, size, pos)
        out.append(_rule(x, row_y - 13))
        out += [_cell("small", x + c, row_y, cell) for c, cell in zip(cols, cells, strict=False)]
        out.append(_tag_cell(p.provenance, x + cols[5], row_y))
    if layout.connections:
        row_y += 34
        out.append(_cell("view-title", x, row_y, "CONNECTIONS"))
        row_y += 22
        ccols = (0, 240, 480, 600)
        heads = ("Part", "Joins", "Joint", "Provenance")
        out += [_cell("th", x + c, row_y, h) for c, h in zip(ccols, heads, strict=True)]
        for conn in layout.connections:
            row_y += 18
            out.append(_rule(x, row_y - 13))
            for c, cell in zip(ccols, (conn.a, conn.b, conn.joint), strict=False):
                out.append(_cell("small", x + c, row_y, _t(cell, 34)))
            out.append(_tag_cell(conn.provenance, x + ccols[3], row_y))
    out.append("</g>")
    return out, row_y - y + 10


def _legend_and_title(
    layout: ConceptLayout, x: float, y: float, w: float
) -> tuple[list[str], float]:
    out = ['<g class="legend">', _cell("view-title", x, y, "PROVENANCE")]
    row_y = y + 4
    for tag in PROVENANCE_TAGS:
        row_y += 20
        out.append(
            f'<rect class="face-ortho s-{tag}" x="{_n(x)}" y="{_n(row_y - 11)}" '
            'width="26" height="13"/>'
        )
        if tag == "open":
            out.append(_cell("open-mark", x + 9, row_y, "?"))
        out.append(_cell(f"t-{tag}", x + 36, row_y, f"{tag}: {_PROVENANCE_MEANING[tag]}"))
    out.append("</g>")

    by = row_y + 22
    lines = [
        ("head", _t(layout.concept_name, 40)),
        ("sub", DISCLAIMER),
        ("small", "Block layout only. No measurement may be read from this sketch."),
        ("small", "All dimensions in mm. x width, y depth, z height."),
        ("small", "Views: B orthographic (front, side, top), C isometric, D exploded."),
    ]
    if layout.notes:
        lines.append(("small", f"Notes: {_t(layout.notes, 64)}"))
    bh = 24 + 20 * len(lines)
    out.append('<g class="title-block">')
    out.append(f'<rect class="block" x="{_n(x)}" y="{_n(by)}" width="{_n(w)}" height="{_n(bh)}"/>')
    ty = by + 8
    for cls, text in lines:
        ty += 22 if cls == "head" else 20
        out.append(f'<text class="{cls}" x="{_n(x + 12)}" y="{_n(ty)}">{text}</text>')
    out.append("</g>")
    return out, by + bh - y


# -- the page -----------------------------------------------------------------

_PAGE_WIDTH = 1180.0


def render_concept_sketch(layout: ConceptLayout) -> str:
    """Draw ``layout`` as one self-contained SVG document. Deterministic."""
    with tracer.start_as_current_span("concept_sketch.render") as span:
        span.set_attribute("concept_sketch.part_count", len(layout.parts))
        span.set_attribute("concept_sketch.connection_count", len(layout.connections))
        try:
            svg = _render(layout)
        except Exception as exc:
            span.record_exception(exc)
            raise
        logger.info(
            "concept_sketch_rendered",
            concept_name=layout.concept_name,
            part_count=len(layout.parts),
            connection_count=len(layout.connections),
            svg_bytes=len(svg),
        )
        return svg


def _render(layout: ConceptLayout) -> str:
    body: list[str] = []
    w = _PAGE_WIDTH
    # Row 1: the three orthographic views, drawn to one shared scale so a
    # size measured off one agrees with the same size on the others.
    ortho_w, ortho_h, row1 = 370.0, 340.0, 64.0
    scale = _ortho_scale(layout, ortho_w, ortho_h)
    for col, view in enumerate(("front", "side", "top")):
        panel = (20 + col * (ortho_w + 15), row1, ortho_w, ortho_h)
        body += _ortho_view(layout, view, panel, scale)

    # Row 2: isometric and exploded, tall enough for one label per part.
    row2 = row1 + ortho_h + 16
    iso_h = max(420.0, 70.0 + 26.0 * len(layout.parts))
    zero = [(0.0, 0.0, 0.0)] * len(layout.parts)
    body += _iso_view(layout, "isometric", (20, row2, 570, iso_h), zero, "ISOMETRIC")
    body += _iso_view(
        layout,
        "exploded",
        (605, row2, 555, iso_h),
        explode_offsets(layout),
        "EXPLODED ISOMETRIC (parts moved along their assembly axis)",
    )

    # Row 3: the part schedule and connections, the legend and the title block.
    row3 = row2 + iso_h + 34
    schedule, sched_h = _schedule(layout, 20, row3)
    legend, legend_h = _legend_and_title(layout, 790, row3, 370)
    body += schedule + legend
    h = row3 + max(sched_h, legend_h) + 24

    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(w)} {_n(h)}" '
        f'width="{_n(w)}" height="{_n(h)}" role="img" class="concept-sketch">',
        f"<title>{_t(layout.concept_name)}: {DISCLAIMER}</title>",
        f"<style>{_STYLE.strip()}</style>",
        f'<rect class="bg" x="0" y="0" width="{_n(w)}" height="{_n(h)}"/>',
        f'<text class="head" x="20" y="30">{_t(layout.concept_name, 80)}</text>',
        f'<text class="sub" x="20" y="50">{DISCLAIMER}</text>',
    ]
    return "\n".join([*head, *body, "</svg>"]) + "\n"
