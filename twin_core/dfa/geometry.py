"""Placements and a simple solid engine for the assembly checks.

A placement is a rigid transform, kept as a 3 x 4 row-major matrix
``((r00, r01, r02, tx), (r10, r11, r12, ty), (r20, r21, r22, tz))``. It is
built from the same ``x, y, z, rx, ry, rz`` location ``cadquery.create_assembly``
takes (rotations in degrees about the part's own origin, X then Y then Z,
then the translation), so a placement recorded on an assembly means the same
thing here and in the CAD adapter.

:class:`SimpleSolidEngine` answers the questions the checks ask of solids
(how much do two bodies overlap, how far apart are they) for bodies given as
boxes in their own frame. It is exact for boxes that stay parallel to each
other and sampled otherwise. It serves bodies with no STEP and the unit
tests; real parts are measured by the CAD adapter
(``cadquery.check_interference``) behind the same :class:`GeometryEngine`
protocol.

Pure: no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

Vec = tuple[float, float, float]
Matrix = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]

IDENTITY: Matrix = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))

#: Overlap at or below this is touching, not interference (coincident faces).
DEFAULT_TOLERANCE_MM3 = 1.0e-3
_EPS = 1.0e-9


def _vec(raw: Any, what: str) -> Vec:
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise ValueError(f"{what} must be [x, y, z]")
    try:
        x, y, z = (float(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be three numbers") from exc
    return (x, y, z)


def as_vec(raw: Any, what: str) -> Vec:
    """``[x, y, z]`` as a vector; raises ``ValueError`` naming ``what`` otherwise."""
    return _vec(raw, what)


def _rot_x(deg: float) -> Matrix:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((1.0, 0.0, 0.0, 0.0), (0.0, c, -s, 0.0), (0.0, s, c, 0.0))


def _rot_y(deg: float) -> Matrix:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, 0.0, s, 0.0), (0.0, 1.0, 0.0, 0.0), (-s, 0.0, c, 0.0))


def _rot_z(deg: float) -> Matrix:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, -s, 0.0, 0.0), (s, c, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))


def compose(a: Matrix, b: Matrix) -> Matrix:
    """``a`` after ``b``: a point goes through ``b`` first."""
    rows = []
    for i in range(3):
        row = [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
        row.append(sum(a[i][k] * b[k][3] for k in range(3)) + a[i][3])
        rows.append((row[0], row[1], row[2], row[3]))
    return (rows[0], rows[1], rows[2])


def translation(v: Vec) -> Matrix:
    return ((1.0, 0.0, 0.0, v[0]), (0.0, 1.0, 0.0, v[1]), (0.0, 0.0, 1.0, v[2]))


def location_matrix(loc: dict[str, Any] | None) -> Matrix:
    """The placement of a ``{x, y, z, rx, ry, rz}`` location (mm, degrees)."""
    loc = loc or {}
    for key in loc:
        if key not in ("x", "y", "z", "rx", "ry", "rz"):
            raise ValueError(f"location key {key!r} is not one of x, y, z, rx, ry, rz")
    try:
        vals = {k: float(loc.get(k, 0.0) or 0.0) for k in ("x", "y", "z", "rx", "ry", "rz")}
    except (TypeError, ValueError) as exc:
        raise ValueError("location values must be numbers") from exc
    rotation = compose(_rot_z(vals["rz"]), compose(_rot_y(vals["ry"]), _rot_x(vals["rx"])))
    return compose(translation((vals["x"], vals["y"], vals["z"])), rotation)


def matrix_from(raw: Any) -> Matrix:
    """A placement from 12 numbers (3 x 4 row-major) or three rows of four."""
    flat: list[float]
    if (
        isinstance(raw, list | tuple)
        and len(raw) == 3
        and all(isinstance(r, list | tuple) for r in raw)
    ):
        flat = [float(v) for r in raw for v in r]
    elif isinstance(raw, list | tuple):
        flat = [float(v) for v in raw]
    else:
        raise ValueError("matrix must be 12 numbers (3 x 4, row-major)")
    if len(flat) != 12:
        raise ValueError("matrix must be 12 numbers (3 x 4, row-major)")
    return (
        (flat[0], flat[1], flat[2], flat[3]),
        (flat[4], flat[5], flat[6], flat[7]),
        (flat[8], flat[9], flat[10], flat[11]),
    )


def flat(m: Matrix) -> list[float]:
    """The 12 numbers of a placement, row-major, rounded for storage."""
    return [round(v, 9) for row in m for v in row]


def axis_rotation(point: Vec, direction: Vec, angle_deg: float) -> Matrix:
    """Rotation by ``angle_deg`` about the axis through ``point`` along ``direction``."""
    n = math.sqrt(sum(c * c for c in direction))
    if n < _EPS:
        raise ValueError("a joint axis direction must not be zero")
    ux, uy, uz = (c / n for c in direction)
    a = math.radians(angle_deg)
    c, s, t = math.cos(a), math.sin(a), 1.0 - math.cos(a)
    rot: Matrix = (
        (t * ux * ux + c, t * ux * uy - s * uz, t * ux * uz + s * uy, 0.0),
        (t * ux * uy + s * uz, t * uy * uy + c, t * uy * uz - s * ux, 0.0),
        (t * ux * uz - s * uy, t * uy * uz + s * ux, t * uz * uz + c, 0.0),
    )
    neg: Vec = (-point[0], -point[1], -point[2])
    return compose(translation(point), compose(rot, translation(neg)))


def apply(m: Matrix, p: Vec) -> Vec:
    return (
        m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
        m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
        m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3],
    )


def apply_dir(m: Matrix, v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec) -> Vec:
    n = norm(a)
    if n < _EPS:
        raise ValueError("a direction must not be zero")
    return (a[0] / n, a[1] / n, a[2] / n)


# ---------------------------------------------------------------------------
# Bodies and the engine protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    """A box in a body's own frame: its minimum corner and its size (mm)."""

    min: Vec
    size: Vec

    @property
    def volume(self) -> float:
        return self.size[0] * self.size[1] * self.size[2]


