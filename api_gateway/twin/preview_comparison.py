"""Preview versus exact: the revision-bound comparison (C3D slice 5, spec section 7).

When an exact build commits a validated revision from a parametric feature,
the L2 preview of that same definition (``twin_core.design_ir.
feature_definition``) is measured against what the kernel built:

``bounding_box``
    the largest corner difference between the two axis-aligned boxes;
``dimensions``
    the largest difference between the sorted extents (the major dims);
``volume``
    the relative difference, the preview's from its profile and amount and
    the exact one from ``cadquery.validate_shape``;
``surface_distance``
    a bounded, deterministic sample of points on each surface and the
    distance from each to the other surface: the largest of both directions
    is a Hausdorff-style estimate (not a proof: between samples a deviation
    can be larger);
``features``
    the intended through-holes (inner loops of an extruded profile) against
    the genus of the exact tessellation (closed, after welding).

The two mesh checks need the exact tessellation (the same OCCT GLB the
viewer shows); without it they are ``not_evaluated``, never a pass.

**No universal threshold** (section 7). The tolerance is per component: the
parametric sketch can carry ``comparison_tolerance`` (``abs_mm``, ``rel``,
``volume_rel``), else the default below, with its basis written into every
record. Exceeding it is reported as a ``discrepancy`` with the values; it
does not fail the build, unvalidate the revision or say anything about
manufacturability.

The record is stored on the exact revision under ``preview_comparison``.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer
from twin_core.design_ir.feature_definition import (
    FeatureDefinitionError,
    PreviewSolid,
    definition_from_entities,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.preview_comparison")

#: Node metadata key the comparison is stored under, on the exact revision.
COMPARISON_KEY = "preview_comparison"
#: Bounded samples per surface.
SAMPLE_COUNT = 96

#: The default tolerance and why. A display-correspondence tolerance, not a
#: manufacturing tolerance.
DEFAULT_ABS_MM = 0.25
DEFAULT_REL = 0.01
DEFAULT_VOLUME_REL = 0.02
DEFAULT_BASIS = (
    "default: the larger of 0.25 mm and 1% of the part's bounding-box diagonal "
    "(2% for volume). It covers the preview's chord error on arcs (6 degree "
    "segments, under 0.14% of the radius) and the difference between the "
    "preview's Catmull-Rom splines and the kernel's interpolating B-splines. "
    "It says whether the preview showed the exact part faithfully; it is not a "
    "manufacturing tolerance."
)

Vec3 = tuple[float, float, float]
Tri = tuple[Vec3, Vec3, Vec3]

_BBOX_KEYS = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")


@dataclass(frozen=True)
class Tolerance:
    abs_mm: float = DEFAULT_ABS_MM
    rel: float = DEFAULT_REL
    volume_rel: float = DEFAULT_VOLUME_REL
    basis: str = DEFAULT_BASIS
    source: str = "default"

    def linear(self, diagonal: float) -> float:
        return max(self.abs_mm, self.rel * diagonal)

    @classmethod
    def from_config(cls, raw: Any) -> Tolerance:
        """A component's tolerance (``abs_mm``, ``rel``, ``volume_rel``), or the default."""
        if not isinstance(raw, dict) or not raw:
            return cls()

        def num(key: str, default: float) -> float:
            try:
                v = float(raw.get(key, default))
            except (TypeError, ValueError):
                return default
            return v if v >= 0 and math.isfinite(v) else default

        return cls(
            abs_mm=num("abs_mm", DEFAULT_ABS_MM),
            rel=num("rel", DEFAULT_REL),
            volume_rel=num("volume_rel", DEFAULT_VOLUME_REL),
            basis=str(raw.get("basis") or "configured for this component"),
            source="component",
        )


# ---------------------------------------------------------------------------
# The exact tessellation
# ---------------------------------------------------------------------------


_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_FORMATS = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}


def _accessor(doc: dict[str, Any], binary: bytes, index: int) -> list[tuple[float, ...]]:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    comps = _COMPONENTS[acc["type"]]
    fmt = _FORMATS[acc["componentType"]]
    size = struct.calcsize(fmt)
    stride = int(view.get("byteStride") or size * comps)
    base = int(view.get("byteOffset") or 0) + int(acc.get("byteOffset") or 0)
    out: list[tuple[float, ...]] = []
    for i in range(int(acc["count"])):
        out.append(struct.unpack_from(f"<{comps}{fmt}", binary, base + i * stride))
    return out


