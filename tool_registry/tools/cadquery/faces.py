"""Planar faces of an exact B-Rep, for face-attached sketch planes (CDE slice 9).

:func:`describe_faces` reads a STEP (the same OCCT reader
``cadquery.validate_shape`` uses) and lists every planar face with what a
sketch plane needs to sit on it and to find it again on a later revision:

* ``face_id``: stable within this revision only (``s<solid>f<face>``, the
  OCCT explorer order). It is never used on its own to find a face on
  another revision; the geometry signature is (FRS PLN-009).
* ``normal`` (outward, unit), ``offset`` (``normal . p`` for any point on
  the plane), ``area``, ``centroid``, ``bounding_box``.
* ``outer_loop_edges``: the edge count of the outer wire, and
  ``longest_edge``: the longest straight edge of that wire, which fixes the
  plane's U axis.
* ``adjacent``: one summary per neighbouring face (shared edge): its id,
  surface type and, when planar, its normal.
* ``mesh``: where the face's triangles sit in the GLB the OCCT converter
  makes from the same STEP (``part_index``, ``face_index``,
  ``triangle_start``, ``triangle_count``). The triangulation uses the
  converter's own parameters (``BRepMesh_IncrementalMesh(shape, deflection,
  False, 0.5, True)``, per solid, faces in explorer order), so the ranges
  line up with the converter's per-part triangle order. The viewer still
  checks a range against the face's plane before trusting it.

The semantic feature tag (which sketch primitive made a face) is added by
the gateway from the revision's recorded build source; this module knows
only geometry.

The pure helpers (:func:`canonical_direction`, :func:`face_record`) are
tested without a CAD kernel.
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
tracer = get_tracer("tool_registry.tools.cadquery.faces")

#: The OCCT converter's "standard" tessellation deflection (tools/occt-converter).
DEFAULT_DEFLECTION = 0.1
MAX_FACES = 2000
KERNEL = "OCCT via CadQuery"

Vec3 = tuple[float, float, float]


def _round(v: float, places: int = 9) -> float:
    out = round(float(v), places)
    return 0.0 if out == 0 else out


def _vec(v: Any) -> list[float]:
    return [_round(c) for c in v]


def canonical_direction(d: Vec3) -> Vec3:
    """``d`` normalised and signed so its largest component is positive.

    An edge has no preferred direction, so the U axis it gives is made
    canonical: the same edge on a later revision gives the same U.
    """
    length = math.sqrt(sum(c * c for c in d))
    if length <= 0:
        raise ValueError("a direction needs a non-zero length")
    unit = (d[0] / length, d[1] / length, d[2] / length)
    biggest = max(range(3), key=lambda i: (abs(unit[i]), -i))
    if unit[biggest] < 0:
        unit = (-unit[0], -unit[1], -unit[2])
    return unit


def face_record(
    *,
    face_id: str,
    normal: Vec3,
    centroid: Vec3,
    area: float,
    bbox: dict[str, float],
    outer_loop_edges: int,
    longest_edge: dict[str, Any] | None,
    adjacent: list[dict[str, Any]],
    mesh: dict[str, int] | None,
) -> dict[str, Any]:
    """One planar face as the tool returns it. Pure."""
    length = math.sqrt(sum(c * c for c in normal))
    if length <= 0:
        raise ValueError("a planar face needs a normal")
    n = (normal[0] / length, normal[1] / length, normal[2] / length)
    offset = n[0] * centroid[0] + n[1] * centroid[1] + n[2] * centroid[2]
    return {
        "face_id": face_id,
        "surface": "plane",
        "normal": _vec(n),
        "offset": _round(offset),
        "area": _round(area, 6),
        "centroid": _vec(centroid),
        "bounding_box": {k: _round(v) for k, v in bbox.items()},
        "outer_loop_edges": int(outer_loop_edges),
        "longest_edge": longest_edge,
        "adjacent": adjacent,
        "mesh": mesh,
    }


# ---------------------------------------------------------------------------
# Kernel side
# ---------------------------------------------------------------------------


def _geom_type(face: Any) -> str:
    try:
        return str(face.geomType()).lower()
    except Exception:  # noqa: BLE001 - an unreadable surface is "other"
        return "other"


def _bbox(shape: Any) -> dict[str, float]:
    bb = shape.BoundingBox()
    return {
        "min_x": bb.xmin,
        "min_y": bb.ymin,
        "min_z": bb.zmin,
        "max_x": bb.xmax,
        "max_y": bb.ymax,
        "max_z": bb.zmax,
    }


def _longest_line(face: Any) -> tuple[int, dict[str, Any] | None]:
    """``(edge count, longest straight edge)`` of the face's outer wire."""
    wire = face.outerWire()
    edges = list(wire.Edges())
    best: dict[str, Any] | None = None
    for edge in edges:
        if str(edge.geomType()).upper() != "LINE":
            continue
        start, end = edge.startPoint(), edge.endPoint()
        length = float(edge.Length())
        if best is None or length > best["length"] + 1e-9:
            best = {
                "start": _vec((start.x, start.y, start.z)),
                "end": _vec((end.x, end.y, end.z)),
                "length": _round(length, 6),
                "direction": _vec(
                    canonical_direction((end.x - start.x, end.y - start.y, end.z - start.z))
                ),
            }
    return len(edges), best