@dataclass
class Body:
    """One placed body of an assembly."""

    #: Unique in the assembly: the part's name there.
    name: str
    #: The revision it is, ``KEY@n``.
    ref: str
    matrix: Matrix = IDENTITY
    #: The body's solid as a box in its own frame (simple engine).
    box: Box | None = None
    #: The body's STEP file on the adapter workspace (CAD engine).
    file: str | None = None


@dataclass(frozen=True)
class Overlap:
    a: str
    b: str
    volume_mm3: float


@dataclass
class EngineResult:
    """What the engine measured."""

    #: Overlapping pairs at the assembled placements.
    overlaps: list[Overlap] = field(default_factory=list)
    #: Minimum gap per requested pair, ``(a, b) -> mm`` (0 when they touch or overlap).
    distances: dict[tuple[str, str], float] = field(default_factory=dict)
    #: Overlapping pairs per extra pose, by pose id.
    poses: dict[str, list[Overlap]] = field(default_factory=dict)


@dataclass(frozen=True)
class Pose:
    """The assembly with some bodies moved (one sample of a joint's range)."""

    id: str
    #: Body name -> its placement in this pose.
    matrices: dict[str, Matrix]
    #: Only these pairs are checked in this pose (empty: every pair).
    pairs: tuple[tuple[str, str], ...] = ()


class GeometryEngine(Protocol):
    """Measures overlaps and gaps between placed bodies."""

    async def measure(
        self,
        bodies: Sequence[Body],
        *,
        tolerance_mm3: float,
        distance_pairs: Sequence[tuple[str, str]] = (),
        poses: Sequence[Pose] = (),
    ) -> EngineResult: ...