def read_glb_triangles(glb: bytes) -> list[Tri]:
    """Every triangle a GLB's default scene draws, in world coordinates."""
    from api_gateway.twin.glb_bounds import _apply, _local, _mul, gltf_json

    doc = gltf_json(glb)
    (json_len,) = struct.unpack_from("<I", glb, 12)
    offset = 20 + json_len
    binary = b""
    if offset + 8 <= len(glb):
        bin_len, _kind = struct.unpack_from("<II", glb, offset)
        binary = glb[offset + 8 : offset + 8 + bin_len]
    nodes = doc.get("nodes") or []
    scenes = doc.get("scenes") or []
    roots = (scenes[int(doc.get("scene", 0))].get("nodes") if scenes else None) or list(
        range(len(nodes))
    )
    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    tris: list[Tri] = []
    stack: list[tuple[int, list[float]]] = [(int(r), identity) for r in roots]
    while stack:
        index, parent = stack.pop()
        node = nodes[index]
        local, _ = _local(node)
        world = _mul(parent, local)
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int):
            for prim in doc["meshes"][mesh_index].get("primitives") or []:
                if prim.get("mode", 4) != 4:
                    continue
                pos = [
                    _apply(world, (p[0], p[1], p[2]))
                    for p in _accessor(doc, binary, prim["attributes"]["POSITION"])
                ]
                if prim.get("indices") is not None:
                    idx = [int(i[0]) for i in _accessor(doc, binary, prim["indices"])]
                else:
                    idx = list(range(len(pos)))
                for k in range(0, len(idx) - 2, 3):
                    tris.append((pos[idx[k]], pos[idx[k + 1]], pos[idx[k + 2]]))
        stack.extend((int(c), world) for c in node.get("children") or [])
    return tris


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _area(t: Tri) -> float:
    c = _cross(_sub(t[1], t[0]), _sub(t[2], t[0]))
    return 0.5 * math.sqrt(_dot(c, c))


def mesh_samples(tris: Sequence[Tri], count: int = SAMPLE_COUNT) -> list[Vec3]:
    """``count`` points on the mesh, area-stratified and deterministic."""
    areas = [_area(t) for t in tris]
    total = sum(areas)
    if total <= 0 or count <= 0:
        return []
    out: list[Vec3] = []
    step = total / count
    target = step / 2
    walked = 0.0
    # A fixed barycentric pattern, cycled, so samples spread over a triangle.
    pattern = ((1 / 3, 1 / 3), (0.6, 0.2), (0.2, 0.6), (0.2, 0.2))
    k = 0
    for t, a in zip(tris, areas, strict=True):
        while target <= walked + a and len(out) < count:
            u, v = pattern[k % len(pattern)]
            k += 1
            w = 1 - u - v
            out.append(
                (
                    t[0][0] * w + t[1][0] * u + t[2][0] * v,
                    t[0][1] * w + t[1][1] * u + t[2][1] * v,
                    t[0][2] * w + t[1][2] * u + t[2][2] * v,
                )
            )
            target += step
        walked += a
    return out


