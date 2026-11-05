"""Exact FDM manufacturability measures of a validated B-Rep (C3D slice 6).

:func:`dfm_measure` reads a STEP (the same OCCT reader ``validate_shape``
and ``describe_faces`` use) and returns **measured values with face
anchors**; it judges nothing. The gateway compares them against the
versioned FDM rule profile (``twin_core.dfm.exact``), so a limit never lives
in the adapter.

What is measured:

* **Wall thickness, by ray casting along inward normals on a bounded
  sample.** Each face is sampled on an ``n x n`` grid of its parameter range
  (only points inside the trimmed face count). At each point a ray starts a
  hair inside the material and runs along the inward normal; the first exit
  through the solid is the local through-thickness. ``n`` shrinks so the
  whole part stays under ``max_wall_samples`` rays.

  Limits, stated in the result: only sampled points are measured, so a thin
  region smaller than the grid spacing can be missed; the thickness is along
  the surface normal, so a tapered wall reads its normal thickness, not the
  thinnest section in another direction; a ray that leaves through an
  adjacent face near an edge reads that distance; points on a face whose
  normal does not point out of the solid (a defect the validity check would
  already report) find no exit and are skipped.
* **Faces for the overhang check.** Every face with its outward normal
  (planar faces) or its most downward sampled normal (curved faces), its
  area, and whether it rests on the build plate (a downward face at the
  lowest extent along the build direction, which needs no support).
* **Build extents** along the build direction and across it, from the
  shape rotated so the build direction is +Z.
* **Cylindrical faces**: radius, whether the material is outside (a hole) or
  inside (a pin or boss), and whether the cylinder is complete (fillets and
  partial arcs are not holes).
* **Planar face records** from ``faces.py`` (same ``s<solid>f<face>`` ids,
  the converter's mesh ranges), so the gateway can sign each anchored face
  and the viewer can highlight it.

The pure helpers (:func:`sample_fractions`, :func:`samples_per_face`,
:func:`group_cylinders`, :func:`downward_tilt_deg`) are tested without a
CAD kernel.
"""

from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cadquery.dfm_measure")

KERNEL = "OCCT via CadQuery"
MAX_FACES = 2000
#: The default bound on wall-thickness rays for the whole part.
DEFAULT_MAX_WALL_SAMPLES = 1500
#: Grid points per face side before the budget shrinks it.
DEFAULT_SAMPLES_PER_SIDE = 5
#: How far inside the surface a ray starts (mm).
RAY_START_MM = 1e-4
#: How many of the thinnest samples are returned.
THINNEST_KEPT = 12
#: A cylinder whose angular span reaches this share of a full turn is complete.
FULL_TURN_SHARE = 0.98

WALL_METHOD = (
    "ray cast along the inward surface normal from points sampled on a grid over each "
    "face; the first exit through the solid is the local through-thickness"
)
WALL_LIMITS = [
    "only sampled points are measured; a thin region smaller than the grid spacing can be missed",
    "thickness is measured along the surface normal, not the thinnest section in any direction",
    "near an edge a ray can leave through the adjacent face and read that distance",
    "points whose normal does not point out of the solid find no exit and are skipped",
]
HOLE_LIMITS = [
    "holes and pins are read from complete cylindrical faces only; slots, polygonal holes "
    "and partial arcs (fillets) are not counted",
]

Vec3 = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _unit(v: Vec3) -> Vec3:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length <= 1e-12:
        raise ValueError("a direction needs a non-zero length")
    return (v[0] / length, v[1] / length, v[2] / length)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _r(v: float, places: int = 6) -> float:
    out = round(float(v), places)
    return 0.0 if out == 0 else out


def sample_fractions(n: int) -> list[float]:
    """Cell-centred fractions of a parameter range: ``n`` points, none on a boundary."""
    if n < 1:
        raise ValueError("at least one sample per side")
    return [(i + 0.5) / n for i in range(n)]


def samples_per_face(faces: int, per_side: int, budget: int) -> int:
    """Grid points per face side so ``faces * side**2`` stays within ``budget`` (at least 1)."""
    if faces <= 0:
        return per_side
    side = per_side
    while side > 1 and faces * side * side > budget:
        side -= 1
    return side