def _describe_solid(
    cq: Any, solid: Any, solid_index: int, deflection: float, budget: int
) -> list[dict[str, Any]]:
    from OCP.BRep import BRep_Tool  # type: ignore[import-not-found]
    from OCP.BRepMesh import BRepMesh_IncrementalMesh  # type: ignore[import-not-found]
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE  # type: ignore[import-not-found]
    from OCP.TopExp import TopExp, TopExp_Explorer  # type: ignore[import-not-found]
    from OCP.TopLoc import TopLoc_Location  # type: ignore[import-not-found]
    from OCP.TopoDS import TopoDS  # type: ignore[import-not-found]
    from OCP.TopTools import (  # type: ignore[import-not-found]
        TopTools_IndexedDataMapOfShapeListOfShape,
    )

    wrapped = solid.wrapped
    # The converter's own call, so triangle counts line up with its GLB.
    BRepMesh_IncrementalMesh(wrapped, deflection, False, 0.5, True)

    faces: list[Any] = []
    explorer = TopExp_Explorer(wrapped, TopAbs_FACE)
    while explorer.More():
        faces.append(TopoDS.Face_s(explorer.Current()))
        explorer.Next()

    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(wrapped, TopAbs_EDGE, TopAbs_FACE, edge_faces)

    def explorer_index(shape: Any) -> int | None:
        for i, f in enumerate(faces):
            if f.IsSame(shape):
                return i
        return None

    wrapped_faces = [cq.Face(f) for f in faces]
    types = [_geom_type(f) for f in wrapped_faces]
    normals: list[list[float] | None] = []
    for f, kind in zip(wrapped_faces, types, strict=True):
        if kind == "plane":
            n = f.normalAt(f.Center())
            normals.append(_vec(canonical_unit((n.x, n.y, n.z))))
        else:
            normals.append(None)

    out: list[dict[str, Any]] = []
    triangle_start = 0
    for i, (face, cq_face) in enumerate(zip(faces, wrapped_faces, strict=True)):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        count = int(tri.NbTriangles()) if tri is not None else 0
        start = triangle_start
        triangle_start += count
        if types[i] != "plane" or len(out) >= budget:
            continue
        neighbours: dict[int, dict[str, Any]] = {}
        edge_explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while edge_explorer.More():
            edge = edge_explorer.Current()
            idx = edge_faces.FindIndex(edge)
            if idx:
                for other in edge_faces.FindFromIndex(idx):
                    if other.IsSame(face):
                        continue
                    j = explorer_index(other)
                    if j is None or j in neighbours:
                        continue
                    neighbours[j] = {
                        "face_id": f"s{solid_index}f{j}",
                        "surface": types[j],
                        "normal": normals[j],
                    }
            edge_explorer.Next()
        edge_count, longest = _longest_line(cq_face)
        n = normals[i]
        assert n is not None
        c = cq_face.Center()
        out.append(
            face_record(
                face_id=f"s{solid_index}f{i}",
                normal=(n[0], n[1], n[2]),
                centroid=(c.x, c.y, c.z),
                area=float(cq_face.Area()),
                bbox=_bbox(cq_face),
                outer_loop_edges=edge_count,
                longest_edge=longest,
                adjacent=[neighbours[k] for k in sorted(neighbours)],
                mesh={
                    "part_index": solid_index,
                    "face_index": i,
                    "triangle_start": start,
                    "triangle_count": count,
                },
            )
        )
    return out


def canonical_unit(v: Vec3) -> Vec3:
    """``v`` normalised (its sign kept: a normal's sign is its meaning)."""
    length = math.sqrt(sum(c * c for c in v))
    if length <= 0:
        raise ValueError("zero-length vector")
    return (v[0] / length, v[1] / length, v[2] / length)


def describe_faces(
    step_base64: str,
    *,
    work_dir: str,
    deflection: float = DEFAULT_DEFLECTION,
    max_faces: int = MAX_FACES,
) -> dict[str, Any]:
    """The planar faces of a STEP's B-Rep. See the module docstring."""
    from tool_registry.tools.cadquery.validity import _decode, _require_cq, step_shape

    content = _decode(step_base64)
    if not deflection > 0:
        raise ValueError("deflection must be above zero")
    started = time.monotonic()
    with tracer.start_as_current_span("cadquery.describe_faces") as span:
        cq = _require_cq()
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".step", dir=work_dir, delete=False) as handle:
            handle.write(content)
            path = handle.name
        try:
            # step_shape casts compounds to their real leaves, as every STEP writer does.
            shape = step_shape(cq, cq.importers.importStep(path))
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass
        if shape is None:
            raise ValueError("the STEP holds no shape")
        solids = list(shape.Solids()) or [shape]
        faces: list[dict[str, Any]] = []
        total = 0
        for index, solid in enumerate(solids):
            described = _describe_solid(cq, solid, index, deflection, max_faces - len(faces))
            faces.extend(described)
            total += sum(1 for _ in solid.Faces())
        duration = round(time.monotonic() - started, 4)
        span.set_attribute("faces.planar", len(faces))
        span.set_attribute("faces.total", total)
        span.set_attribute("faces.duration_s", duration)
        logger.info(
            "cadquery_faces_described",
            solids=len(solids),
            faces=total,
            planar=len(faces),
            duration_s=duration,
        )
        return {
            "faces": faces,
            "mesh_map": {f["face_id"]: f["mesh"] for f in faces if isinstance(f.get("mesh"), dict)},
            "solid_count": len(solids),
            "face_count": total,
            "planar_face_count": len(faces),
            "truncated": len(faces) >= max_faces,
            "bounding_box": _bbox(shape),
            "deflection": deflection,
            "mesh_source": "occt-converter: per part, faces in explorer order",
            "kernel": KERNEL,
            "duration_s": duration,
        }


__all__ = [
    "DEFAULT_DEFLECTION",
    "canonical_direction",
    "canonical_unit",
    "describe_faces",
    "face_record",
]