# ---------------------------------------------------------------------------
# The simple solid engine: boxes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Obb:
    centre: Vec
    axes: tuple[Vec, Vec, Vec]
    half: Vec

    def contains(self, p: Vec, eps: float = 1e-9) -> bool:
        d = sub(p, self.centre)
        return all(abs(dot(d, self.axes[i])) <= self.half[i] + eps for i in range(3))

    def distance_to(self, p: Vec) -> float:
        d = sub(p, self.centre)
        total = 0.0
        for i in range(3):
            excess = abs(dot(d, self.axes[i])) - self.half[i]
            if excess > 0:
                total += excess * excess
        return math.sqrt(total)

    def point(self, f: Vec) -> Vec:
        """The point at box coordinates ``f`` (each -1 to 1 along an axis)."""
        c, h, a = self.centre, self.half, self.axes
        return (
            c[0] + f[0] * h[0] * a[0][0] + f[1] * h[1] * a[1][0] + f[2] * h[2] * a[2][0],
            c[1] + f[0] * h[0] * a[0][1] + f[1] * h[1] * a[1][1] + f[2] * h[2] * a[2][1],
            c[2] + f[0] * h[0] * a[0][2] + f[1] * h[1] * a[1][2] + f[2] * h[2] * a[2][2],
        )

    def corners(self) -> list[Vec]:
        return [
            self.point((float(sx), float(sy), float(sz)))
            for sx in (-1, 1)
            for sy in (-1, 1)
            for sz in (-1, 1)
        ]


def _obb(box: Box, m: Matrix) -> _Obb:
    centre_local: Vec = (
        box.min[0] + box.size[0] / 2,
        box.min[1] + box.size[1] / 2,
        box.min[2] + box.size[2] / 2,
    )
    axes = (
        unit(apply_dir(m, (1.0, 0.0, 0.0))),
        unit(apply_dir(m, (0.0, 1.0, 0.0))),
        unit(apply_dir(m, (0.0, 0.0, 1.0))),
    )
    return _Obb(apply(m, centre_local), axes, (box.size[0] / 2, box.size[1] / 2, box.size[2] / 2))


def _aligned_extents(o: _Obb) -> tuple[Vec, Vec] | None:
    """World min/max when the box's axes are the world axes (in any order)."""
    lo = [0.0, 0.0, 0.0]
    hi = [0.0, 0.0, 0.0]
    used = set()
    ext = [0.0, 0.0, 0.0]
    for i, axis in enumerate(o.axes):
        k = max(range(3), key=lambda j: abs(axis[j]))
        if abs(abs(axis[k]) - 1.0) > 1e-9 or k in used:
            return None
        used.add(k)
        ext[k] = o.half[i]
    for k in range(3):
        lo[k] = o.centre[k] - ext[k]
        hi[k] = o.centre[k] + ext[k]
    return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


def _separated(a: _Obb, b: _Obb) -> bool:
    """Separating axis test for two boxes."""
    axes: list[Vec] = list(a.axes) + list(b.axes)
    for u in a.axes:
        for v in b.axes:
            cross = (
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            )
            if norm(cross) > 1e-9:
                axes.append(unit(cross))
    d = sub(b.centre, a.centre)
    for axis in axes:
        ra = sum(a.half[i] * abs(dot(a.axes[i], axis)) for i in range(3))
        rb = sum(b.half[i] * abs(dot(b.axes[i], axis)) for i in range(3))
        if abs(dot(d, axis)) > ra + rb + 1e-12:
            return True
    return False


def _samples(o: _Obb, n: int) -> Iterable[Vec]:
    for i in range(n):
        for j in range(n):
            for k in range(n):
                yield o.point(((i + 0.5) / n * 2 - 1, (j + 0.5) / n * 2 - 1, (k + 0.5) / n * 2 - 1))


def _surface(o: _Obb, n: int) -> Iterable[Vec]:
    yield from o.corners()
    for face in range(3):
        others = [a for a in range(3) if a != face]
        for side in (-1.0, 1.0):
            for i in range(n + 1):
                for j in range(n + 1):
                    f = [0.0, 0.0, 0.0]
                    f[face] = side
                    f[others[0]] = i / n * 2 - 1
                    f[others[1]] = j / n * 2 - 1
                    yield o.point((f[0], f[1], f[2]))