def downward_tilt_deg(normal: Vec3, build: Vec3) -> float:
    """Tilt from vertical of a face whose outward normal is ``normal`` (0 to 90)."""
    n = _unit(normal)
    b = _unit(build)
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, abs(_dot(n, b))))))


def _canonical_axis(d: Vec3) -> Vec3:
    u = _unit(d)
    biggest = max(range(3), key=lambda i: (abs(u[i]), -i))
    return u if u[biggest] >= 0 else (-u[0], -u[1], -u[2])


def _axis_foot(point: Vec3, direction: Vec3) -> Vec3:
    """The point of the axis line closest to the origin (the line's own key)."""
    d = _canonical_axis(direction)
    t = _dot(point, d)
    return (point[0] - t * d[0], point[1] - t * d[1], point[2] - t * d[2])


def group_cylinders(cylinders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge cylindrical faces of one cylinder (a seam split) and mark complete ones.

    Each input: ``{face_id, radius, axis_point, axis_direction, kind, span_rad,
    signature, centre}``. Faces with the same radius, axis line and kind are
    one cylinder; it is ``complete`` when their spans add up to a full turn.
    """
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for c in cylinders:
        d = _canonical_axis(tuple(c["axis_direction"]))  # type: ignore[arg-type]
        foot = _axis_foot(tuple(c["axis_point"]), d)  # type: ignore[arg-type]
        key = (
            round(float(c["radius"]), 4),
            tuple(round(x, 3) for x in d),
            tuple(round(x, 3) for x in foot),
            c["kind"],
        )
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "face_ids": [],
                "radius": _r(c["radius"]),
                "diameter_mm": _r(2.0 * float(c["radius"])),
                "kind": c["kind"],
                "axis_direction": [_r(x) for x in d],
                "axis_point": [_r(x) for x in foot],
                "span_rad": 0.0,
                "signature": c.get("signature"),
                "centre": c.get("centre"),
            }
        g["face_ids"].append(c["face_id"])
        g["span_rad"] += float(c["span_rad"])
    out = []
    for g in groups.values():
        g["complete"] = g["span_rad"] >= 2.0 * math.pi * FULL_TURN_SHARE
        g["span_rad"] = _r(g["span_rad"])
        out.append(g)
    return sorted(out, key=lambda g: (g["kind"], g["diameter_mm"]))


def _signature(surface: str, *, normal: Vec3 | None, area: float, centre: Vec3) -> str:
    """A compact, person-readable geometry signature for a non-planar face."""
    c = ", ".join(f"{x:.4g}" for x in centre)
    if normal is not None:
        n = ", ".join(f"{x:.3g}" for x in normal)
        return f"{surface} normal ({n}) area {area:.4g} at ({c})"
    return f"{surface} area {area:.4g} at ({c})"


# ---------------------------------------------------------------------------
# Kernel side
# ---------------------------------------------------------------------------


def _surface_kind(face: Any) -> str:
    try:
        return str(face.geomType()).lower()
    except Exception:  # noqa: BLE001 - an unreadable surface is "other"
        return "other"


class _FaceProbe:
    """Point, outward normal and inside test on one face's parameter range."""

    def __init__(self, cq_face: Any) -> None:
        from OCP.BRepGProp import BRepGProp_Face  # type: ignore[import-not-found]
        from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d  # type: ignore[import-not-found]

        self.face = cq_face
        self._prop = BRepGProp_Face(cq_face.wrapped)
        self._classifier = BRepTopAdaptor_FClass2d(cq_face.wrapped, 1e-7)
        self.umin, self.umax, self.vmin, self.vmax = cq_face._uvBounds()

    def at(self, fu: float, fv: float) -> tuple[Vec3, Vec3] | None:
        from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec  # type: ignore[import-not-found]
        from OCP.TopAbs import TopAbs_IN  # type: ignore[import-not-found]

        u = self.umin + fu * (self.umax - self.umin)
        v = self.vmin + fv * (self.vmax - self.vmin)
        if self._classifier.Perform(gp_Pnt2d(u, v)) != TopAbs_IN:
            return None
        p, n = gp_Pnt(), gp_Vec()
        # Oriented by the face's orientation in the solid: outward on a valid solid.
        self._prop.Normal(u, v, p, n)
        mag = n.Magnitude()
        if mag <= 1e-12:
            return None
        return (p.X(), p.Y(), p.Z()), (n.X() / mag, n.Y() / mag, n.Z() / mag)


def _ray_exit(shape: Any, start: Vec3, direction: Vec3, tol: float = 1e-7) -> float | None:
    """Distance from ``start`` along ``direction`` to the first face crossing."""
    from OCP.BRepIntCurveSurface import (  # type: ignore[import-not-found]
        BRepIntCurveSurface_Inter,
    )
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt  # type: ignore[import-not-found]

    inter = BRepIntCurveSurface_Inter()
    inter.Init(shape, gp_Lin(gp_Pnt(*start), gp_Dir(*direction)), tol)
    best: float | None = None
    while inter.More():
        w = float(inter.W())
        if w > 1e-7 and (best is None or w < best):
            best = w
        inter.Next()
    return best


def _cylinder_of(cq_face: Any, probe: _FaceProbe) -> dict[str, Any] | None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface  # type: ignore[import-not-found]

    surf = BRepAdaptor_Surface(cq_face.wrapped)
    cyl = surf.Cylinder()
    axis = cyl.Axis()
    loc, d = axis.Location(), axis.Direction()
    axis_point = (loc.X(), loc.Y(), loc.Z())
    axis_dir = (d.X(), d.Y(), d.Z())
    sample = probe.at(0.5, 0.5) or probe.at(0.25, 0.5) or probe.at(0.75, 0.5)
    if sample is None:
        return None
    p, n = sample
    rel = (p[0] - axis_point[0], p[1] - axis_point[1], p[2] - axis_point[2])
    along = _dot(rel, _unit(axis_dir))
    radial = (
        rel[0] - along * axis_dir[0],
        rel[1] - along * axis_dir[1],
        rel[2] - along * axis_dir[2],
    )
    # The outward normal points at the axis on a hole (material outside the cylinder).
    kind = "hole" if _dot(n, radial) < 0 else "boss"
    return {
        "radius": float(cyl.Radius()),
        "axis_point": axis_point,
        "axis_direction": axis_dir,
        "kind": kind,
        "span_rad": abs(probe.umax - probe.umin),
    }


def exact_box(shape: Any) -> tuple[float, float, float, float, float, float]:
    """``(xmin, ymin, zmin, xmax, ymax, zmax)`` from the B-Rep geometry, never a mesh."""
    from OCP.Bnd import Bnd_Box  # type: ignore[import-not-found]
    from OCP.BRepBndLib import BRepBndLib  # type: ignore[import-not-found]

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (xmin, ymin, zmin, xmax, ymax, zmax)


def _build_frame_box(cq: Any, shape: Any, build: Vec3) -> dict[str, Any]:
    """The bounding box with the build direction turned to +Z."""
    z = (0.0, 0.0, 1.0)
    turned = shape
    d = _dot(build, z)
    if d < 1 - 1e-9:
        if d <= -1 + 1e-9:
            turned = shape.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 180.0)
        else:
            axis = (build[1] * z[2] - build[2] * z[1], build[2] * z[0] - build[0] * z[2], 0.0)
            angle = math.degrees(math.acos(max(-1.0, min(1.0, d))))
            turned = shape.rotate(cq.Vector(0, 0, 0), cq.Vector(*axis), angle)
    x0, y0, z0, x1, y1, z1 = exact_box(turned)
    return {
        "extents_mm": [_r(x1 - x0, 4), _r(y1 - y0, 4), _r(z1 - z0, 4)],
        "height_mm": _r(z1 - z0, 4),
    }


