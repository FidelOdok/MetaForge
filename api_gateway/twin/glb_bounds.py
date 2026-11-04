"""The bounding box of a GLB, read from its glTF JSON alone.

A reference model's size is checked against the concept's envelope without
loading a mesh library and without reading a single vertex: glTF requires
every ``POSITION`` accessor to carry its ``min`` and ``max``. Each mesh's
accessor box is carried through its node's transform (``matrix``, or
``translation`` / ``rotation`` / ``scale``) and the eight transformed corners
are unioned. With no rotation that is exact; with one it can only grow the
box, so the result says it is approximate. A file with no scene graph to
follow falls back to the union of the accessors' own boxes, also approximate.

:func:`envelope_fit` finds the one uniform factor that best fits a model's box
to a concept's envelope, and :func:`scale_glb` applies it by giving the scene
a parent node with that ``scale``: only the JSON chunk changes, never a vertex
buffer, and every node keeps its own transform under the new parent.

Pure stdlib.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from typing import Any

_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A  # "JSON"

Matrix = list[float]  # column-major 4x4, as glTF stores it
_IDENTITY: Matrix = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


#: The name of the parent node :func:`scale_glb` adds.
SCALE_NODE_NAME = "metaforge_envelope_scale"
#: How the two horizontal glTF axes may be paired with the concept's width and
#: depth: a generator may face the product along x or along z.
AXIS_MAPPINGS: tuple[str, ...] = ("x=width, z=depth", "z=width, x=depth")


class GlbBoundsError(ValueError):
    """The file is not a GLB whose bounds can be read."""


@dataclass(frozen=True)
class GlbBounds:
    """Axis-aligned bounds in the file's own units and axes (glTF: +Y up)."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]
    #: ``True`` when a rotation or a missing scene graph made it a superset.
    approximate: bool
    #: How the box was found: ``node_transforms`` or ``accessor_union``.
    method: str

    @property
    def size(self) -> tuple[float, float, float]:
        return (
            self.max[0] - self.min[0],
            self.max[1] - self.min[1],
            self.max[2] - self.min[2],
        )


def gltf_json(glb: bytes) -> dict[str, Any]:
    """The JSON chunk of a binary glTF."""
    if len(glb) < 20 or glb[:4] != _MAGIC:
        raise GlbBoundsError("not a GLB (no glTF header)")
    length, chunk_type = struct.unpack_from("<II", glb, 12)
    if chunk_type != _JSON_CHUNK or 20 + length > len(glb):
        raise GlbBoundsError("the GLB's first chunk is not its JSON")
    try:
        doc = json.loads(glb[20 : 20 + length].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GlbBoundsError(f"the GLB's JSON could not be read: {exc}") from exc
    if not isinstance(doc, dict):
        raise GlbBoundsError("the GLB's JSON is not an object")
    return doc


def _mul(a: Matrix, b: Matrix) -> Matrix:
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def _local(node: dict[str, Any]) -> tuple[Matrix, bool]:
    """A node's local matrix, and whether it rotates."""
    matrix = node.get("matrix")
    if isinstance(matrix, list) and len(matrix) == 16:
        m = [float(v) for v in matrix]
        # Any off-diagonal term in the upper 3x3 is a rotation (or shear).
        rotates = any(abs(m[c * 4 + r]) > 1e-9 for c in range(3) for r in range(3) if c != r)
        return m, rotates
    t = [float(v) for v in node.get("translation") or (0, 0, 0)]
    s = [float(v) for v in node.get("scale") or (1, 1, 1)]
    x, y, z, w = (float(v) for v in node.get("rotation") or (0, 0, 0, 1))
    rotates = abs(x) > 1e-9 or abs(y) > 1e-9 or abs(z) > 1e-9
    r = [
        1 - 2 * (y * y + z * z),
        2 * (x * y + z * w),
        2 * (x * z - y * w),
        2 * (x * y - z * w),
        1 - 2 * (x * x + z * z),
        2 * (y * z + x * w),
        2 * (x * z + y * w),
        2 * (y * z - x * w),
        1 - 2 * (x * x + y * y),
    ]
    m = [
        r[0] * s[0], r[1] * s[0], r[2] * s[0], 0.0,
        r[3] * s[1], r[4] * s[1], r[5] * s[1], 0.0,
        r[6] * s[2], r[7] * s[2], r[8] * s[2], 0.0,
        t[0], t[1], t[2], 1.0,
    ]  # fmt: skip
    return m, rotates


def _apply(m: Matrix, p: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _accessor_box(doc: dict[str, Any], index: Any) -> tuple[list[float], list[float]] | None:
    accessors = doc.get("accessors") or []
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        return None
    acc = accessors[index]
    lo, hi = acc.get("min"), acc.get("max")
    if not (isinstance(lo, list) and isinstance(hi, list) and len(lo) >= 3 and len(hi) >= 3):
        return None
    return [float(v) for v in lo[:3]], [float(v) for v in hi[:3]]


def _mesh_boxes(doc: dict[str, Any], mesh_index: Any) -> list[tuple[list[float], list[float]]]:
    meshes = doc.get("meshes") or []
    if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes):
        return []
    boxes = []
    for prim in meshes[mesh_index].get("primitives") or []:
        box = _accessor_box(doc, (prim.get("attributes") or {}).get("POSITION"))
        if box is not None:
            boxes.append(box)
    return boxes


def glb_bounds(glb: bytes) -> GlbBounds:
    """The axis-aligned bounds of everything a GLB's default scene draws."""
    doc = gltf_json(glb)
    nodes = doc.get("nodes") or []
    scenes = doc.get("scenes") or []
    scene_index = doc.get("scene", 0)
    roots: list[Any] = []
    if isinstance(scene_index, int) and 0 <= scene_index < len(scenes):
        roots = list(scenes[scene_index].get("nodes") or [])
    elif nodes:
        children = {c for n in nodes for c in (n.get("children") or [])}
        roots = [i for i in range(len(nodes)) if i not in children]

    points: list[tuple[float, float, float]] = []
    rotated = False
    stack: list[tuple[Any, Matrix, int]] = [(r, _IDENTITY, 0) for r in roots]
    while stack:
        index, parent, depth = stack.pop()
        if not isinstance(index, int) or not 0 <= index < len(nodes) or depth > 64:
            continue
        node = nodes[index]
        local, rotates = _local(node)
        rotated = rotated or rotates
        world = _mul(parent, local)
        for lo, hi in _mesh_boxes(doc, node.get("mesh")):
            for x in (lo[0], hi[0]):
                for y in (lo[1], hi[1]):
                    for z in (lo[2], hi[2]):
                        points.append(_apply(world, (x, y, z)))
        stack += [(c, world, depth + 1) for c in node.get("children") or []]

    method = "node_transforms"
    if not points:
        # No scene graph reaches a mesh: union every mesh's accessor box.
        method = "accessor_union"
        rotated = True
        for i in range(len(doc.get("meshes") or [])):
            for lo, hi in _mesh_boxes(doc, i):
                points += [(lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])]
    if not points:
        raise GlbBoundsError("the GLB has no mesh positions with min/max")
    return GlbBounds(
        min=(
            min(p[0] for p in points),
            min(p[1] for p in points),
            min(p[2] for p in points),
        ),
        max=(
            max(p[0] for p in points),
            max(p[1] for p in points),
            max(p[2] for p in points),
        ),
        approximate=rotated,
        method=method,
    )


