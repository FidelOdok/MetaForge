"""A concept's block layout as a structured 3D mesh, L1 (C3D slice 2).

``twin.render_concept_sketch`` draws a concept's block layout (named boxes and
cylinders with sizes and positions in mm) as a 2D sheet. This turns the same
layout into a GLB: one node per part, named with the part's component id,
with ``extras`` saying which component it is, its shape, its size and
position, and that every dimension is *estimated*. It is the bridge between
the AI concept (L0, no parts) and exact CAD (L3): the parts are identified,
their sizes are not checked.

Deterministic and free: no provider, no clock, no randomness. The same layout
and the same component ids give byte-identical GLB, so an unchanged concept
is never redrawn as a new revision.

Axes: the layout has x width, y depth (front to back) and z up, in mm. glTF
has +Y up and metres, so a layout point ``(x, y, z)`` is written as
``(x, z, -y) / 1000``, a proper rotation, so faces keep their winding.

Pure stdlib (tool_registry may not import twin_core).
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from typing import Any

from tool_registry.tools.twin.concept_sketch import ConceptLayout, SketchPart

GLB_MAGIC = b"glTF"
_JSON = 0x4E4F534A
_BIN = 0x004E4942
_FLOAT = 5126
_UINT32 = 5125
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
#: Sides of a cylinder (the 2D sketch draws 24 too).
CYLINDER_SEGMENTS = 24
ROOT_NODE_NAME = "metaforge_structured_mesh"
GENERATOR = "MetaForge twin.render_structured_mesh"

#: A grey per provenance, so a viewer can tell guesses from checked sizes.
_COLOURS: dict[str, tuple[float, float, float]] = {
    "verified": (0.55, 0.62, 0.70),
    "intended": (0.72, 0.74, 0.78),
    "assumption": (0.86, 0.80, 0.62),
    "open": (0.88, 0.66, 0.62),
}

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class StructuredMesh:
    glb: bytes
    #: ``[{component_id, name, node}]`` in node order.
    nodes: list[dict[str, Any]]
    triangle_count: int
    overall_mm: Vec


def _to_gltf(p: Vec) -> Vec:
    return (p[0] / 1000.0, p[2] / 1000.0, -p[1] / 1000.0)


def _dir_to_gltf(n: Vec) -> Vec:
    return (n[0], n[2], -n[1])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


class _Builder:
    """Flat-shaded triangles in the part's local layout frame (mm, centred)."""

    def __init__(self) -> None:
        self.positions: list[Vec] = []
        self.normals: list[Vec] = []
        self.indices: list[int] = []

    def quad_or_fan(self, ring: list[Vec], normal: Vec) -> None:
        """A convex planar polygon, fanned, wound to face ``normal``."""
        base = len(self.positions)
        self.positions.extend(ring)
        self.normals.extend([normal] * len(ring))
        for i in range(1, len(ring) - 1):
            a, b, c = ring[0], ring[i], ring[i + 1]
            tri = (base, base + i, base + i + 1)
            if _dot(_cross(_sub(b, a), _sub(c, a)), normal) < 0:
                tri = (base, base + i + 1, base + i)
            self.indices.extend(tri)


def _box(b: _Builder, h: Vec) -> None:
    for axis in range(3):
        for sign in (-1.0, 1.0):
            n = [0.0, 0.0, 0.0]
            n[axis] = sign
            u, v = [i for i in range(3) if i != axis]
            ring: list[Vec] = []
            for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0.0, 0.0, 0.0]
                p[axis] = sign * h[axis]
                p[u] = su * h[u]
                p[v] = sv * h[v]
                ring.append((p[0], p[1], p[2]))
            b.quad_or_fan(ring, (n[0], n[1], n[2]))


def _cylinder(b: _Builder, h: Vec, axis: int) -> None:
    u, v = [i for i in range(3) if i != axis]
    ru, rv, half = h[u], h[v], h[axis]

    def point(angle: float, along: float) -> Vec:
        p = [0.0, 0.0, 0.0]
        p[axis] = along
        p[u] = ru * math.cos(angle)
        p[v] = rv * math.sin(angle)
        return (p[0], p[1], p[2])

    angles = [2 * math.pi * k / CYLINDER_SEGMENTS for k in range(CYLINDER_SEGMENTS)]
    for sign in (-1.0, 1.0):
        n = [0.0, 0.0, 0.0]
        n[axis] = sign
        b.quad_or_fan([point(a, sign * half) for a in angles], (n[0], n[1], n[2]))
    step = 2 * math.pi / CYLINDER_SEGMENTS
    for a0 in angles:
        a1 = a0 + step
        mid = a0 + step / 2
        n = [0.0, 0.0, 0.0]
        n[u] = math.cos(mid) / max(ru, 1e-9)
        n[v] = math.sin(mid) / max(rv, 1e-9)
        length = math.sqrt(n[u] ** 2 + n[v] ** 2) or 1.0
        normal = (n[0] / length, n[1] / length, n[2] / length)
        ring = [point(a0, -half), point(a1, -half), point(a1, half), point(a0, half)]
        b.quad_or_fan(ring, normal)


_AXIS = {"x": 0, "y": 1, "z": 2}