def dfm_measure(
    step_base64: str,
    *,
    work_dir: str,
    build_direction: Vec3 = (0.0, 0.0, 1.0),
    samples_per_side: int = DEFAULT_SAMPLES_PER_SIDE,
    max_wall_samples: int = DEFAULT_MAX_WALL_SAMPLES,
    max_faces: int = MAX_FACES,
) -> dict[str, Any]:
    """Measure a STEP's B-Rep for the exact FDM checks. See the module docstring."""
    from tool_registry.tools.cadquery.faces import DEFAULT_DEFLECTION, _describe_solid
    from tool_registry.tools.cadquery.validity import _decode, _require_cq, step_shape

    content = _decode(step_base64)
    build = _unit(build_direction)
    if samples_per_side < 1 or max_wall_samples < 1:
        raise ValueError("the wall sample bounds must be at least 1")
    started = time.monotonic()
    with tracer.start_as_current_span("cadquery.dfm_measure") as span:
        cq = _require_cq()
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".step", dir=work_dir, delete=False) as handle:
            handle.write(content)
            path = handle.name
        try:
            shape = step_shape(cq, cq.importers.importStep(path))
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass
        if shape is None:
            raise ValueError("the STEP holds no shape")
        solids = list(shape.Solids())
        # Before anything tessellates the shape, so the box is the B-Rep's own.
        bx = exact_box(shape)
        frame = _build_frame_box(cq, shape, build)
        corners = [
            (x, y, z) for x in (bx[0], bx[3]) for y in (bx[1], bx[4]) for z in (bx[2], bx[5])
        ]
        lowest = min(_dot(c, build) for c in corners)
        plate_tol = max(1e-4, 1e-6 * math.dist(bx[:3], bx[3:]))

        all_faces = [(si, fi, f) for si, s in enumerate(solids) for fi, f in enumerate(s.Faces())]
        truncated = len(all_faces) > max_faces
        all_faces = all_faces[:max_faces]
        side = samples_per_face(len(all_faces), samples_per_side, max_wall_samples)
        fractions = sample_fractions(side)

        faces_out: list[dict[str, Any]] = []
        raw_cylinders: list[dict[str, Any]] = []
        wall_samples: list[dict[str, Any]] = []
        per_face_min: dict[str, float] = {}
        skipped = 0
        rays = 0
        for si, fi, face in all_faces:
            face_id = f"s{si}f{fi}"
            kind = _surface_kind(face)
            try:
                probe = _FaceProbe(face)
            except Exception as exc:  # noqa: BLE001 - an unreadable face is skipped
                logger.debug("dfm_measure_face_unreadable", face_id=face_id, error=str(exc))
                continue
            area = float(face.Area())
            c = face.Center()
            centre = (c.x, c.y, c.z)
            planar_normal: Vec3 | None = None
            worst: Vec3 | None = None
            on_plate = kind == "plane"
            points: list[tuple[Vec3, Vec3]] = []
            for fu in fractions:
                for fv in fractions:
                    sample = probe.at(fu, fv)
                    if sample is not None:
                        points.append(sample)
            if not points:
                centre_sample = probe.at(0.5, 0.5)
                if centre_sample is not None:
                    points.append(centre_sample)
            for p, n in points:
                if worst is None or _dot(n, build) < _dot(worst, build):
                    worst = n
                if abs(_dot(p, build) - lowest) > plate_tol:
                    on_plate = False
            if kind == "plane" and points:
                planar_normal = points[0][1]
            if worst is None or _dot(worst, build) > -0.999:
                on_plate = False
            if not points:
                on_plate = False
            for p, n in points:
                rays += 1
                start = (
                    p[0] - n[0] * RAY_START_MM,
                    p[1] - n[1] * RAY_START_MM,
                    p[2] - n[2] * RAY_START_MM,
                )
                try:
                    exit_at = _ray_exit(shape.wrapped, start, (-n[0], -n[1], -n[2]))
                except Exception as exc:  # noqa: BLE001 - a failed ray is a skipped sample
                    logger.debug("dfm_measure_ray_failed", face_id=face_id, error=str(exc))
                    exit_at = None
                if exit_at is None:
                    skipped += 1
                    continue
                thickness = exit_at + RAY_START_MM
                wall_samples.append(
                    {
                        "face_id": face_id,
                        "surface": kind,
                        "thickness_mm": _r(thickness, 5),
                        "point": [_r(x, 5) for x in p],
                    }
                )
                per_face_min[face_id] = min(per_face_min.get(face_id, math.inf), thickness)
            signature = _signature(
                kind,
                normal=tuple(_r(x, 4) for x in planar_normal) if planar_normal else None,  # type: ignore[arg-type]
                area=area,
                centre=centre,
            )
            faces_out.append(
                {
                    "face_id": face_id,
                    "surface": kind,
                    "area": _r(area, 6),
                    "centre": [_r(x, 6) for x in centre],
                    "normal": [_r(x, 9) for x in planar_normal] if planar_normal else None,
                    "most_downward_normal": [_r(x, 9) for x in worst] if worst else None,
                    "on_build_plate": on_plate,
                    "samples": len(points),
                    "signature": signature,
                }
            )
            if kind == "cylinder":
                try:
                    cyl = _cylinder_of(face, probe)
                except Exception as exc:  # noqa: BLE001 - an unreadable cylinder is skipped
                    logger.debug("dfm_measure_cylinder_failed", face_id=face_id, error=str(exc))
                    cyl = None
                if cyl is not None:
                    cyl.update(
                        face_id=face_id,
                        signature=f"cylinder r {cyl['radius']:.4g} "
                        f"{'hole' if cyl['kind'] == 'hole' else 'pin'} at "
                        + "("
                        + ", ".join(f"{x:.4g}" for x in centre)
                        + ")",
                        centre=[_r(x, 6) for x in centre],
                    )
                    raw_cylinders.append(cyl)

        planar: list[dict[str, Any]] = []
        for index, solid in enumerate(solids):
            try:
                planar.extend(
                    _describe_solid(cq, solid, index, DEFAULT_DEFLECTION, max_faces - len(planar))
                )
            except Exception as exc:  # noqa: BLE001 - anchors fall back to the face table
                logger.info("dfm_measure_planar_faces_failed", solid=index, error=str(exc))

        cylinders = group_cylinders(raw_cylinders)
        complete = [c for c in cylinders if c["complete"]]
        holes = [c for c in complete if c["kind"] == "hole"]
        bosses = [c for c in complete if c["kind"] == "boss"]
        wall_samples.sort(key=lambda s: s["thickness_mm"])
        measured = bool(wall_samples) and bool(solids)
        wall: dict[str, Any] = {
            "status": "measured" if measured else "not_measured",
            "method": WALL_METHOD,
            "limits": WALL_LIMITS,
            "samples_per_side": side,
            "rays": rays,
            "samples": len(wall_samples),
            "skipped": skipped,
            "min_mm": wall_samples[0]["thickness_mm"] if measured else None,
            "thinnest": wall_samples[:THINNEST_KEPT],
            "per_face_min_mm": {k: _r(v, 5) for k, v in sorted(per_face_min.items())},
        }
        if not solids:
            wall["reason"] = "the STEP holds no solid, so no wall has two sides"
        elif not wall_samples:
            wall["reason"] = "no sampled ray found an exit through the solid"
        duration = round(time.monotonic() - started, 4)
        span.set_attribute("dfm.faces", len(faces_out))
        span.set_attribute("dfm.wall_samples", len(wall_samples))
        span.set_attribute("dfm.duration_s", duration)
        logger.info(
            "cadquery_dfm_measured",
            solids=len(solids),
            faces=len(faces_out),
            wall_samples=len(wall_samples),
            wall_min_mm=wall["min_mm"],
            holes=len(holes),
            bosses=len(bosses),
            duration_s=duration,
        )
        return {
            "build_direction": [_r(x, 9) for x in build],
            "solid_count": len(solids),
            "face_count": len(faces_out),
            "truncated": truncated,
            "bounding_box": dict(
                zip(("min_x", "min_y", "min_z", "max_x", "max_y", "max_z"), bx, strict=True)
            ),
            "build_frame": frame,
            "wall_thickness": wall,
            "faces": faces_out,
            "planar_faces": planar,
            "cylinders": cylinders,
            "holes": holes,
            "bosses": bosses,
            "min_hole_diameter_mm": min((h["diameter_mm"] for h in holes), default=None),
            "min_boss_diameter_mm": min((b["diameter_mm"] for b in bosses), default=None),
            "hole_limits": HOLE_LIMITS,
            "kernel": KERNEL,
            "duration_s": duration,
        }


__all__ = [
    "DEFAULT_MAX_WALL_SAMPLES",
    "DEFAULT_SAMPLES_PER_SIDE",
    "HOLE_LIMITS",
    "WALL_LIMITS",
    "WALL_METHOD",
    "dfm_measure",
    "downward_tilt_deg",
    "exact_box",
    "group_cylinders",
    "sample_fractions",
    "samples_per_face",
]