def paired_size(size_m: tuple[float, float, float], mapping: str) -> tuple[float, float, float]:
    """``size_m`` (glTF x, y up, z) as (width, depth, height) under ``mapping``."""
    gx, gy, gz = size_m
    if mapping == AXIS_MAPPINGS[0]:
        return (gx, gz, gy)
    if mapping == AXIS_MAPPINGS[1]:
        return (gz, gx, gy)
    raise ValueError(f"unknown axis mapping {mapping!r}")


def envelope_deviation(
    size_m: tuple[float, float, float],
    envelope_mm: tuple[float, float, float],
    *,
    scaled_to_metres: bool,
) -> dict[str, Any]:
    """How a model's extents compare with the concept's envelope, per axis.

    ``size_m`` is in glTF axes (x, y up, z); ``envelope_mm`` in the concept's
    (width x, depth y, height z). Height is glTF y. The two horizontal axes
    are paired whichever way fits better, because a generator may face the
    product along x or z; the pairing used is reported.

    ``scaled_to_metres`` says the model is in real units (``auto_size``):
    the deviation is then absolute. Otherwise the model's units are its own,
    and it is scaled so its largest axis matches the envelope's: the
    deviation is then of proportions only.
    """
    width, depth, height = (v / 1000.0 for v in envelope_mm)
    best: dict[str, Any] | None = None
    for mapping in AXIS_MAPPINGS:
        model = paired_size(size_m, mapping)
        factor = 1.0
        if not scaled_to_metres:
            largest = max(model)
            factor = max(width, depth, height) / largest if largest > 0 else 1.0
        pct = {}
        wanted = (width, depth, height)
        for axis, got, want in zip(("width", "depth", "height"), model, wanted, strict=True):
            pct[axis] = round((got * factor - want) / want * 100, 1) if want > 0 else None
        score = sum(abs(v) for v in pct.values() if v is not None)
        if best is None or score < best["_score"]:
            best = {"_score": score, "axis_mapping": mapping, "deviation_pct": pct}
    assert best is not None
    best.pop("_score")
    best["basis"] = "absolute" if scaled_to_metres else "proportional"
    return best


