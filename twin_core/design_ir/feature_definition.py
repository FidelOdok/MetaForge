"""The shared feature definition: what the L2 preview and the exact build both read.

Spec section 2 asks that the fast preview and the exact CAD come from the same
structured feature definition. Here that definition **is** the design IR the
exact path lowers for a parametric sketch: a body, one sketch entity (its
elements already on the plane, ``v`` up) and one ``pad`` or ``revolve``
(optionally followed by an empty-selector ``shell``). Nothing else is a
second product definition:

* :func:`feature_entities` builds those IR entities from an approved
  parametric sketch, its chosen feature plan entry and the plane placement.
  :meth:`api_gateway.twin.exact_cad.ExactCadService.parametric_source` uses
  it, so the exact build lowers exactly these entities.
* :func:`definition_from_entities` reads the same entities back as the
  preview definition the browser draws (``dashboard/src/lib/preview``): the
  profile elements, the world frame of the sketch plane (CadQuery's own
  named-plane conventions, so the preview sits where the B-Rep will), the
  feature and its amount.
* :func:`definition_hash` is the SHA-256 of the canonical entities. The exact
  build stores it on the revision it commits, so an unchanged definition is
  not rebuilt (SYNC-008), and the browser knows which exact revision belongs
  to the definition it is showing.
* :class:`PreviewSolid` is the Python mirror of the browser's preview
  tessellation rules (the same arc and spline segment counts), used by the
  revision-bound comparison (section 7) to measure the L2 shape against the
  exact result: bounding box, volume, sampled surface distance and the
  intended through-holes.

Pure: no I/O, no gateway imports.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from twin_core.design_ir.models import (
    SketchArc,
    SketchCircle,
    SketchElement,
    SketchEllipse,
    SketchLine,
    SketchRectangle,
    SketchSpline,
)

DEFINITION_SCHEMA = "metaforge.feature_definition/1"

#: Preview tessellation rules, shared with ``dashboard/src/lib/preview/profile.ts``.
ARC_SEGMENT_DEG = 6.0
SPLINE_SEGMENTS_PER_SPAN = 8
REVOLVE_SEGMENT_DEG = 6.0
#: The depth shown, labelled as assumed, while the person has not given one.
ASSUMED_DEPTH_FRACTION = 0.1

#: Millimetres per sketch unit, by the sketch's unit.
UNIT_TO_MM: dict[str, float] = {"mm": 1.0, "in": 25.4}

Vec3 = tuple[float, float, float]
Point = tuple[float, float]

#: CadQuery's named planes: ``(x_dir, normal)``; ``y = normal x x``.
_CQ_PLANES: dict[str, tuple[Vec3, Vec3]] = {
    "XY": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "YZ": ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
    "XZ": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
}


class FeatureDefinitionError(ValueError):
    """A design IR that is not one supported preview feature."""


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: Sequence[float]) -> Vec3:
    n = math.sqrt(sum(float(c) * float(c) for c in a))
    if n <= 0:
        raise FeatureDefinitionError("a frame direction needs a non-zero length")
    return (float(a[0]) / n, float(a[1]) / n, float(a[2]) / n)


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


def frame_of(placement: dict[str, Any] | None) -> dict[str, list[float]]:
    """The world frame (``origin``, ``x_dir``, ``y_dir``, ``normal``) of a sketch placement.

    ``placement`` is a sketch entity's ``plane``/``offset`` (CadQuery's named
    plane moved along its own normal) or an explicit ``frame``. ``None``
    means the XY plane at the origin.
    """
    p = placement or {}
    raw = p.get("frame")
    if isinstance(raw, dict) and raw.get("origin") is not None:
        origin: Vec3 = tuple(float(c) for c in raw["origin"])  # type: ignore[assignment]
        x = _unit(raw["x_dir"])
        n = _unit(raw["normal"])
    else:
        name = str(p.get("plane") or "XY")
        if name not in _CQ_PLANES:
            raise FeatureDefinitionError(f"unknown sketch plane {name!r}")
        x, n = _CQ_PLANES[name]
        origin = _mul(n, float(p.get("offset") or 0.0))
    y = _cross(n, x)
    return {
        "origin": list(origin),
        "x_dir": list(x),
        "y_dir": list(y),
        "normal": list(n),
    }


def _frame_vecs(frame: dict[str, Any]) -> tuple[Vec3, Vec3, Vec3, Vec3]:
    def v(key: str) -> Vec3:
        raw = frame[key]
        return (float(raw[0]), float(raw[1]), float(raw[2]))

    return v("origin"), v("x_dir"), v("y_dir"), v("normal")


# ---------------------------------------------------------------------------
# The IR the exact path lowers
# ---------------------------------------------------------------------------


def feature_entities(
    sketch_entity: dict[str, Any],
    *,
    op: str,
    amount: float,
) -> list[dict[str, Any]]:
    """The design IR entities of one parametric feature: body, sketch, pad or revolve.

    ``sketch_entity`` is the dumped ``SketchEntity`` (elements on the plane,
    placement fields set). ``amount`` is the depth (extrude) or the angle in
    degrees (revolve, capped at a whole turn).
    """
    entities: list[dict[str, Any]] = [{"id": "body1", "op": "create_body"}, sketch_entity]
    if op == "extrude":
        entities.append(
            {
                "id": "feature1",
                "op": "pad",
                "body_ref": "body1",
                "sketch_ref": "sketch1",
                "depth": float(amount),
            }
        )
    elif op == "revolve":
        entities.append(
            {
                "id": "feature1",
                "op": "revolve",
                "body_ref": "body1",
                "sketch_ref": "sketch1",
                "angle": min(float(amount), 360.0),
            }
        )
    else:
        raise FeatureDefinitionError(f"unsupported feature op {op!r}")
    return entities


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        # Round so a value that only moved in its last bits hashes the same.
        r = round(value, 9)
        return 0.0 if r == 0 else r
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return value


def definition_hash(entities: Iterable[dict[str, Any]]) -> str:
    """SHA-256 of the canonical design IR entities (key order and float noise ignored)."""
    blob = json.dumps(_canonical(list(entities)), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def definition_from_entities(
    entities: Sequence[dict[str, Any]], *, scale_to_mm: float = 1.0
) -> dict[str, Any]:
    """The preview definition the browser draws, from the exact path's IR entities.

    Supported: one sketch, then ``pad`` or ``revolve``, then an optional
    empty-selector ``shell``. Anything else raises
    :class:`FeatureDefinitionError` (the preview does not pretend).
    """
    sketches = [e for e in entities if isinstance(e, dict) and e.get("op") == "sketch"]
    features = [e for e in entities if isinstance(e, dict) and e.get("op") in ("pad", "revolve")]
    shells = [e for e in entities if isinstance(e, dict) and e.get("op") == "shell"]
    others = [
        e
        for e in entities
        if isinstance(e, dict)
        and e.get("op") not in ("create_body", "sketch", "pad", "revolve", "shell")
    ]
    if len(sketches) != 1 or len(features) != 1 or others or len(shells) > 1:
        raise FeatureDefinitionError(
            "the preview draws one sketch with one extrude or revolve (and an optional shell)"
        )
    sketch, feature = sketches[0], features[0]
    if shells and shells[0].get("face_selectors"):
        raise FeatureDefinitionError("the preview draws only a shell with no face selection")
    op = "extrude" if feature["op"] == "pad" else "revolve"
    amount = float(feature["depth"] if op == "extrude" else feature.get("angle", 360.0))
    elements = [
        e
        for e in (sketch.get("elements") or [])
        if isinstance(e, dict) and not e.get("construction")
    ]
    out: dict[str, Any] = {
        "schema": DEFINITION_SCHEMA,
        "op": op,
        "elements": elements,
        "frame": frame_of(sketch),
        "scale_to_mm": float(scale_to_mm),
        "depth" if op == "extrude" else "angle": amount,
        "definition_hash": definition_hash(entities),
    }
    if shells:
        out["shell_thickness"] = float(shells[0]["thickness"])
    return out


# ---------------------------------------------------------------------------
# Profile loops (mirror of dashboard/src/lib/preview/profile.ts)
# ---------------------------------------------------------------------------


def _arc_points(center: Point, radius: float, start_deg: float, sweep_deg: float) -> list[Point]:
    n = max(2, math.ceil(abs(sweep_deg) / ARC_SEGMENT_DEG))
    out: list[Point] = []
    for i in range(n + 1):
        a = math.radians(start_deg + sweep_deg * i / n)
        out.append((center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)))
    return out


def _catmull_rom(points: Sequence[Point], closed: bool) -> list[Point]:
    pts = list(points)
    if len(pts) < 2:
        return pts
    spans = len(pts) if closed else len(pts) - 1
    out: list[Point] = []

    def at(i: int) -> Point:
        if closed:
            return pts[i % len(pts)]
        return pts[min(max(i, 0), len(pts) - 1)]

    for s in range(spans):
        p0, p1, p2, p3 = at(s - 1), at(s), at(s + 1), at(s + 2)
        for k in range(SPLINE_SEGMENTS_PER_SPAN):
            t = k / SPLINE_SEGMENTS_PER_SPAN
            t2, t3 = t * t, t * t * t
            out.append(
                (
                    0.5
                    * (
                        2 * p1[0]
                        + (-p0[0] + p2[0]) * t
                        + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                        + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
                    ),
                    0.5
                    * (
                        2 * p1[1]
                        + (-p0[1] + p2[1]) * t
                        + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                        + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
                    ),
                )
            )
    if not closed:
        out.append(pts[-1])
    return out


def _parse(el: dict[str, Any] | SketchElement) -> SketchElement:
    if not isinstance(el, dict):
        return el
    kind = el.get("type")
    model: Any = {
        "line": SketchLine,
        "circle": SketchCircle,
        "rectangle": SketchRectangle,
        "arc": SketchArc,
        "ellipse": SketchEllipse,
        "spline": SketchSpline,
    }.get(str(kind))
    if model is None:
        raise FeatureDefinitionError(f"unsupported sketch element {kind!r}")
    parsed: SketchElement = model.model_validate(el)
    return parsed


def element_polyline(el: dict[str, Any] | SketchElement) -> tuple[list[Point], bool]:
    """``(points, closed)`` of one element, with the preview's segment counts."""
    e = _parse(el)
    if isinstance(e, SketchLine):
        return [tuple(e.start), tuple(e.end)], False  # type: ignore[list-item]
    if isinstance(e, SketchArc):
        sweep = (e.end_angle - e.start_angle) % 360.0 or 360.0
        return _arc_points(tuple(e.center), e.radius, e.start_angle, sweep), False  # type: ignore[arg-type]
    if isinstance(e, SketchCircle):
        pts = _arc_points(tuple(e.center), e.radius, 0.0, 360.0)  # type: ignore[arg-type]
        return pts[:-1], True
    if isinstance(e, SketchEllipse):
        n = math.ceil(360.0 / ARC_SEGMENT_DEG)
        rot = math.radians(e.rotation)
        out: list[Point] = []
        for i in range(n):
            t = 2 * math.pi * i / n
            a, b = e.major_radius * math.cos(t), e.minor_radius * math.sin(t)
            out.append(
                (
                    e.center[0] + a * math.cos(rot) - b * math.sin(rot),
                    e.center[1] + a * math.sin(rot) + b * math.cos(rot),
                )
            )
        return out, True
    if isinstance(e, SketchRectangle):
        x, y = e.origin
        return [(x, y), (x + e.width, y), (x + e.width, y + e.height), (x, y + e.height)], True
    if isinstance(e, SketchSpline):
        pts = _catmull_rom([tuple(p) for p in e.points], e.closed)  # type: ignore[misc]
        return pts, bool(e.closed)
    raise FeatureDefinitionError(f"unsupported sketch element {e!r}")  # pragma: no cover


