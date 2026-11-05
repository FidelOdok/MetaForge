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
    the intended openings against the genus (and, for a revolve, the closed
    shells) of the exact tessellation, closed after welding: an extrusion's
    inner loops are through-holes; a whole-turn revolve clear of its axis has
    a bore and each inner loop is an enclosed ring cavity; a part turn has no
    bore and its inner loops open at both end faces.

The two mesh checks need the exact tessellation (the same OCCT GLB the
viewer shows); without it they are ``not_evaluated``, never a pass.

A **multi-feature part** (a feature tree the L2 preview does not draw) gets
the ``features`` check alone (:func:`compare_features_only`): its openings
are read from the design IR (one pad, pockets parallel to it, edge rounds;
anything else says why it is not predicted), and the shape checks say there
is no preview to measure.

An **assembly** (:func:`assembly_comparison_for_node`, stored under
``assembly_comparison``) is compared component by component, each found by
the component id the converter writes into its GLB node extras:
``component_pose`` and ``component_alignment`` against the structured
layout (L1), and ``interference`` between every pair (bounded to
:data:`MAX_ASSEMBLY_COMPONENTS` parts and sampled), with the smallest
clearance reported but not judged.

**No universal threshold** (section 7). The tolerance is per component: the
parametric sketch can carry ``comparison_tolerance`` (``abs_mm``, ``rel``,
``volume_rel``), else the default below, with its basis written into every
record. Exceeding it is reported as a ``discrepancy`` with the values; it
does not fail the build, unvalidate the revision or say anything about
manufacturability.

The record is stored on the exact revision under ``preview_comparison``.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer
from twin_core.design_ir.feature_definition import (
    FeatureDefinitionError,
    PreviewSolid,
    Profile,
    _frame_vecs,
    definition_from_entities,
    definition_hash,
    frame_of,
    point_in_loop,
    profile_loops,
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
    return [t for part in read_glb_components(glb) for t in part["tris"]]


def read_glb_components(glb: bytes) -> list[dict[str, Any]]:
    """A GLB's triangles grouped by the component that draws them.

    Each mesh belongs to its nearest node (itself or an ancestor) whose
    ``extras`` carry a ``component_id``, else to its own node. Returns
    ``[{key, component_id, name, tris}]`` in first-seen order, world
    coordinates.
    """
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
    groups: dict[str, dict[str, Any]] = {}
    stack: list[tuple[int, list[float], tuple[str, str | None, str] | None]] = [
        (int(r), identity, None) for r in reversed(roots)
    ]
    while stack:
        index, parent, owner = stack.pop()
        node = nodes[index]
        local, _ = _local(node)
        world = _mul(parent, local)
        raw_extras = node.get("extras")
        extras: dict[str, Any] = raw_extras if isinstance(raw_extras, dict) else {}
        cid = extras.get("component_id")
        name = str(node.get("name") or f"node_{index}")
        if isinstance(cid, str) and cid:
            part = extras.get("part_name")
            owner = (f"cid:{cid}", cid, str(part) if isinstance(part, str) and part else name)
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int):
            key, gcid, gname = owner or (f"node:{index}", None, name)
            group = groups.setdefault(
                key, {"key": key, "component_id": gcid, "name": gname, "tris": []}
            )
            tris: list[Tri] = group["tris"]
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
        stack.extend((int(c), world, owner) for c in reversed(node.get("children") or []))
    return list(groups.values())


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


def _closest_on_segment(p: Vec3, a: Vec3, b: Vec3) -> Vec3:
    ab = _sub(b, a)
    length2 = _dot(ab, ab)
    if length2 <= 0:
        return a
    s = min(max(_dot(_sub(p, a), ab) / length2, 0.0), 1.0)
    return (a[0] + ab[0] * s, a[1] + ab[1] * s, a[2] + ab[2] * s)


def _closest_on_tri(p: Vec3, t: Tri) -> Vec3:
    """Closest point on a triangle (Ericson, Real-Time Collision Detection 5.1.5)."""
    a, b, c = t
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    normal = _cross(ab, ac)
    if _dot(normal, normal) <= 1e-18 * max(_dot(ab, ab), _dot(ac, ac), 1e-300) ** 2:
        # A degenerate (zero-area) triangle: the nearest of its three edges.
        return min(
            (
                _closest_on_segment(p, a, b),
                _closest_on_segment(p, b, c),
                _closest_on_segment(p, a, c),
            ),
            key=lambda q: _dot(_sub(p, q), _sub(p, q)),
        )
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
    genus, _shells, note = mesh_topology(tris, weld)
    return genus, note


