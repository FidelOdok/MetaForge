"""The bounding box of a GLB, read from its glTF JSON alone.

A reference model's size is checked against the concept's envelope without
loading a mesh library and without reading a single vertex: glTF requires
every ``POSITION`` accessor to carry its ``min`` and ``max``. Each mesh's
accessor box is carried through its node's transform (``matrix``, or
``translation`` / ``rotation`` / ``scale``) and the eight transformed corners
are unioned. With no rotation that is exact; with one it can only grow the
box, so the result says it is approximate. A file with no scene graph to
follow falls back to the union of the accessors' own boxes, also approximate.

Pure stdlib.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A  # "JSON"

Matrix = list[float]  # column-major 4x4, as glTF stores it
_IDENTITY: Matrix = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


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
    gx, gy, gz = size_m
    pairings = {
        "x=width, z=depth": (gx, gz),
        "z=width, x=depth": (gz, gx),
    }
    best: dict[str, Any] | None = None
    for mapping, (mw, md) in pairings.items():
        model = (mw, md, gy)
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