def _dist2(a: Point, b: Point) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def signed_area(loop: Sequence[Point]) -> float:
    s = 0.0
    for i in range(len(loop)):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % len(loop)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def point_in_loop(p: Point, loop: Sequence[Point]) -> bool:
    inside = False
    n = len(loop)
    j = n - 1
    for i in range(n):
        xi, yi = loop[i]
        xj, yj = loop[j]
        if (yi > p[1]) != (yj > p[1]):
            x = (xj - xi) * (p[1] - yi) / ((yj - yi) or 1e-300) + xi
            if p[0] < x:
                inside = not inside
        j = i
    return inside


@dataclass
class Profile:
    """The closed loops of a profile: one outer, its holes, and any separate islands."""

    outer: list[Point]
    holes: list[list[Point]] = field(default_factory=list)
    islands: list[list[Point]] = field(default_factory=list)
    open_chains: int = 0

    @property
    def loops(self) -> list[list[Point]]:
        return [self.outer, *self.holes, *self.islands]


def profile_loops(elements: Sequence[dict[str, Any] | SketchElement]) -> Profile:
    """Chain a profile's elements into closed loops and sort them into outer, holes, islands.

    Closed elements are loops on their own; open ones (lines, arcs, open
    splines) are joined end to end, reversing where needed. The largest
    loop is the outer boundary; loops inside it are holes (through-holes
    once extruded), loops outside it are islands. A chain that does not
    close is counted in ``open_chains`` and not drawn.
    """
    loops: list[list[Point]] = []
    chains: list[list[Point]] = []
    every: list[Point] = []
    for raw in elements:
        if isinstance(raw, dict) and raw.get("construction"):
            continue
        if not isinstance(raw, dict) and getattr(raw, "construction", False):
            continue
        pts, closed = element_polyline(raw)
        every.extend(pts)
        if closed:
            loops.append(pts)
        elif len(pts) >= 2:
            chains.append(pts)
    if every:
        xs, ys = [p[0] for p in every], [p[1] for p in every]
        extent = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    else:
        extent = 0.0
    tol2 = max(1e-6, extent * 1e-4) ** 2
    open_chains = 0
    unused = list(chains)
    while unused:
        loop = list(unused.pop(0))
        closed = _dist2(loop[0], loop[-1]) <= tol2
        while not closed:
            end = loop[-1]
            nxt = None
            for i, c in enumerate(unused):
                if _dist2(c[0], end) <= tol2:
                    nxt = (i, c)
                    break
                if _dist2(c[-1], end) <= tol2:
                    nxt = (i, list(reversed(c)))
                    break
            if nxt is None:
                break
            unused.pop(nxt[0])
            loop.extend(nxt[1][1:])
            closed = _dist2(loop[0], loop[-1]) <= tol2
        if closed:
            loops.append(loop[:-1] if len(loop) > 3 else loop)
        else:
            open_chains += 1
    if not loops:
        raise FeatureDefinitionError("the profile has no closed loop to draw")
    loops.sort(key=lambda lp: abs(signed_area(lp)), reverse=True)
    outer = loops[0]
    holes: list[list[Point]] = []
    islands: list[list[Point]] = []
    for lp in loops[1:]:
        (holes if point_in_loop(lp[0], outer) else islands).append(lp)
    return Profile(outer=outer, holes=holes, islands=islands, open_chains=open_chains)


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = 0.0 if ll <= 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _boundary_dist(p: Point, loops: Sequence[Sequence[Point]]) -> float:
    best = math.inf
    for lp in loops:
        n = len(lp)
        for i in range(n):
            d = _seg_dist(p, lp[i], lp[(i + 1) % n])
            if d < best:
                best = d
    return best