def _part_geometry(part: SketchPart) -> _Builder:
    b = _Builder()
    half = (part.size[0] / 2, part.size[1] / 2, part.size[2] / 2)
    if part.shape == "cylinder":
        _cylinder(b, half, _AXIS.get(part.axis, 2))
    else:
        _box(b, half)
    return b


def _pad4(data: bytes, pad: bytes = b"\x00") -> bytes:
    return data + pad * ((4 - len(data) % 4) % 4)


def build_structured_mesh(
    layout: ConceptLayout,
    component_ids: dict[str, str],
    *,
    extras: dict[str, Any] | None = None,
) -> StructuredMesh:
    """The layout as a GLB, one node per part named by its component id.

    ``component_ids`` maps each part name to its id; a part with no id is
    named after itself (and its extras say ``component_id: null``).
    """
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    material_index: dict[str, int] = {}
    triangles = 0

    def view(data: bytes, target: int) -> int:
        offset = len(binary)
        binary.extend(_pad4(data))
        buffer_views.append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target}
        )
        return len(buffer_views) - 1

    for index, part in enumerate(layout.parts):
        geo = _part_geometry(part)
        positions = [_to_gltf(p) for p in geo.positions]
        normals = [_dir_to_gltf(n) for n in geo.normals]
        pos_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
        nrm_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
        idx_bytes = struct.pack(f"<{len(geo.indices)}I", *geo.indices)
        lo = [min(p[i] for p in positions) for i in range(3)]
        hi = [max(p[i] for p in positions) for i in range(3)]
        # Round-trip the bounds through float32 so min/max match the data.
        lo = [struct.unpack("<f", struct.pack("<f", v))[0] for v in lo]
        hi = [struct.unpack("<f", struct.pack("<f", v))[0] for v in hi]
        accessors.append(
            {
                "bufferView": view(pos_bytes, _ARRAY_BUFFER),
                "componentType": _FLOAT,
                "count": len(positions),
                "type": "VEC3",
                "min": lo,
                "max": hi,
            }
        )
        pos_acc = len(accessors) - 1
        accessors.append(
            {
                "bufferView": view(nrm_bytes, _ARRAY_BUFFER),
                "componentType": _FLOAT,
                "count": len(normals),
                "type": "VEC3",
            }
        )
        nrm_acc = len(accessors) - 1
        accessors.append(
            {
                "bufferView": view(idx_bytes, _ELEMENT_ARRAY_BUFFER),
                "componentType": _UINT32,
                "count": len(geo.indices),
                "type": "SCALAR",
            }
        )
        idx_acc = len(accessors) - 1
        if part.provenance not in material_index:
            r, g, bl = _COLOURS.get(part.provenance, (0.75, 0.75, 0.75))
            materials.append(
                {
                    "name": f"provenance_{part.provenance}",
                    "pbrMetallicRoughness": {
                        "baseColorFactor": [r, g, bl, 1.0],
                        "metallicFactor": 0.0,
                        "roughnessFactor": 0.85,
                    },
                }
            )
            material_index[part.provenance] = len(materials) - 1
        meshes.append(
            {
                "name": part.name,
                "primitives": [
                    {
                        "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                        "indices": idx_acc,
                        "material": material_index[part.provenance],
                        "mode": 4,
                    }
                ],
            }
        )
        cid = component_ids.get(part.name)
        centre = _to_gltf(part.centre)
        nodes.append(
            {
                "name": cid or part.name,
                "mesh": index,
                "translation": [round(c, 9) for c in centre],
                "extras": {
                    "component_id": cid,
                    "part_name": part.name,
                    "shape": part.shape,
                    "axis": part.axis,
                    "size_mm": list(part.size),
                    "position_mm": list(part.position),
                    "provenance": part.provenance,
                    "dimensions": "estimated",
                    "level": "L1",
                },
            }
        )
        summary.append({"component_id": cid, "name": part.name, "node": cid or part.name})
        triangles += len(geo.indices) // 3

    root_extras = {
        "up_axis": "y",
        "level": "L1",
        "authority": "structured",
        "dimensions": "estimated",
        "concept_name": layout.concept_name,
        **(extras or {}),
    }
    nodes.append(
        {
            "name": ROOT_NODE_NAME,
            "children": list(range(len(layout.parts))),
            "extras": root_extras,
        }
    )
    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": GENERATOR, "extras": root_extras},
        "scene": 0,
        "scenes": [{"name": layout.concept_name, "nodes": [len(nodes) - 1]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    json_bytes = _pad4(json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8"), b" ")
    bin_bytes = bytes(binary)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    glb = (
        GLB_MAGIC
        + struct.pack("<II", 2, total)
        + struct.pack("<II", len(json_bytes), _JSON)
        + json_bytes
        + struct.pack("<II", len(bin_bytes), _BIN)
        + bin_bytes
    )
    return StructuredMesh(
        glb=glb, nodes=summary, triangle_count=triangles, overall_mm=layout.overall()
    )


__all__ = [
    "CYLINDER_SEGMENTS",
    "GENERATOR",
    "ROOT_NODE_NAME",
    "StructuredMesh",
    "build_structured_mesh",
]