def _closest_on_tri(p: Vec3, t: Tri) -> Vec3:
    """Closest point on a triangle (Ericson, Real-Time Collision Detection 5.1.5)."""
    a, b, c = t
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = _sub(p, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return (a[0] + ab[0] * v, a[1] + ab[1] * v, a[2] + ab[2] * v)
    cp = _sub(p, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return (a[0] + ac[0] * w, a[1] + ac[1] * w, a[2] + ac[2] * w)
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return (b[0] + (c[0] - b[0]) * w, b[1] + (c[1] - b[1]) * w, b[2] + (c[2] - b[2]) * w)
    denom = 1.0 / (va + vb + vc)
    v, w = vb * denom, vc * denom
    return (
        a[0] + ab[0] * v + ac[0] * w,
        a[1] + ab[1] * v + ac[1] * w,
        a[2] + ab[2] * v + ac[2] * w,
    )


class MeshDistance:
    """Point-to-mesh distance with a box lower bound to skip far triangles."""

    def __init__(self, tris: Sequence[Tri]) -> None:
        self._tris = list(tris)
        self._boxes = [
            (
                min(t[0][0], t[1][0], t[2][0]),
                min(t[0][1], t[1][1], t[2][1]),
                min(t[0][2], t[1][2], t[2][2]),
                max(t[0][0], t[1][0], t[2][0]),
                max(t[0][1], t[1][1], t[2][1]),
                max(t[0][2], t[1][2], t[2][2]),
            )
            for t in self._tris
        ]

    def __call__(self, p: Vec3) -> float:
        bounds = []
        for i, b in enumerate(self._boxes):
            dx = max(b[0] - p[0], 0.0, p[0] - b[3])
            dy = max(b[1] - p[1], 0.0, p[1] - b[4])
            dz = max(b[2] - p[2], 0.0, p[2] - b[5])
            bounds.append((dx * dx + dy * dy + dz * dz, i))
        bounds.sort()
        best = math.inf
        for lb, i in bounds:
            if lb >= best:
                break
            q = _closest_on_tri(p, self._tris[i])
            d = _sub(p, q)
            best = min(best, _dot(d, d))
        return math.sqrt(best)


def mesh_genus(tris: Sequence[Tri], weld: float) -> tuple[int | None, str]:
    """``(genus, note)`` of a closed triangle mesh after welding vertices within ``weld``.

    A plate with ``n`` through-holes has genus ``n``. ``None`` when the mesh
    is not closed after welding (a boundary edge), so nothing is claimed.
    """
    index: dict[tuple[int, int, int], int] = {}

    def vid(p: Vec3) -> int:
        key = (round(p[0] / weld), round(p[1] / weld), round(p[2] / weld))
        return index.setdefault(key, len(index))

    faces: list[tuple[int, int, int]] = []
    for t in tris:
        a, b, c = vid(t[0]), vid(t[1]), vid(t[2])
        if a != b and b != c and a != c:
            faces.append((a, b, c))
    edges: dict[tuple[int, int], int] = {}
    for a, b, c in faces:
        for e in ((a, b), (b, c), (c, a)):
            k = (min(e), max(e))
            edges[k] = edges.get(k, 0) + 1
    if not faces:
        return None, "the exact tessellation has no triangles"
    open_edges = sum(1 for n in edges.values() if n != 2)
    if open_edges:
        return None, f"the exact tessellation is not closed after welding ({open_edges} open edges)"
    parent = list(range(len(index)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in faces:
        for x, y in ((a, b), (b, c)):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry
    used = {v for f in faces for v in f}
    components = len({find(v) for v in used})
    chi = len(used) - len(edges) + len(faces)
    genus = (2 * components - chi) // 2
    return genus, f"{components} closed shell(s), Euler characteristic {chi}"


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _bbox_list(raw: Any) -> list[float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        return [float(raw[k]) for k in _BBOX_KEYS]
    except (KeyError, TypeError, ValueError):
        return None


def _diag(box: Sequence[float]) -> float:
    return math.sqrt(sum((box[i + 3] - box[i]) ** 2 for i in range(3)))


def _check(check: str, outcome: str, detail: str, **values: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"check": check, "outcome": outcome, "detail": detail}
    out.update({k: (round(v, 6) if isinstance(v, float) else v) for k, v in values.items()})
    return out


def compare_preview_to_exact(
    definition: dict[str, Any],
    *,
    exact_bbox: Any,
    exact_volume: Any,
    exact_triangles: Sequence[Tri] | None,
    tolerance: Tolerance | None = None,
    mesh_note: str | None = None,
    sample_count: int = SAMPLE_COUNT,
) -> dict[str, Any]:
    """Compare the L2 preview of ``definition`` with the exact result. Pure.

    ``outcome`` is ``consistent`` (every check ran and is within tolerance),
    ``discrepancy`` (a check exceeded it; the values say by how much) or
    ``incomplete`` (no check exceeded it, but at least one could not run).
    """
    tol = tolerance or Tolerance()
    solid = PreviewSolid.from_definition(definition)
    pbox_d = solid.bounding_box()
    pbox = [pbox_d[k] for k in _BBOX_KEYS]
    ebox = _bbox_list(exact_bbox)
    diag = _diag(ebox if ebox is not None else pbox)
    lin = tol.linear(diag)
    checks: list[dict[str, Any]] = []

    if ebox is None:
        checks.append(_check("bounding_box", "not_evaluated", "the exact bounding box is missing"))
        checks.append(_check("dimensions", "not_evaluated", "the exact bounding box is missing"))
    else:
        worst = max(abs(a - b) for a, b in zip(pbox, ebox, strict=True))
        checks.append(
            _check(
                "bounding_box",
                "pass" if worst <= lin else "fail",
                f"largest corner difference {worst:.3f} mm (tolerance {lin:.3f} mm)",
                value_mm=worst,
                tolerance_mm=lin,
            )
        )
        p_dims = sorted((pbox[i + 3] - pbox[i] for i in range(3)), reverse=True)
        e_dims = sorted((ebox[i + 3] - ebox[i] for i in range(3)), reverse=True)
        dim_worst = max(abs(a - b) for a, b in zip(p_dims, e_dims, strict=True))
        checks.append(
            _check(
                "dimensions",
                "pass" if dim_worst <= lin else "fail",
                "major dimensions preview "
                + " x ".join(f"{d:.2f}" for d in p_dims)
                + " mm, exact "
                + " x ".join(f"{d:.2f}" for d in e_dims)
                + " mm",
                value_mm=dim_worst,
                tolerance_mm=lin,
                preview_mm=[round(d, 4) for d in p_dims],
                exact_mm=[round(d, 4) for d in e_dims],
            )
        )

    p_vol = solid.volume()
    try:
        e_vol = float(exact_volume) if exact_volume is not None else None
    except (TypeError, ValueError):
        e_vol = None
    if e_vol is None or e_vol <= 0 or p_vol <= 0:
        checks.append(_check("volume", "not_evaluated", "a volume was not measurable on one side"))
    else:
        rel = abs(p_vol - e_vol) / e_vol
        checks.append(
            _check(
                "volume",
                "pass" if rel <= tol.volume_rel else "fail",
                f"preview {p_vol:.2f} mm3, exact {e_vol:.2f} mm3 "
                f"(relative difference {rel:.4f}, tolerance {tol.volume_rel:.4f})",
                preview_mm3=p_vol,
                exact_mm3=e_vol,
                value_rel=rel,
                tolerance_rel=tol.volume_rel,
            )
        )

    tris = list(exact_triangles or [])
    frame_ok = True
    if tris and ebox is not None:
        xs = [c for t in tris for c in (t[0][0], t[1][0], t[2][0])]
        ys = [c for t in tris for c in (t[0][1], t[1][1], t[2][1])]
        zs = [c for t in tris for c in (t[0][2], t[1][2], t[2][2])]
        mbox = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]
        off = max(abs(a - b) for a, b in zip(mbox, ebox, strict=True))
        if off > max(lin, 0.02 * diag):
            frame_ok = False
            mesh_note = (
                f"the exact tessellation's box is {off:.3f} mm from the B-Rep's, so it is "
                "not in the B-Rep's frame"
            )
    if not tris or not frame_ok:
        why = mesh_note or "the exact tessellation was not available"
        checks.append(_check("surface_distance", "not_evaluated", why))
    else:
        exact_dist = MeshDistance(tris)
        p_samples = solid.samples(sample_count)
        e_samples = mesh_samples(tris, sample_count)
        forward = [exact_dist(p) for p in p_samples]
        backward = [solid.distance(p) for p in e_samples]
        both = forward + backward
        haus = max(both) if both else 0.0
        mean = sum(both) / len(both) if both else 0.0
        checks.append(
            _check(
                "surface_distance",
                "pass" if haus <= lin else "fail",
                f"sampled bidirectional distance: largest {haus:.3f} mm, mean {mean:.3f} mm "
                f"over {len(p_samples)} + {len(e_samples)} points (tolerance {lin:.3f} mm)",
                hausdorff_mm=haus,
                mean_mm=mean,
                preview_to_exact_mm=max(forward) if forward else 0.0,
                exact_to_preview_mm=max(backward) if backward else 0.0,
                samples=len(both),
                tolerance_mm=lin,
            )
        )

    intended = solid.through_holes
    if solid.op != "extrude":
        checks.append(
            _check("features", "not_evaluated", "through-holes are compared on extrusions only")
        )
    elif not tris or not frame_ok:
        checks.append(
            _check(
                "features",
                "not_evaluated",
                mesh_note or "the exact tessellation was not available",
                intended_through_holes=intended,
            )
        )
    else:
        genus, note = mesh_genus(tris, weld=max(1e-4, diag * 1e-6))
        if genus is None:
            checks.append(
                _check("features", "not_evaluated", note, intended_through_holes=intended)
            )
        else:
            found = genus
            checks.append(
                _check(
                    "features",
                    "pass" if found == intended else "fail",
                    f"intended through-holes {intended}, found {found} in the exact solid ({note})",
                    intended_through_holes=intended,
                    exact_through_holes=found,
                )
            )

    outcomes = {c["outcome"] for c in checks}
    if "fail" in outcomes:
        outcome = "discrepancy"
    elif "not_evaluated" in outcomes:
        outcome = "incomplete"
    else:
        outcome = "consistent"
    return {
        "outcome": outcome,
        "checks": checks,
        "discrepancies": [c for c in checks if c["outcome"] == "fail"],
        "tolerance": {
            "abs_mm": tol.abs_mm,
            "rel": tol.rel,
            "volume_rel": tol.volume_rel,
            "linear_mm": round(lin, 6),
            "basis": tol.basis,
            "source": tol.source,
        },
        "preview": {
            "op": solid.op,
            "definition_hash": definition.get("definition_hash"),
            "bounding_box": pbox_d,
            "volume_mm3": round(p_vol, 6),
            "intended_through_holes": intended,
        },
        "method": (
            "L2 preview rebuilt from the stored definition with the browser's "
            f"tessellation rules; {sample_count} samples per surface; exact side from "
            "cadquery.validate_shape and the OCCT tessellation"
        ),
        "compared_at": datetime.now(UTC).isoformat(),
    }


def comparison_for_source(
    source: dict[str, Any],
    *,
    validity: dict[str, Any],
    glb: bytes | None,
    mesh_note: str | None = None,
) -> dict[str, Any] | None:
    """The comparison for an exact build's source, or ``None`` when it has no L2 preview.

    A source has an L2 preview when its design IR is one supported preview
    feature (the parametric sketch path). The tolerance is the source's
    ``comparison_tolerance`` (the component's), else the default.
    """
    if source.get("kind") != "design_ir":
        return None
    entities = (source.get("design_ir") or {}).get("entities") or []
    try:
        definition = definition_from_entities(entities)
    except (FeatureDefinitionError, KeyError, TypeError, ValueError):
        return None
    tris: list[Tri] | None = None
    if glb:
        try:
            tris = read_glb_triangles(glb)
        except Exception as exc:  # noqa: BLE001 - a GLB that does not parse is "not available"
            mesh_note = f"the exact tessellation could not be read: {exc}"[:200]
    with tracer.start_as_current_span("twin.preview_comparison.compare") as span:
        record = compare_preview_to_exact(
            definition,
            exact_bbox=validity.get("bounding_box"),
            exact_volume=validity.get("volume_mm3"),
            exact_triangles=tris,
            tolerance=Tolerance.from_config(source.get("comparison_tolerance")),
            mesh_note=mesh_note,
        )
        span.set_attribute("comparison.outcome", record["outcome"])
        span.set_attribute("comparison.discrepancies", len(record["discrepancies"]))
    if source.get("parametric_sketch_ref"):
        record["preview"]["sketch_ref"] = source["parametric_sketch_ref"]
    return record


__all__ = [
    "COMPARISON_KEY",
    "DEFAULT_BASIS",
    "MeshDistance",
    "Tolerance",
    "compare_preview_to_exact",
    "comparison_for_source",
    "mesh_genus",
    "mesh_samples",
    "read_glb_triangles",
]