def _inside(p: Point, profile: Profile) -> bool:
    if point_in_loop(p, profile.outer):
        return not any(point_in_loop(p, h) for h in profile.holes)
    return any(point_in_loop(p, i) for i in profile.islands)


def _scaled(profile: Profile, s: float) -> Profile:
    def sc(lp: list[Point]) -> list[Point]:
        return [(x * s, y * s) for x, y in lp]

    return Profile(
        outer=sc(profile.outer),
        holes=[sc(h) for h in profile.holes],
        islands=[sc(i) for i in profile.islands],
        open_chains=profile.open_chains,
    )


def inset_loop(loop: Sequence[Point], distance: float) -> list[Point]:
    """``loop`` moved inward by ``distance`` (mitred offset; approximate on concave corners)."""
    pts = list(loop)
    if signed_area(pts) < 0:
        pts.reverse()
    n = len(pts)
    out: list[Point] = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        e1 = (p1[0] - p0[0], p1[1] - p0[1])
        e2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*e1) or 1e-12, math.hypot(*e2) or 1e-12
        n1 = (-e1[1] / l1, e1[0] / l1)
        n2 = (-e2[1] / l2, e2[0] / l2)
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by) or 1e-12
        cos_half = max(0.2, (bx * n1[0] + by * n1[1]) / bl)
        k = distance / cos_half
        out.append((p1[0] + bx / bl * k, p1[1] + by / bl * k))
    return out


