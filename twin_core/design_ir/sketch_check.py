"""Deterministic re-check of a sketch's accepted constraints.

The dashboard fits and solves a sketch in the browser
(``dashboard/src/lib/sketch/solver/equations.ts``). Before the gateway
promotes an interpretation to a parametric sketch it re-checks the accepted
constraints itself with this mirror of those equations: same parameters,
same residuals (angle residuals scaled by the sketch's extent so every
residual is a length), so one tolerance applies to all. The two modules must
change together.

This is a check, not a solver: it evaluates each accepted constraint's
residual on the geometry as submitted and reports the ones that do not hold.
No numeric search runs here, so the result is the same on every run.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from observability.tracing import get_tracer
from twin_core.design_ir.models import (
    SketchArc,
    SketchCircle,
    SketchConstraint,
    SketchElement,
    SketchEllipse,
    SketchLine,
    SketchRectangle,
    SketchSpline,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.design_ir.sketch_check")

#: Residual tolerance relative to the sketch extent. The browser solves to
#: 1e-7 of the extent; this leaves room for rounding on the way over.
RELATIVE_TOLERANCE = 1e-5

Point = tuple[float, float]


class SketchRefError(ValueError):
    """A constraint names an element or point the sketch does not have."""


@dataclass(frozen=True)
class ConstraintCheck:
    """One accepted constraint's residual against the tolerance."""

    constraint_id: str
    type: str
    residual: float
    holds: bool
    reason: str = ""


def _elements_by_id(elements: Sequence[SketchElement]) -> dict[str, SketchElement]:
    return {el.id: el for el in elements if el.id}


def sketch_extent(elements: Sequence[SketchElement]) -> float:
    """Bounding-box diagonal of the elements' defining points (as the browser computes it)."""
    xs: list[float] = []
    ys: list[float] = []

    def add(x: float, y: float) -> None:
        xs.append(x)
        ys.append(y)

    for el in elements:
        if isinstance(el, SketchLine):
            add(*el.start)
            add(*el.end)
        elif isinstance(el, SketchSpline):
            for p in el.points:
                add(*p)
        elif isinstance(el, SketchRectangle):
            add(*el.origin)
            add(el.origin[0] + el.width, el.origin[1] + el.height)
        else:
            r = el.major_radius if isinstance(el, SketchEllipse) else el.radius
            add(el.center[0] - r, el.center[1] - r)
            add(el.center[0] + r, el.center[1] + r)
    if not xs:
        return 1.0
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _split(ref: str) -> tuple[str, str | None]:
    head, dot, key = ref.rpartition(".")
    if dot and key in ("start", "end", "center"):
        return head, key
    return ref, None


def point_of(el: SketchElement, key: str | None) -> Point | None:
    """A named point of an element (``None`` when it has no such point)."""
    if isinstance(el, SketchLine):
        return el.start if key == "start" else el.end if key == "end" else None
    if isinstance(el, SketchArc):
        if key == "center":
            return el.center
        if key in ("start", "end"):
            a = math.radians(el.start_angle if key == "start" else el.end_angle)
            return (el.center[0] + el.radius * math.cos(a), el.center[1] + el.radius * math.sin(a))
        return None
    if isinstance(el, (SketchCircle, SketchEllipse)):
        return el.center if key == "center" else None
    if isinstance(el, SketchSpline):
        if el.closed:
            return None
        return el.points[0] if key == "start" else el.points[-1] if key == "end" else None
    return None


def _element(by_id: dict[str, SketchElement], ref: str) -> SketchElement:
    eid, _ = _split(ref)
    el = by_id.get(eid)
    if el is None:
        raise SketchRefError(f"it refers to {eid}, which is not in the sketch")
    return el


def _point(by_id: dict[str, SketchElement], ref: str) -> Point:
    el = _element(by_id, ref)
    _, key = _split(ref)
    pt = point_of(el, key)
    if pt is None:
        raise SketchRefError(f"{ref} is not a point of a {el.type}")
    return pt


def _line(by_id: dict[str, SketchElement], ref: str) -> SketchLine:
    el = _element(by_id, ref)
    if not isinstance(el, SketchLine):
        raise SketchRefError(f"{ref} is a {el.type}, not a line")
    return el


def _round(by_id: dict[str, SketchElement], ref: str) -> SketchCircle | SketchArc:
    el = _element(by_id, ref)
    if not isinstance(el, (SketchCircle, SketchArc)):
        raise SketchRefError(f"{ref} is a {el.type}, not a circle or arc")
    return el


def _dir(line: SketchLine) -> Point:
    return (line.end[0] - line.start[0], line.end[1] - line.start[1])