def mesh_topology(tris: Sequence[Tri], weld: float) -> tuple[int | None, int | None, str]:
    """``(genus, closed shells, note)`` of a triangle mesh after welding within ``weld``.

    The genus is summed over the shells: a ring has 1, a plate with ``n``
    through-holes has ``n``, and a closed internal cavity adds a shell of its
    own. ``(None, None, why)`` when the mesh is not closed after welding.
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
        return None, None, "the exact tessellation has no triangles"
    open_edges = sum(1 for n in edges.values() if n != 2)
    if open_edges:
        return (
            None,
            None,
            f"the exact tessellation is not closed after welding ({open_edges} open edges)",
        )
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
    return genus, components, f"{components} closed shell(s), Euler characteristic {chi}"


def intended_topology(solid: PreviewSolid) -> dict[str, Any]:
    """What the exact solid's topology should be, from the preview feature alone.

    ``{genus, shells, basis}``; ``shells`` is ``None`` where it is not
    predicted. ``{reason}`` when the feature's topology cannot be predicted
    honestly (it is then ``not_evaluated``, never guessed).
    """
    prof = solid.profile
    if prof.islands:
        return {"reason": "the profile has separate regions, so it makes more than one solid"}
    holes = len(prof.holes)
    if solid.op == "extrude":
        if solid.shell_thickness and holes:
            return {"reason": "the topology of a shelled part with holes is not predicted"}
        return {"genus": holes, "shells": None, "basis": "inner loops of the extruded profile"}
    us = [u for u, _v in prof.outer]
    span = max(us) - min(us) if us else 0.0
    eps = max(1e-6, span * 1e-6)
    if min(us) < -eps and max(us) > eps:
        return {"reason": "the revolve profile crosses its axis"}
    if solid.amount >= 360.0 - 1e-9:
        bore = 1 if min(abs(u) for u in us) > eps else 0
        basis = (
            "a whole turn: the bore left where the profile is clear of the axis"
            if bore
            else "a whole turn of a profile that meets the axis (no bore)"
        )
        if holes:
            basis += f", and {holes} inner loop(s), each an enclosed ring-shaped cavity"
        return {"genus": bore + holes, "shells": 1 + holes, "basis": basis}
    basis = f"a part turn ({solid.amount:g} degrees): no bore"
    if holes:
        basis += f"; {holes} inner loop(s), each open at both end faces"
    return {"genus": holes, "shells": 1, "basis": basis}


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


def _outcome(checks: Sequence[dict[str, Any]]) -> str:
    outcomes = {c["outcome"] for c in checks}
    if "fail" in outcomes:
        return "discrepancy"
    if "not_evaluated" in outcomes:
        return "incomplete"
    return "consistent"


def _box_of(tris: Sequence[Tri]) -> list[float]:
    xs = [c for t in tris for c in (t[0][0], t[1][0], t[2][0])]
    ys = [c for t in tris for c in (t[0][1], t[1][1], t[2][1])]
    zs = [c for t in tris for c in (t[0][2], t[1][2], t[2][2])]
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


def _mesh_in_frame(
    tris: Sequence[Tri],
    ebox: Sequence[float] | None,
    lin: float,
    diag: float,
    mesh_note: str | None,
) -> tuple[bool, str | None]:
    """Whether the tessellation sits in the B-Rep's frame (its box matches the B-Rep's)."""
    if not tris or ebox is None:
        return True, mesh_note
    off = max(abs(a - b) for a, b in zip(_box_of(tris), ebox, strict=True))
    if off > max(lin, 0.02 * diag):
        return False, (
            f"the exact tessellation's box is {off:.3f} mm from the B-Rep's, so it is "
            "not in the B-Rep's frame"
        )
    return True, mesh_note


def _features_check(
    topology: dict[str, Any],
    tris: Sequence[Tri],
    *,
    weld: float,
    mesh_note: str | None,
) -> dict[str, Any]:
    """The ``features`` check: the intended openings and shells against the exact mesh."""
    if topology.get("reason"):
        return _check("features", "not_evaluated", str(topology["reason"]))
    intended = int(topology["genus"])
    want_shells = topology.get("shells")
    basis = str(topology.get("basis") or "")
    if not tris:
        return _check(
            "features",
            "not_evaluated",
            mesh_note or "the exact tessellation was not available",
            intended_through_holes=intended,
            basis=basis,
        )
    genus, shells, note = mesh_topology(tris, weld=weld)
    if genus is None:
        return _check(
            "features", "not_evaluated", note, intended_through_holes=intended, basis=basis
        )
    ok = genus == intended and (want_shells is None or shells == want_shells)
    detail = f"intended through-holes {intended}, found {genus} in the exact solid ({note})"
    values: dict[str, Any] = {
        "intended_through_holes": intended,
        "exact_through_holes": genus,
        "basis": basis,
    }
    if want_shells is not None:
        detail += f"; intended closed shells {want_shells}, found {shells}"
        values.update(intended_shells=want_shells, exact_shells=shells)
    return _check("features", "pass" if ok else "fail", detail, **values)


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
    frame_ok, mesh_note = _mesh_in_frame(tris, ebox, lin, diag, mesh_note)
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
    topology = intended_topology(solid)
    checks.append(
        _features_check(
            topology,
            tris if frame_ok else [],
            weld=max(1e-4, diag * 1e-6),
            mesh_note=mesh_note,
        )
    )

    return {
        "outcome": _outcome(checks),
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
            "intended_topology": topology,
        },
        "method": (
            "L2 preview rebuilt from the stored definition with the browser's "
            f"tessellation rules; {sample_count} samples per surface; exact side from "
            "cadquery.validate_shape and the OCCT tessellation"
        ),
        "compared_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Multi-feature parts: intended openings from the design IR
# ---------------------------------------------------------------------------

#: Ops that leave a solid's openings as they were (rounded or bevelled edges).
_OPENING_NEUTRAL_OPS = frozenset(
    {"create_body", "fillet_edges", "chamfer_edges", "fillet", "chamfer"}
)
NO_PREVIEW_NOTE = "a multi-feature part has no L2 preview to measure against"


def _loops_of(sketch: dict[str, Any]) -> Profile:
    elements = [
        e
        for e in (sketch.get("elements") or [])
        if isinstance(e, dict) and not e.get("construction")
    ]
    return profile_loops(elements)


def ir_intended_topology(entities: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The openings a multi-feature part should have, read from its design IR.

    Bounded to what can be predicted without a kernel: one padded body, then
    pockets parallel to the pad and edge fillets or chamfers. A pocket loop
    wholly inside the padded profile that cuts the pad's whole thickness is a
    through-hole; one that cuts part of it is blind; one that crosses the
    profile's edge is a notch. Anything else (another pad, a revolve, a loft,
    a shell, a boolean, overlapping cuts) is ``{reason}``, never guessed.
    """
    ents = [e for e in entities if isinstance(e, dict)]
    by_id = {str(e.get("id")): e for e in ents}
    others = sorted(
        {
            str(e.get("op"))
            for e in ents
            if e.get("op") not in _OPENING_NEUTRAL_OPS | {"sketch", "pad", "pocket"}
        }
    )
    if others:
        return {"reason": f"the openings of a part made with {', '.join(others)} are not predicted"}
    pads = [e for e in ents if e.get("op") == "pad"]
    if len(pads) != 1:
        return {"reason": "openings are predicted only for a part padded once"}
    pad = pads[0]
    try:
        sketch = by_id[str(pad["sketch_ref"])]
        profile = _loops_of(sketch)
        origin, pad_x, pad_y, normal = _frame_vecs(frame_of(sketch))
        depth = float(pad["depth"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"reason": f"the padded profile could not be read: {exc}"[:200]}
    if profile.islands or profile.open_chains:
        return {"reason": "the padded profile has separate regions or open chains"}
    if pad.get("midplane"):
        lo, hi = -depth / 2, depth / 2
    elif pad.get("reversed"):
        lo, hi = -depth, 0.0
    else:
        lo, hi = 0.0, depth
    eps = max(1e-6, depth * 1e-6)
    through: list[list[tuple[float, float]]] = []
    blind = notches = 0
    for pocket in (e for e in ents if e.get("op") == "pocket"):
        try:
            psketch = by_id[str(pocket["sketch_ref"])]
            pprofile = _loops_of(psketch)
            po, px, py, pn = _frame_vecs(frame_of(psketch))
            pdepth = float(pocket["depth"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"reason": f"a pocket's profile could not be read: {exc}"[:200]}
        if pprofile.holes:
            return {"reason": "a pocket whose profile has an inner loop is not predicted"}
        s = _dot(pn, normal)
        if abs(abs(s) - 1.0) > 1e-6:
            return {"reason": "a pocket that is not parallel to the pad is not predicted"}
        h0 = _dot(_sub(po, origin), normal)
        ends = (h0, h0 + pdepth * s) if pocket.get("reversed") else (h0 - pdepth * s, h0)
        c_lo, c_hi = min(ends), max(ends)
        if c_hi <= lo + eps or c_lo >= hi - eps:
            continue
        cuts_through = c_lo <= lo + eps and c_hi >= hi - eps
        for loop in (pprofile.outer, *pprofile.islands):
            uv: list[tuple[float, float]] = []
            for u, v in loop:
                w = (
                    po[0] + px[0] * u + py[0] * v,
                    po[1] + px[1] * u + py[1] * v,
                    po[2] + px[2] * u + py[2] * v,
                )
                rel = _sub(w, origin)
                uv.append((_dot(rel, pad_x), _dot(rel, pad_y)))
            inside = [
                point_in_loop(p, profile.outer)
                and not any(point_in_loop(p, h) for h in profile.holes)
                for p in uv
            ]
            if not any(inside):
                continue
            if not all(inside):
                notches += 1
                continue
            if any(point_in_loop(q, uv) for h in profile.holes for q in h):
                return {"reason": "a pocket that takes in a sketched hole is not predicted"}
            if not cuts_through:
                blind += 1
                continue
            if any(point_in_loop(uv[0], t) or point_in_loop(t[0], uv) for t in through):
                return {"reason": "through cuts that overlap each other are not predicted"}
            through.append(uv)
    holes = len(profile.holes)
    basis = (
        f"{holes} inner loop(s) of the padded profile and {len(through)} pocket loop(s) "
        f"cut through the pad ({blind} blind, {notches} at an edge)"
    )
    # A cut that crosses the edge may split the part, so shells are then not predicted.
    return {"genus": holes + len(through), "shells": None if notches else 1, "basis": basis}


def compare_features_only(
    entities: Sequence[dict[str, Any]],
    *,
    exact_bbox: Any,
    exact_triangles: Sequence[Tri] | None,
    tolerance: Tolerance | None = None,
    mesh_note: str | None = None,
) -> dict[str, Any]:
    """The comparison for a multi-feature part: intended openings against the exact mesh.

    There is no L2 preview of such a part, so the shape checks say so
    (``not_evaluated``) and only ``features`` can pass or fail.
    """
    tol = tolerance or Tolerance()
    topology = ir_intended_topology(entities)
    ebox = _bbox_list(exact_bbox)
    diag = _diag(ebox) if ebox is not None else 0.0
    lin = tol.linear(diag)
    checks = [
        _check(name, "not_evaluated", NO_PREVIEW_NOTE)
        for name in ("bounding_box", "dimensions", "volume", "surface_distance")
    ]
    tris = list(exact_triangles or [])
    frame_ok, mesh_note = _mesh_in_frame(tris, ebox, lin, diag, mesh_note)
    checks.append(
        _features_check(
            topology,
            tris if frame_ok else [],
            weld=max(1e-4, diag * 1e-6),
            mesh_note=mesh_note,
        )
    )
    return {
        "outcome": _outcome(checks),
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
            "op": "multi_feature",
            "definition_hash": definition_hash(entities),
            "volume_mm3": None,
            "intended_through_holes": topology.get("genus"),
            "intended_topology": topology,
        },
        "method": (
            "intended openings read from the design IR (pad, pockets, edge rounds); "
            "exact side from the OCCT tessellation's genus and shells"
        ),
        "compared_at": datetime.now(UTC).isoformat(),
    }


def _has_feature_tree(entities: Sequence[Any]) -> bool:
    return any(isinstance(e, dict) and e.get("op") in ("pad", "revolve") for e in entities)


def comparison_for_source(
    source: dict[str, Any],
    *,
    validity: dict[str, Any],
    glb: bytes | None,
    mesh_note: str | None = None,
) -> dict[str, Any] | None:
    """The comparison for an exact build's source, or ``None`` when there is nothing to compare.

    A source has an L2 preview when its design IR is one supported preview
    feature (the parametric sketch path): every check runs. A multi-feature
    part (a feature tree with no L2 preview) gets the ``features`` check
    only, the others saying why they did not run. The tolerance is the
    source's ``comparison_tolerance`` (the component's), else the default.
    """
    if source.get("kind") != "design_ir":
        return None
    entities = (source.get("design_ir") or {}).get("entities") or []
    definition: dict[str, Any] | None
    try:
        definition = definition_from_entities(entities)
    except (FeatureDefinitionError, KeyError, TypeError, ValueError):
        definition = None
    if definition is None and not _has_feature_tree(entities):
        return None
    tris: list[Tri] | None = None
    if glb:
        try:
            tris = read_glb_triangles(glb)
        except Exception as exc:  # noqa: BLE001 - a GLB that does not parse is "not available"
            mesh_note = f"the exact tessellation could not be read: {exc}"[:200]
    tolerance = Tolerance.from_config(source.get("comparison_tolerance"))
    with tracer.start_as_current_span("twin.preview_comparison.compare") as span:
        if definition is not None:
            record = compare_preview_to_exact(
                definition,
                exact_bbox=validity.get("bounding_box"),
                exact_volume=validity.get("volume_mm3"),
                exact_triangles=tris,
                tolerance=tolerance,
                mesh_note=mesh_note,
            )
        else:
            record = compare_features_only(
                entities,
                exact_bbox=validity.get("bounding_box"),
                exact_triangles=tris,
                tolerance=tolerance,
                mesh_note=mesh_note,
            )
        span.set_attribute("comparison.scope", "preview" if definition else "features")
        span.set_attribute("comparison.outcome", record["outcome"])
        span.set_attribute("comparison.discrepancies", len(record["discrepancies"]))
    if source.get("parametric_sketch_ref"):
        record["preview"]["sketch_ref"] = source["parametric_sketch_ref"]
    return record


# ---------------------------------------------------------------------------
# Assemblies: component pose, alignment, interference and clearance
# ---------------------------------------------------------------------------

#: Node metadata key the assembly comparison is stored under.
ASSEMBLY_COMPARISON_KEY = "assembly_comparison"
#: Pairwise checks stop here (n(n-1)/2 pairs); past it they are not evaluated.
MAX_ASSEMBLY_COMPONENTS = 16
#: Bounded samples per component for the interference test.
ASSEMBLY_SAMPLES = 48
#: A pair whose two meshes hold more triangles than this is not tested.
MAX_PAIR_TRIANGLES = 40_000
DEFAULT_POSE_ABS_MM = 2.0
DEFAULT_POSE_REL = 0.05
POSE_BASIS = (
    "default: the larger of 2 mm and 5% of the assembly's bounding-box diagonal. The "
    "structured layout's sizes and positions are estimated, so this says whether each "
    "part sits where the layout put it, not whether the parts fit."
)
#: Two extents within this ratio leave a part's long axis undecided.
_ALIGN_AMBIGUOUS = 0.1


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def structured_layout_parts(glb: bytes) -> list[dict[str, Any]]:
    """The intended parts of an L1 structured mesh: ``[{component_id, name, box}]``.

    Read from each node's ``extras`` (``position_mm`` is the low corner and
    ``size_mm`` the size, in the layout's own z-up millimetres), not from its
    vertices, so the glTF axes do not matter.
    """
    from api_gateway.twin.glb_bounds import gltf_json

    out: list[dict[str, Any]] = []
    for node in gltf_json(glb).get("nodes") or []:
        extras = node.get("extras") if isinstance(node.get("extras"), dict) else None
        if not extras or extras.get("level") != "L1":
            continue
        pos, size = extras.get("position_mm"), extras.get("size_mm")
        try:
            lo = [float(c) for c in pos]
            sz = [float(c) for c in size]
        except (TypeError, ValueError):
            continue
        if len(lo) != 3 or len(sz) != 3:
            continue
        out.append(
            {
                "component_id": extras.get("component_id"),
                "name": str(extras.get("part_name") or node.get("name") or ""),
                "box": [*lo, lo[0] + sz[0], lo[1] + sz[1], lo[2] + sz[2]],
            }
        )
    return out


def _centre(box: Sequence[float]) -> Vec3:
    return ((box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2)


def _union(boxes: Sequence[Sequence[float]]) -> list[float]:
    return [
        *(min(b[i] for b in boxes) for i in range(3)),
        *(max(b[i + 3] for b in boxes) for i in range(3)),
    ]


def _long_axis(box: Sequence[float]) -> int | None:
    """The index of a box's longest side, or ``None`` when two sides are about equal."""
    ext = [box[i + 3] - box[i] for i in range(3)]
    order = sorted(range(3), key=lambda i: ext[i], reverse=True)
    top, second = ext[order[0]], ext[order[1]]
    if top <= 0 or (top - second) / top < _ALIGN_AMBIGUOUS:
        return None
    return order[0]


def _box_gap(a: Sequence[float], b: Sequence[float]) -> float:
    d = [max(b[i] - a[i + 3], 0.0, a[i] - b[i + 3]) for i in range(3)]
    return math.sqrt(sum(c * c for c in d))


_RAY: Vec3 = (0.5773502, 0.5773503, 0.5773504)


def _ray_hits(p: Vec3, t: Tri) -> bool:
    """Whether the ray from ``p`` along ``_RAY`` crosses triangle ``t`` (Moller-Trumbore)."""
    e1, e2 = _sub(t[1], t[0]), _sub(t[2], t[0])
    h = _cross(_RAY, e2)
    a = _dot(e1, h)
    if abs(a) < 1e-12:
        return False
    f = 1.0 / a
    s = _sub(p, t[0])
    u = f * _dot(s, h)
    if u < 0.0 or u > 1.0:
        return False
    q = _cross(s, e1)
    v = f * _dot(_RAY, q)
    if v < 0.0 or u + v > 1.0:
        return False
    return f * _dot(e2, q) > 1e-9


def point_inside(p: Vec3, tris: Sequence[Tri]) -> bool:
    """Whether ``p`` is inside a closed mesh (ray crossing parity)."""
    return sum(1 for t in tris if _ray_hits(p, t)) % 2 == 1


def _inside_box(p: Vec3, box: Sequence[float]) -> bool:
    return all(box[i] <= p[i] <= box[i + 3] for i in range(3))


def _pair_state(
    a: dict[str, Any], b: dict[str, Any], lin: float, closed: dict[str, bool]
) -> dict[str, Any]:
    """Interference or clearance of one pair of components (bounded, sampled)."""
    pair = {"a": a["label"], "b": b["label"]}
    gap = _box_gap(a["box"], b["box"])
    if gap > lin:
        return {**pair, "state": "clear", "clearance_mm": round(gap, 4), "method": "boxes"}
    if not (closed[a["key"]] and closed[b["key"]]):
        return {**pair, "state": "not_evaluated", "detail": "a mesh is not closed"}
    if len(a["tris"]) + len(b["tris"]) > MAX_PAIR_TRIANGLES:
        return {
            **pair,
            "state": "not_evaluated",
            "detail": f"more than {MAX_PAIR_TRIANGLES} triangles in the pair",
        }
    depth = 0.0
    nearest = math.inf
    for src, dst in ((a, b), (b, a)):
        dist = MeshDistance(dst["tris"])
        for p in mesh_samples(src["tris"], ASSEMBLY_SAMPLES):
            d = dist(p)
            nearest = min(nearest, d)
            if d > lin and _inside_box(p, dst["box"]) and point_inside(p, dst["tris"]):
                depth = max(depth, d)
    if depth > 0:
        return {**pair, "state": "interfering", "depth_mm": round(depth, 4), "method": "samples"}
    if nearest <= lin:
        return {**pair, "state": "contact", "clearance_mm": 0.0, "method": "samples"}
    return {**pair, "state": "clear", "clearance_mm": round(nearest, 4), "method": "samples"}


def compare_assembly(
    exact_parts: Sequence[dict[str, Any]],
    *,
    layout_parts: Sequence[dict[str, Any]] | None = None,
    abs_mm: float = DEFAULT_POSE_ABS_MM,
    rel: float = DEFAULT_POSE_REL,
) -> dict[str, Any]:
    """Compare an assembly's exact components with its structured layout. Pure.

    ``exact_parts`` is :func:`read_glb_components` of the exact GLB (one
    entry per component, triangles in the CAD frame); ``layout_parts`` is
    :func:`structured_layout_parts` of the L1 mesh, or ``None`` when the
    project has none. Checks:

    ``component_pose``
        each component's centre against the layout's, after removing the
        whole assembly's offset (the layout and the CAD need not share an
        origin);
    ``component_alignment``
        each component's long axis against the layout's (undecided when two
        sides are about equal);
    ``interference``
        every pair (up to :data:`MAX_ASSEMBLY_COMPONENTS` parts): clear by
        their boxes, else sampled points of one inside the other deeper than
        the tolerance. The smallest clearance found is reported; there is no
        clearance requirement recorded, so clearance is reported, not judged.
    """
    parts: list[dict[str, Any]] = [
        {
            "key": str(p.get("key") or p.get("component_id") or p.get("name")),
            "component_id": p.get("component_id"),
            "name": str(p.get("name") or ""),
            "label": str(p.get("component_id") or p.get("name") or ""),
            "tris": list(p.get("tris") or []),
        }
        for p in exact_parts
        if p.get("tris")
    ]
    for p in parts:
        p["box"] = _box_of(p["tris"])
    checks: list[dict[str, Any]] = []
    whole = _union([p["box"] for p in parts]) if parts else None
    diag = _diag(whole) if whole else 0.0
    lin = max(abs_mm, rel * diag)

    if len(parts) < 2:
        why = "the exact model has fewer than two components"
        checks += [
            _check("component_pose", "not_evaluated", why),
            _check("component_alignment", "not_evaluated", why),
            _check("interference", "not_evaluated", why),
        ]
    else:
        checks += _pose_checks(parts, layout_parts, lin)
        checks.append(_interference_check(parts, diag))
    return {
        "outcome": _outcome(checks),
        "checks": checks,
        "discrepancies": [c for c in checks if c["outcome"] == "fail"],
        "components": [
            {"component_id": p["component_id"], "name": p["name"], "box": p["box"]} for p in parts
        ],
        "tolerance": {
            "abs_mm": abs_mm,
            "rel": rel,
            "linear_mm": round(lin, 6),
            "basis": POSE_BASIS,
            "source": "default",
        },
        "method": (
            "exact components from the OCCT tessellation, grouped by the component id in "
            "each GLB node's extras; intended poses from the structured layout (L1); "
            f"interference from {ASSEMBLY_SAMPLES} samples per component and pair"
        ),
        "compared_at": datetime.now(UTC).isoformat(),
    }


def _pose_checks(
    parts: Sequence[dict[str, Any]],
    layout_parts: Sequence[dict[str, Any]] | None,
    lin: float,
) -> list[dict[str, Any]]:
    if not layout_parts:
        why = "there is no structured layout (L1) of this assembly to compare poses against"
        return [
            _check("component_pose", "not_evaluated", why),
            _check("component_alignment", "not_evaluated", why),
        ]
    by_id = {str(p["component_id"]): p for p in layout_parts if p.get("component_id")}
    by_name = {_key(str(p["name"])): p for p in layout_parts if p.get("name")}
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched: list[str] = []
    for p in parts:
        want = by_id.get(str(p["component_id"])) if p["component_id"] else None
        want = want or by_name.get(_key(p["name"]))
        if want is None:
            unmatched.append(p["label"])
        else:
            matched.append((p, want))
    if not matched:
        why = "no exact component matches a layout part by component id or name"
        return [
            _check("component_pose", "not_evaluated", why, unmatched=unmatched),
            _check("component_alignment", "not_evaluated", why, unmatched=unmatched),
        ]
    e_c = _centre(_union([p["box"] for p, _ in matched]))
    l_c = _centre(_union([w["box"] for _, w in matched]))
    offset = _sub(e_c, l_c)
    poses: list[dict[str, Any]] = []
    aligns: list[dict[str, Any]] = []
    for p, want in matched:
        c = _centre(p["box"])
        target = tuple(a + b for a, b in zip(_centre(want["box"]), offset, strict=True))
        d = math.dist(c, target)
        poses.append(
            {
                "component": p["label"],
                "offset_mm": round(d, 4),
                "outcome": "pass" if d <= lin else "fail",
            }
        )
        e_axis, l_axis = _long_axis(p["box"]), _long_axis(want["box"])
        if e_axis is None or l_axis is None:
            aligns.append({"component": p["label"], "outcome": "not_evaluated"})
        else:
            aligns.append(
                {
                    "component": p["label"],
                    "long_axis_exact": "xyz"[e_axis],
                    "long_axis_layout": "xyz"[l_axis],
                    "outcome": "pass" if e_axis == l_axis else "fail",
                }
            )
    worst = max(x["offset_mm"] for x in poses)
    moved = [x["component"] for x in poses if x["outcome"] == "fail"]
    pose = _check(
        "component_pose",
        "fail" if moved else ("not_evaluated" if unmatched else "pass"),
        f"{len(matched)} component(s) compared; largest offset from the layout {worst:.2f} mm "
        f"(tolerance {lin:.2f} mm)"
        + (f"; not in the layout: {', '.join(unmatched)}" if unmatched else ""),
        value_mm=worst,
        tolerance_mm=lin,
        components=poses,
        unmatched=unmatched,
    )
    turned = [x["component"] for x in aligns if x["outcome"] == "fail"]
    decided = [x for x in aligns if x["outcome"] != "not_evaluated"]
    if turned:
        align_outcome = "fail"
    elif not decided:
        align_outcome = "not_evaluated"
    else:
        align_outcome = "pass"
    align = _check(
        "component_alignment",
        align_outcome,
        f"{len(decided)} component(s) with a clear long axis; "
        + (f"turned from the layout: {', '.join(turned)}" if turned else "all as laid out"),
        components=aligns,
    )
    return [pose, align]


def _interference_check(parts: Sequence[dict[str, Any]], diag: float) -> dict[str, Any]:
    if len(parts) > MAX_ASSEMBLY_COMPONENTS:
        return _check(
            "interference",
            "not_evaluated",
            f"{len(parts)} components; the bounded check covers up to {MAX_ASSEMBLY_COMPONENTS}",
        )
    # Contact within a hair of the surface is assembly, not interference.
    lin = max(1e-3, diag * 1e-4)
    closed = {
        p["key"]: mesh_topology(p["tris"], weld=max(1e-4, diag * 1e-6))[0] is not None
        for p in parts
    }
    pairs = [
        _pair_state(parts[i], parts[j], lin, closed)
        for i in range(len(parts))
        for j in range(i + 1, len(parts))
    ]
    hits = [p for p in pairs if p["state"] == "interfering"]
    skipped = [p for p in pairs if p["state"] == "not_evaluated"]
    clear = [p for p in pairs if "clearance_mm" in p]
    smallest = min(clear, key=lambda p: p["clearance_mm"]) if clear else None
    detail = f"{len(pairs)} pair(s): {len(hits)} interfering"
    if smallest is not None:
        detail += (
            f"; smallest clearance {smallest['clearance_mm']:.3f} mm between "
            f"{smallest['a']} and {smallest['b']} (reported, not judged: no clearance "
            "requirement is recorded)"
        )
    if skipped:
        detail += f"; {len(skipped)} pair(s) not evaluated"
    outcome = "fail" if hits else ("not_evaluated" if skipped else "pass")
    return _check(
        "interference",
        outcome,
        detail,
        pairs=pairs,
        min_clearance_mm=smallest["clearance_mm"] if smallest else None,
        contact_tolerance_mm=lin,
    )


def assembly_comparison_for_glbs(
    exact_glb: bytes | None,
    layout_glb: bytes | None,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """:func:`compare_assembly` from the two GLBs; nothing measurable is ``not_evaluated``."""
    with tracer.start_as_current_span("twin.preview_comparison.assembly") as span:
        exact: list[dict[str, Any]] = []
        why = note or "the exact tessellation was not available"
        if exact_glb:
            try:
                exact = read_glb_components(exact_glb)
            except Exception as exc:  # noqa: BLE001 - an unreadable GLB is "not available"
                why = f"the exact tessellation could not be read: {exc}"[:200]
        layout: list[dict[str, Any]] | None = None
        if layout_glb:
            try:
                layout = structured_layout_parts(layout_glb) or None
            except Exception as exc:  # noqa: BLE001 - no layout to compare poses against
                logger.info("assembly_layout_unreadable", error=str(exc)[:200])
        if not exact:
            checks = [
                _check(c, "not_evaluated", why)
                for c in ("component_pose", "component_alignment", "interference")
            ]
            record: dict[str, Any] = {
                "outcome": "incomplete",
                "checks": checks,
                "discrepancies": [],
                "components": [],
                "compared_at": datetime.now(UTC).isoformat(),
            }
        else:
            record = compare_assembly(exact, layout_parts=layout)
        span.set_attribute("comparison.outcome", record["outcome"])
        span.set_attribute("comparison.components", len(record["components"]))
        return record


def _record_metric(record: dict[str, Any]) -> None:
    try:
        from observability.metrics import collector_for

        metrics = collector_for("metaforge-gateway")
        metrics.record_preview_comparison(str(record["outcome"]))
        for check in record["discrepancies"]:
            metrics.record_preview_discrepancy(str(check["check"]))
    except Exception:  # noqa: BLE001 - a metric never fails a comparison
        pass


async def _newest_layout_glb(
    twin: Any, project_id: Any, read_blob: Callable[[Any], bytes | None]
) -> bytes | None:
    """The project's newest structured mesh (L1), or ``None``."""
    from twin_core.models.enums import WorkProductType
    from twin_core.representation import STRUCTURED_MESH_TOOL

    if project_id is None:
        return None
    try:
        renders = await twin.list_work_products(
            work_product_type=WorkProductType.RENDER, project_id=project_id
        )
    except Exception as exc:  # noqa: BLE001 - no layout is "not evaluated"
        logger.info("assembly_layout_lookup_failed", error=str(exc)[:200])
        return None
    meshes = [
        wp
        for wp in renders
        if (getattr(wp, "metadata", None) or {}).get("generated_by") == STRUCTURED_MESH_TOOL
    ]
    if not meshes:
        return None
    newest = max(meshes, key=lambda wp: str(getattr(wp, "created_at", "") or ""))
    return read_blob(newest)


async def assembly_comparison_for_node(
    twin: Any,
    wp: Any,
    *,
    read_blob: Callable[[Any], bytes | None],
    to_glb: Callable[[bytes, str], tuple[bytes | None, str | None]],
    refresh: bool = False,
) -> dict[str, Any]:
    """Compare a CAD revision's components with the project's layout, and store it.

    ``read_blob(wp)`` reads a work product's file; ``to_glb(step, filename)``
    returns ``(glb, None)`` or ``(None, why)``. The STEP carries the
    revision's component ids (so the GLB nodes do too) before it is
    converted. A stored record for the same content is returned unless
    ``refresh``. Never raises for a missing mesh or layout: those checks are
    ``not_evaluated`` with the reason.
    """
    from twin_core.component_identity import stamp_step_for_node
    from twin_core.geometry_state import set_node_metadata

    meta = dict(getattr(wp, "metadata", None) or {})
    stored = meta.get(ASSEMBLY_COMPARISON_KEY)
    content_hash = getattr(wp, "content_hash", None)
    if (
        not refresh
        and isinstance(stored, dict)
        and stored.get("content_hash") == content_hash
        and content_hash
    ):
        return stored
    with tracer.start_as_current_span("twin.preview_comparison.assembly_node") as span:
        span.set_attribute("twin.node_id", str(wp.id))
        glb: bytes | None = None
        note: str | None = None
        step = read_blob(wp)
        if not step:
            note = "the revision has no stored STEP to tessellate"
        else:
            step, _n = stamp_step_for_node(step, name=wp.name, meta=meta)
            glb, note = await asyncio.to_thread(to_glb, step, f"{wp.id}.step")
        layout = await _newest_layout_glb(twin, getattr(wp, "project_id", None), read_blob)
        record = await asyncio.to_thread(assembly_comparison_for_glbs, glb, layout, note=note)
        record["content_hash"] = content_hash
        if meta.get("item_key") and isinstance(meta.get("item_revision"), int):
            record["item_ref"] = f"{meta['item_key']}@{meta['item_revision']}"
        try:
            await set_node_metadata(twin, wp, **{ASSEMBLY_COMPARISON_KEY: record})
        except Exception as exc:  # noqa: BLE001 - the answer still stands unstored
            span.record_exception(exc)
            logger.warning("assembly_comparison_not_stored", node_id=str(wp.id), error=str(exc))
        _record_metric(record)
        span.set_attribute("comparison.outcome", record["outcome"])
        logger.info(
            "assembly_comparison_recorded",
            node_id=str(wp.id),
            outcome=record["outcome"],
            components=len(record["components"]),
            discrepancies=[c["check"] for c in record["discrepancies"]],
        )
        return record


__all__ = [
    "ASSEMBLY_COMPARISON_KEY",
    "assembly_comparison_for_node",
    "COMPARISON_KEY",
    "DEFAULT_BASIS",
    "MAX_ASSEMBLY_COMPONENTS",
    "MeshDistance",
    "Tolerance",
    "assembly_comparison_for_glbs",
    "compare_assembly",
    "compare_features_only",
    "compare_preview_to_exact",
    "comparison_for_source",
    "intended_topology",
    "ir_intended_topology",
    "mesh_genus",
    "mesh_topology",
    "point_inside",
    "read_glb_components",
    "structured_layout_parts",
    "mesh_samples",
    "read_glb_triangles",
]