# ---------------------------------------------------------------------------
# The preview solid (measures, distance, samples)
# ---------------------------------------------------------------------------


def _first_moment_u(loop: Sequence[Point]) -> float:
    """∫∫ u dA over a loop (signed by orientation)."""
    s = 0.0
    n = len(loop)
    for i in range(n):
        u1, v1 = loop[i]
        u2, v2 = loop[(i + 1) % n]
        s += (u1 + u2) * (u1 * v2 - u2 * v1)
    return s / 6.0


def _region_area(profile: Profile) -> float:
    return (
        abs(signed_area(profile.outer))
        - sum(abs(signed_area(h)) for h in profile.holes)
        + sum(abs(signed_area(i)) for i in profile.islands)
    )


def _region_moment(profile: Profile) -> float:
    def m(lp: Sequence[Point]) -> float:
        return _first_moment_u(lp) * (1.0 if signed_area(lp) >= 0 else -1.0)

    return m(profile.outer) - sum(m(h) for h in profile.holes) + sum(m(i) for i in profile.islands)


def _grid_inside(profile: Profile, count: int) -> list[Point]:
    """About ``count`` points on a regular grid inside the region (deterministic)."""
    xs = [p[0] for lp in profile.loops for p in lp]
    ys = [p[1] for lp in profile.loops for p in lp]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    area = max(_region_area(profile), 1e-12)
    step = math.sqrt(area / max(count, 1))
    out: list[Point] = []
    if step <= 0:
        return out
    nx, ny = max(1, int(w / step)), max(1, int(h / step))
    for i in range(nx):
        for j in range(ny):
            p = (min(xs) + (i + 0.5) * w / nx, min(ys) + (j + 0.5) * h / ny)
            if _inside(p, profile):
                out.append(p)
    return out