def envelope_fit(
    size_m: tuple[float, float, float],
    envelope_mm: tuple[float, float, float],
) -> dict[str, Any]:
    """The uniform factor that best fits a model's box to the envelope.

    ``size_m`` is in glTF axes (x, y up, z) and the model's own units;
    ``envelope_mm`` is (width, depth, height). For each pairing of the
    horizontal axes (:data:`AXIS_MAPPINGS`) the factor ``s`` minimising
    ``sum((ln(s * model_i) - ln(envelope_i)) ** 2)`` over the three axes is
    the geometric mean of the per-axis ratios; the pairing whose residual is
    smaller wins. A log error weighs "twice too big" and "half too small"
    alike, so no one axis dominates.

    Returns ``scale_factor`` (model units to metres), ``axis_mapping``, and
    ``proportion_error_pct`` per axis after scaling: what a uniform factor
    cannot fix, because the model's proportions differ from the envelope's.
    ``ValueError`` when an axis of either box is not positive.
    """
    wanted = (envelope_mm[0] / 1000.0, envelope_mm[1] / 1000.0, envelope_mm[2] / 1000.0)
    if any(not w > 0 for w in wanted):
        raise ValueError("the envelope has an axis that is not positive")
    if any(not v > 0 for v in size_m):
        raise ValueError("the model has an axis with no extent; it cannot be fitted")
    best: tuple[float, str, float, tuple[float, float, float]] | None = None
    for mapping in AXIS_MAPPINGS:
        model = paired_size(size_m, mapping)
        logs = [math.log(w / m) for m, w in zip(model, wanted, strict=True)]
        log_factor = sum(logs) / len(logs)
        residual = sum((v - log_factor) ** 2 for v in logs)
        if best is None or residual < best[0] - 1e-12:
            best = (residual, mapping, math.exp(log_factor), model)
    assert best is not None
    residual, mapping, factor, model = best
    error = {
        axis: round((got * factor - want) / want * 100, 1)
        for axis, got, want in zip(("width", "depth", "height"), model, wanted, strict=True)
    }
    return {
        "scale_factor": factor,
        "axis_mapping": mapping,
        "proportion_error_pct": error,
        "log_residual": residual,
    }


def _chunks_after_json(glb: bytes) -> bytes:
    """Every chunk after the JSON one (the BIN chunk, usually), unchanged."""
    (length,) = struct.unpack_from("<I", glb, 12)
    (total,) = struct.unpack_from("<I", glb, 8)
    end = min(total, len(glb)) if total >= 20 + length else len(glb)
    return glb[20 + length : end]


def _scale_in_place(node: dict[str, Any], factor: float) -> None:
    """Scale one node's whole local transform uniformly, about its parent's origin.

    ``S * T * R * Sc`` is ``T(f t) * R * (f Sc)`` because a uniform scale
    commutes with any rotation, so this is exact for a rotated node too.
    """
    matrix = node.get("matrix")
    if isinstance(matrix, list) and len(matrix) == 16:
        node["matrix"] = [
            float(v) * factor if (i % 4) < 3 else float(v) for i, v in enumerate(matrix)
        ]
        return
    t = node.get("translation") or [0.0, 0.0, 0.0]
    s = node.get("scale") or [1.0, 1.0, 1.0]
    node["translation"] = [float(v) * factor for v in t]
    node["scale"] = [float(v) * factor for v in s]


def scale_glb(glb: bytes, factor: float) -> bytes:
    """``glb`` scaled uniformly by ``factor``, by editing its JSON chunk alone.

    The usual file has one scene: a new parent node carrying ``scale`` takes
    the scene's root nodes as its children and becomes the scene's only
    root, so every node keeps its own transform (and any animation of it).
    A file whose scenes do not share one root list, or that has no scene,
    has each root node's own transform scaled instead, which is the same
    result for a static model. Buffers, accessors and every other chunk are
    copied byte for byte.
    """
    if not math.isfinite(factor) or not factor > 0:
        raise GlbBoundsError(f"a scale factor must be positive and finite, not {factor!r}")
    doc = gltf_json(glb)
    (version,) = struct.unpack_from("<I", glb, 4)
    rest = _chunks_after_json(glb)
    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
        doc["nodes"] = nodes
    raw_scenes = doc.get("scenes")
    scenes = [s for s in raw_scenes if isinstance(s, dict)] if isinstance(raw_scenes, list) else []
    root_lists = [list(s.get("nodes") or []) for s in scenes]
    if root_lists and root_lists[0] and all(r == root_lists[0] for r in root_lists):
        index = len(nodes)
        nodes.append(
            {
                "name": SCALE_NODE_NAME,
                "scale": [factor, factor, factor],
                "children": root_lists[0],
            }
        )
        for scene in scenes:
            scene["nodes"] = [index]
    else:
        children = {c for n in nodes if isinstance(n, dict) for c in (n.get("children") or [])}
        roots = {i for lst in root_lists for i in lst if isinstance(i, int)}
        if not roots:
            roots = {i for i in range(len(nodes)) if i not in children}
        for i in sorted(roots):
            if 0 <= i < len(nodes) and isinstance(nodes[i], dict):
                _scale_in_place(nodes[i], factor)
    raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    total = 12 + 8 + len(raw) + len(rest)
    return (
        _MAGIC
        + struct.pack("<II", version, total)
        + struct.pack("<II", len(raw), _JSON_CHUNK)
        + raw
        + rest
    )