def residuals(c: SketchConstraint, by_id: dict[str, SketchElement], scale: float) -> list[float]:
    """The constraint's residuals (zero when it holds). Raises ``SketchRefError``."""
    r = c.refs
    t = c.type
    if t == "coincident":
        a, b = _point(by_id, r[0]), _point(by_id, r[1])
        return [a[0] - b[0], a[1] - b[1]]
    if t in ("horizontal", "vertical"):
        ln = _line(by_id, r[0])
        k = 1 if t == "horizontal" else 0
        return [ln.end[k] - ln.start[k]]
    if t in ("parallel", "perpendicular"):
        d1, d2 = _dir(_line(by_id, r[0])), _dir(_line(by_id, r[1]))
        n = (math.hypot(*d1) * math.hypot(*d2)) or 1e-12
        v = d1[0] * d2[1] - d1[1] * d2[0] if t == "parallel" else d1[0] * d2[0] + d1[1] * d2[1]
        return [v / n * scale]
    if t == "tangent":
        e0, e1 = _element(by_id, r[0]), _element(by_id, r[1])
        if isinstance(e0, SketchLine) or isinstance(e1, SketchLine):
            ln = _line(by_id, r[0] if isinstance(e0, SketchLine) else r[1])
            rd = _round(by_id, r[1] if isinstance(e0, SketchLine) else r[0])
            d = _dir(ln)
            length = math.hypot(*d) or 1e-12
            cx, cy = rd.center[0] - ln.start[0], rd.center[1] - ln.start[1]
            return [abs(d[0] * cy - d[1] * cx) / length - rd.radius]
        ra, rb = _round(by_id, r[0]), _round(by_id, r[1])
        dist = math.hypot(ra.center[0] - rb.center[0], ra.center[1] - rb.center[1])
        if c.variant == "internal":
            return [dist - abs(ra.radius - rb.radius)]
        return [dist - (ra.radius + rb.radius)]
    if t == "equal":
        e0, e1 = _element(by_id, r[0]), _element(by_id, r[1])
        if isinstance(e0, SketchLine) and isinstance(e1, SketchLine):
            return [math.hypot(*_dir(e0)) - math.hypot(*_dir(e1))]
        return [_round(by_id, r[0]).radius - _round(by_id, r[1]).radius]
    if t == "concentric":
        a = _point(by_id, f"{_split(r[0])[0]}.center")
        b = _point(by_id, f"{_split(r[1])[0]}.center")
        return [a[0] - b[0], a[1] - b[1]]
    if t == "symmetric":
        pa, pb = _point(by_id, r[0]), _point(by_id, r[1])
        axis = _line(by_id, r[2])
        d = _dir(axis)
        length = math.hypot(*d) or 1e-12
        mx = (pa[0] + pb[0]) / 2 - axis.start[0]
        my = (pa[1] + pb[1]) / 2 - axis.start[1]
        return [
            (d[0] * my - d[1] * mx) / length,
            ((pb[0] - pa[0]) * d[0] + (pb[1] - pa[1]) * d[1]) / length,
        ]
    if t == "radius":
        return [_round(by_id, r[0]).radius - float(c.value or 0)]
    if t == "length":
        return [math.hypot(*_dir(_line(by_id, r[0]))) - float(c.value or 0)]
    raise SketchRefError(f"unknown constraint type {t}")  # pragma: no cover - Literal-guarded


def check_accepted_constraints(
    elements: Sequence[SketchElement],
    constraints: Sequence[SketchConstraint],
    *,
    relative_tolerance: float = RELATIVE_TOLERANCE,
) -> list[ConstraintCheck]:
    """Check every ``accepted`` constraint on the geometry as given. Deterministic."""
    with tracer.start_as_current_span("design_ir.sketch_check") as span:
        by_id = _elements_by_id(elements)
        scale = max(sketch_extent(elements), 1e-9)
        tol = relative_tolerance * scale
        out: list[ConstraintCheck] = []
        for c in constraints:
            if c.status != "accepted":
                continue
            try:
                worst = max(abs(v) for v in residuals(c, by_id, scale))
            except SketchRefError as exc:
                out.append(ConstraintCheck(c.id, c.type, math.inf, False, str(exc)))
                continue
            holds = worst <= tol
            out.append(
                ConstraintCheck(
                    c.id,
                    c.type,
                    worst,
                    holds,
                    "" if holds else f"off by {worst:.3g} (tolerance {tol:.3g})",
                )
            )
        failing = sum(1 for ch in out if not ch.holds)
        span.set_attribute("sketch.accepted_count", len(out))
        span.set_attribute("sketch.failing_count", failing)
        if failing:
            logger.info("sketch_constraints_do_not_hold", accepted=len(out), failing=failing)
        return out