def _perimeter_points(loops: Sequence[Sequence[Point]], count: int) -> list[Point]:
    segs: list[tuple[Point, Point, float]] = []
    for lp in loops:
        for i in range(len(lp)):
            a, b = lp[i], lp[(i + 1) % len(lp)]
            segs.append((a, b, math.hypot(b[0] - a[0], b[1] - a[1])))
    total = sum(s[2] for s in segs)
    if total <= 0 or count <= 0:
        return []
    out: list[Point] = []
    step = total / count
    target = step / 2
    walked = 0.0
    for a, b, length in segs:
        while target <= walked + length and len(out) < count:
            t = (target - walked) / length if length > 0 else 0.0
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            target += step
        walked += length
    return out


@dataclass
class PreviewSolid:
    """The L2 preview shape, measured the way the browser builds it."""

    op: str
    profile: Profile
    origin: Vec3
    x: Vec3
    y: Vec3
    n: Vec3
    amount: float  # depth in mm, or angle in degrees
    shell_thickness: float | None = None

    # -- construction ---------------------------------------------------

    @classmethod
    def from_definition(cls, definition: dict[str, Any]) -> PreviewSolid:
        scale = float(definition.get("scale_to_mm") or 1.0)
        profile = _scaled(profile_loops(definition.get("elements") or []), scale)
        origin, x, y, n = _frame_vecs(definition["frame"])
        op = str(definition.get("op"))
        if op == "extrude":
            amount = float(definition["depth"]) * scale
        elif op == "revolve":
            amount = min(float(definition.get("angle") or 360.0), 360.0)
        else:
            raise FeatureDefinitionError(f"unsupported feature op {op!r}")
        shell = definition.get("shell_thickness")
        return cls(
            op=op,
            profile=profile,
            origin=origin,
            x=x,
            y=y,
            n=n,
            amount=amount,
            shell_thickness=float(shell) * scale if shell else None,
        )

    def _inner(self) -> PreviewSolid | None:
        """The cavity an empty-selector shell leaves (approximate: an inset, both ends)."""
        t = self.shell_thickness
        if not t or self.op != "extrude" or self.amount <= 2 * t:
            return None
        inner = Profile(outer=inset_loop(self.profile.outer, t))
        return PreviewSolid(
            op="extrude",
            profile=inner,
            origin=_add(self.origin, _mul(self.n, t)),
            x=self.x,
            y=self.y,
            n=self.n,
            amount=self.amount - 2 * t,
        )

    # -- mapping ----------------------------------------------------------

    def _world(self, u: float, v: float, h: float) -> Vec3:
        return _add(self.origin, _add(_mul(self.x, u), _add(_mul(self.y, v), _mul(self.n, h))))

    def _revolved(self, u: float, v: float, deg: float) -> Vec3:
        a = math.radians(deg)
        radial = _sub(_mul(self.x, math.cos(a)), _mul(self.n, math.sin(a)))
        return _add(self.origin, _add(_mul(self.y, v), _mul(radial, u)))

    # -- measures ---------------------------------------------------------

    @property
    def through_holes(self) -> int:
        """Intended through-holes: inner loops of an extruded profile."""
        return len(self.profile.holes) if self.op == "extrude" else 0

    def volume(self) -> float:
        if self.op == "extrude":
            vol = _region_area(self.profile) * self.amount
        else:
            vol = abs(_region_moment(self.profile)) * math.radians(self.amount)
        inner = self._inner()
        return vol - (inner.volume() if inner is not None else 0.0)

    def boundary_points(self) -> list[Vec3]:
        pts: list[Vec3] = []
        if self.op == "extrude":
            for lp in self.profile.loops:
                for u, v in lp:
                    pts.append(self._world(u, v, 0.0))
                    pts.append(self._world(u, v, self.amount))
            return pts
        steps = max(1, math.ceil(self.amount / REVOLVE_SEGMENT_DEG))
        for k in range(steps + 1):
            deg = self.amount * k / steps
            for lp in self.profile.loops:
                for u, v in lp:
                    pts.append(self._revolved(u, v, deg))
        return pts

    def bounding_box(self) -> dict[str, float]:
        pts = self.boundary_points()
        return {
            "min_x": min(p[0] for p in pts),
            "min_y": min(p[1] for p in pts),
            "min_z": min(p[2] for p in pts),
            "max_x": max(p[0] for p in pts),
            "max_y": max(p[1] for p in pts),
            "max_z": max(p[2] for p in pts),
        }

    # -- distance to the preview surface ----------------------------------

    def distance(self, p: Vec3) -> float:
        own = self._distance_outer(p)
        inner = self._inner()
        return min(own, inner._distance_outer(p)) if inner is not None else own

    def _distance_outer(self, p: Vec3) -> float:
        rel = _sub(p, self.origin)
        loops = self.profile.loops
        if self.op == "extrude":
            u, v, h = _dot(rel, self.x), _dot(rel, self.y), _dot(rel, self.n)
            d2 = _boundary_dist((u, v), loops)
            hc = min(max(h, 0.0), self.amount)
            best = math.hypot(d2, h - hc)
            if _inside((u, v), self.profile):
                best = min(best, abs(h), abs(h - self.amount))
            return best
        v = _dot(rel, self.y)
        a, b = _dot(rel, self.x), -_dot(rel, self.n)
        r = math.hypot(a, b)
        phi = math.degrees(math.atan2(b, a)) % 360.0
        best = math.inf
        full = self.amount >= 360.0
        # The surface of revolution: the profile boundary at this angle. A
        # profile point at u < 0 lands half a turn round from one at u > 0.
        if full or phi <= self.amount:
            best = min(best, _boundary_dist((r, v), loops))
        if full or (phi - 180.0) % 360.0 <= self.amount:
            best = min(best, _boundary_dist((-r, v), loops))
        if self.amount < 360.0:
            for cap in (0.0, self.amount):
                c = math.radians(cap)
                rc = a * math.cos(c) + b * math.sin(c)
                w = -a * math.sin(c) + b * math.cos(c)
                d2 = _boundary_dist((rc, v), loops)
                d = abs(w) if _inside((rc, v), self.profile) else math.hypot(d2, w)
                best = min(best, d)
        return best

    # -- samples on the preview surface ------------------------------------

    def samples(self, count: int = 96) -> list[Vec3]:
        out = self._samples_outer(count)
        inner = self._inner()
        if inner is not None:
            out += inner._samples_outer(max(8, count // 3))
        return out

    def _samples_outer(self, count: int) -> list[Vec3]:
        loops = self.profile.loops
        if self.op == "extrude":
            perim = sum(
                math.hypot(lp[(i + 1) % len(lp)][0] - lp[i][0], lp[(i + 1) % len(lp)][1] - lp[i][1])
                for lp in loops
                for i in range(len(lp))
            )
            wall = perim * self.amount
            cap = _region_area(self.profile)
            total = max(wall + 2 * cap, 1e-12)
            n_wall = max(4, round(count * wall / total))
            n_cap = max(2, round(count * cap / total))
            layers = max(1, min(6, round(math.sqrt(n_wall / 8))))
            out: list[Vec3] = []
            ring = _perimeter_points(loops, max(4, n_wall // layers))
            for k in range(layers):
                h = self.amount * (k + 0.5) / layers
                out += [self._world(u, v, h) for u, v in ring]
            inside = _grid_inside(self.profile, n_cap)
            out += [self._world(u, v, 0.0) for u, v in inside]
            out += [self._world(u, v, self.amount) for u, v in inside]
            return out
        steps = max(2, min(12, math.ceil(self.amount / 30.0)))
        ring = _perimeter_points(loops, max(8, count // steps))
        out = []
        for k in range(steps):
            deg = self.amount * (k + 0.5) / steps
            out += [self._revolved(u, v, deg) for u, v in ring]
        if self.amount < 360.0:
            inside = _grid_inside(self.profile, max(4, count // 8))
            for cap in (0.0, self.amount):
                out += [self._revolved(u, v, cap) for u, v in inside]
        return out


__all__ = [
    "ARC_SEGMENT_DEG",
    "ASSUMED_DEPTH_FRACTION",
    "DEFINITION_SCHEMA",
    "REVOLVE_SEGMENT_DEG",
    "SPLINE_SEGMENTS_PER_SPAN",
    "UNIT_TO_MM",
    "FeatureDefinitionError",
    "PreviewSolid",
    "Profile",
    "definition_from_entities",
    "definition_hash",
    "element_polyline",
    "feature_entities",
    "frame_of",
    "inset_loop",
    "point_in_loop",
    "profile_loops",
    "signed_area",
]