def box_overlap_volume(a: Box, ma: Matrix, b: Box, mb: Matrix, samples: int = 24) -> float:
    """The volume two placed boxes share (mm3): exact when parallel, sampled otherwise."""
    oa, ob = _obb(a, ma), _obb(b, mb)
    if _separated(oa, ob):
        return 0.0
    ea, eb = _aligned_extents(oa), _aligned_extents(ob)
    if ea is not None and eb is not None:
        vol = 1.0
        for k in range(3):
            span = min(ea[1][k], eb[1][k]) - max(ea[0][k], eb[0][k])
            if span <= 0:
                return 0.0
            vol *= span
        return vol
    inside = sum(1 for p in _samples(oa, samples) if ob.contains(p))
    return a.volume * inside / samples**3


def box_distance(a: Box, ma: Matrix, b: Box, mb: Matrix, samples: int = 12) -> float:
    """The smallest gap between two placed boxes (mm); 0 when they touch or overlap."""
    oa, ob = _obb(a, ma), _obb(b, mb)
    if not _separated(oa, ob):
        return 0.0
    ea, eb = _aligned_extents(oa), _aligned_extents(ob)
    if ea is not None and eb is not None:
        gap = 0.0
        for k in range(3):
            g = max(0.0, ea[0][k] - eb[1][k], eb[0][k] - ea[1][k])
            gap += g * g
        return math.sqrt(gap)
    best = min(ob.distance_to(p) for p in _surface(oa, samples))
    return min(best, min(oa.distance_to(p) for p in _surface(ob, samples)))


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _overlaps(
    bodies: Sequence[Body],
    matrices: dict[str, Matrix],
    tolerance: float,
    pairs: Sequence[tuple[str, str]] = (),
) -> list[Overlap]:
    wanted = {_key(a, b) for a, b in pairs}
    out: list[Overlap] = []
    for i, ba in enumerate(bodies):
        for bb in bodies[i + 1 :]:
            if wanted and _key(ba.name, bb.name) not in wanted:
                continue
            if ba.box is None or bb.box is None:
                continue
            vol = box_overlap_volume(ba.box, matrices[ba.name], bb.box, matrices[bb.name])
            if vol > tolerance:
                out.append(Overlap(ba.name, bb.name, round(vol, 4)))
    return out


class SimpleSolidEngine:
    """The :class:`GeometryEngine` for bodies given as boxes."""

    async def measure(
        self,
        bodies: Sequence[Body],
        *,
        tolerance_mm3: float,
        distance_pairs: Sequence[tuple[str, str]] = (),
        poses: Sequence[Pose] = (),
    ) -> EngineResult:
        missing = [b.name for b in bodies if b.box is None]
        if missing:
            raise ValueError(f"no simple solid for {', '.join(missing)}")
        base = {b.name: b.matrix for b in bodies}
        by_name = {b.name: b for b in bodies}
        result = EngineResult(overlaps=_overlaps(bodies, base, tolerance_mm3))
        for a, b in distance_pairs:
            ba, bb = by_name.get(a), by_name.get(b)
            if ba is None or bb is None or ba.box is None or bb.box is None:
                continue
            result.distances[_key(a, b)] = round(
                box_distance(ba.box, ba.matrix, bb.box, bb.matrix), 4
            )
        for pose in poses:
            matrices = {**base, **pose.matrices}
            result.poses[pose.id] = _overlaps(bodies, matrices, tolerance_mm3, pose.pairs)
        return result


__all__ = [
    "DEFAULT_TOLERANCE_MM3",
    "IDENTITY",
    "Body",
    "Box",
    "EngineResult",
    "GeometryEngine",
    "Matrix",
    "Overlap",
    "Pose",
    "SimpleSolidEngine",
    "Vec",
    "apply",
    "apply_dir",
    "as_vec",
    "axis_rotation",
    "box_distance",
    "box_overlap_volume",
    "compose",
    "dot",
    "flat",
    "location_matrix",
    "matrix_from",
    "norm",
    "sub",
    "translation",
    "unit",
]
