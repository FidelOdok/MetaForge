"""The parts of a GLB: read them, label them, tag them and swap one (concept parts).

A segmentation provider returns one GLB for the whole model. Whether its parts
come back as separate nodes, and where a semantic label is carried, is not
documented, so :func:`read_parts` reads defensively:

* every node of the default scene that draws a mesh is a candidate part, named
  by its node name, else its mesh name, else ``part_<n>``;
* a label is looked for in the node's and the mesh's ``extras`` (``label``,
  ``semantic_label``, ``part_label``, ``category``, ``class``), then in a node
  or mesh name that is not a generic one (``tripo_part_3``, ``Mesh``,
  ``Object_2`` are generic);
* fewer than two mesh nodes is ``not_segmented``: one mesh, even with several
  primitives, has no part a later call could address by name, and that is
  reported as it is rather than guessed into parts.

:func:`set_node_extras` writes ``extras`` into nodes (the JSON chunk only).
:func:`swap_part` replaces what one node draws with another GLB's scene,
fitted to that node's own box: the new data is appended to the binary chunk,
so every other node's accessors, buffer views and bytes stay exactly as they
were. :func:`node_mesh_bytes` reads a node's geometry bytes back, so that can
be checked. :func:`node_face_samples` bins a node's triangles by normal for
the mesh overhang check.

Pure stdlib.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any

from api_gateway.twin.glb_bounds import (
    _IDENTITY,
    GlbBoundsError,
    Matrix,
    _apply,
    _local,
    _mesh_boxes,
    _mul,
    envelope_fit,
    glb_bounds,
)

_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942

#: Where a label may be carried, in the order they are trusted.
LABEL_EXTRAS_KEYS: tuple[str, ...] = (
    "label",
    "semantic_label",
    "part_label",
    "category",
    "class",
)
#: Names a tool gives a part when it knows nothing about it.
_GENERIC_NAME = re.compile(
    r"^(?:tripo[_\- ]?)?(?:part|mesh|node|object|geometry|group|primitive|model|scene)?"
    r"[_\-. ]?\d*$",
    re.IGNORECASE,
)
#: The node :func:`swap_part` adds under the swapped node.
REGENERATED_NODE_NAME = "metaforge_regenerated_part"

_COMPONENT_SIZES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class GlbPartsError(ValueError):
    """The GLB cannot be read or changed as asked. The message is caller-facing."""


@dataclass(frozen=True)
class GlbPart:
    """One mesh-drawing node of a GLB."""

    name: str
    node_index: int
    mesh_index: int
    #: A label the provider gave the part, when one was found.
    label: str | None
    #: Where the label was found (``node.extras.label``, ``node_name`` ...).
    label_source: str | None
    #: World box in the file's own units and axes (glTF, +Y up).
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    primitive_count: int
    triangle_count: int | None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[float, float, float]:
        return (
            self.bbox_max[0] - self.bbox_min[0],
            self.bbox_max[1] - self.bbox_min[1],
            self.bbox_max[2] - self.bbox_min[2],
        )

    @property
    def centre(self) -> tuple[float, float, float]:
        return (
            (self.bbox_max[0] + self.bbox_min[0]) / 2,
            (self.bbox_max[1] + self.bbox_min[1]) / 2,
            (self.bbox_max[2] + self.bbox_min[2]) / 2,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "node_index": self.node_index,
            "mesh_index": self.mesh_index,
            "label": self.label,
            "label_source": self.label_source,
            "bbox_min": [round(v, 6) for v in self.bbox_min],
            "bbox_max": [round(v, 6) for v in self.bbox_max],
            "primitive_count": self.primitive_count,
            "triangle_count": self.triangle_count,
        }


@dataclass(frozen=True)
class PartsReading:
    """What a GLB says about its parts."""

    #: ``segmented`` (two or more mesh nodes) or ``not_segmented``.
    status: str
    parts: list[GlbPart]
    note: str
    #: The labels came from ``extras`` (semantic) or only from names.
    labelled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "note": self.note,
            "labelled": self.labelled,
            "parts": [p.as_dict() for p in self.parts],
        }


# ---------------------------------------------------------------------------
# Reading and writing the container
# ---------------------------------------------------------------------------


def split_glb(glb: bytes) -> tuple[dict[str, Any], bytes, int]:
    """``(json document, binary chunk, version)`` of a GLB. The binary chunk may be empty."""
    if len(glb) < 20 or glb[:4] != _MAGIC:
        raise GlbPartsError("not a GLB (no glTF header)")
    (version,) = struct.unpack_from("<I", glb, 4)
    length, kind = struct.unpack_from("<II", glb, 12)
    if kind != _JSON_CHUNK or 20 + length > len(glb):
        raise GlbPartsError("the GLB's first chunk is not its JSON")
    try:
        doc = json.loads(glb[20 : 20 + length].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GlbPartsError(f"the GLB's JSON could not be read: {exc}") from exc
    if not isinstance(doc, dict):
        raise GlbPartsError("the GLB's JSON is not an object")
    at = 20 + length
    binary = b""
    while at + 8 <= len(glb):
        clen, ctype = struct.unpack_from("<II", glb, at)
        if ctype == _BIN_CHUNK:
            binary = glb[at + 8 : at + 8 + clen]
            break
        at += 8 + clen
    return doc, binary, int(version)


def build_glb(doc: dict[str, Any], binary: bytes, version: int = 2) -> bytes:
    """A GLB from a JSON document and a binary chunk (each padded to 4 bytes)."""
    raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    out = struct.pack("<II", len(raw), _JSON_CHUNK) + raw
    if binary:
        padded = binary + b"\x00" * (-len(binary) % 4)
        out += struct.pack("<II", len(padded), _BIN_CHUNK) + padded
    return _MAGIC + struct.pack("<II", version, 12 + len(out)) + out


def _world_matrices(doc: dict[str, Any]) -> dict[int, Matrix]:
    """Each node of the default scene reached from its roots, with its world matrix."""
    nodes = doc.get("nodes") or []
    scenes = doc.get("scenes") or []
    scene_index = doc.get("scene", 0)
    if isinstance(scene_index, int) and 0 <= scene_index < len(scenes):
        roots = list(scenes[scene_index].get("nodes") or [])
    else:
        children = {c for n in nodes if isinstance(n, dict) for c in (n.get("children") or [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    out: dict[int, Matrix] = {}
    stack: list[tuple[Any, Matrix, int]] = [(r, _IDENTITY, 0) for r in roots]
    while stack:
        index, parent, depth = stack.pop()
        if not isinstance(index, int) or not 0 <= index < len(nodes) or depth > 64:
            continue
        if index in out or not isinstance(nodes[index], dict):
            continue
        world = _mul(parent, _local(nodes[index])[0])
        out[index] = world
        stack += [(c, world, depth + 1) for c in nodes[index].get("children") or []]
    return out


def _box_of(
    doc: dict[str, Any], mesh_index: int, world: Matrix
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    points = []
    for lo, hi in _mesh_boxes(doc, mesh_index):
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    points.append(_apply(world, (x, y, z)))
    if not points:
        return None
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )


def _label_from(extras: Any, where: str) -> tuple[str, str] | None:
    if not isinstance(extras, dict):
        return None
    for key in LABEL_EXTRAS_KEYS:
        value = extras.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120], f"{where}.extras.{key}"
    return None


def is_generic_name(name: str | None) -> bool:
    """A name a tool gives a part it knows nothing about (``tripo_part_2``, ``Mesh``)."""
    text = str(name or "").strip()
    return not text or bool(_GENERIC_NAME.match(text))


def _triangles(doc: dict[str, Any], mesh_index: int) -> int | None:
    meshes = doc.get("meshes") or []
    accessors = doc.get("accessors") or []
    total = 0
    for prim in meshes[mesh_index].get("primitives") or []:
        if prim.get("mode", 4) != 4:
            return None
        ref = prim.get("indices")
        if ref is None:
            ref = (prim.get("attributes") or {}).get("POSITION")
        if not isinstance(ref, int) or not 0 <= ref < len(accessors):
            return None
        total += int(accessors[ref].get("count") or 0) // 3
    return total


#: The ``extras`` key :func:`swap_part` sets on a node whose part was replaced.
REGENERATED_KEY = "metaforge_regenerated"


def _subtree(doc: dict[str, Any], index: int) -> list[int]:
    nodes = doc.get("nodes") or []
    out: list[int] = []
    stack = [index]
    while stack and len(out) < 10_000:
        i = stack.pop()
        if not isinstance(i, int) or not 0 <= i < len(nodes) or i in out:
            continue
        out.append(i)
        stack += list(nodes[i].get("children") or [])
    return out


def _union(
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    if not boxes:
        return None
    return (
        (min(b[0][0] for b in boxes), min(b[0][1] for b in boxes), min(b[0][2] for b in boxes)),
        (max(b[1][0] for b in boxes), max(b[1][1] for b in boxes), max(b[1][2] for b in boxes)),
    )


def read_parts(glb: bytes) -> PartsReading:
    """The parts a GLB draws, read defensively (see the module docstring).

    A node whose part was swapped (:func:`swap_part`) is one part: its box is
    that of everything under it, and the nodes under it are not parts.
    """
    doc, _binary, _version = split_glb(glb)
    nodes = doc.get("nodes") or []
    meshes = doc.get("meshes") or []
    worlds = _world_matrices(doc)
    swapped: dict[int, list[int]] = {}
    for index in worlds:
        extras = nodes[index].get("extras")
        if isinstance(extras, dict) and extras.get(REGENERATED_KEY):
            swapped[index] = _subtree(doc, index)
    inside = {i for idx, tree in swapped.items() for i in tree if i != idx}
    parts: list[GlbPart] = []
    used: set[str] = set()
    labelled = False
    for index in sorted(worlds):
        if index in inside:
            continue
        node = nodes[index]
        mesh_index = node.get("mesh")
        if index in swapped:
            box = _union(
                [
                    b
                    for i in swapped[index]
                    if isinstance(nodes[i].get("mesh"), int) and i in worlds
                    for b in [_box_of(doc, nodes[i]["mesh"], worlds[i])]
                    if b is not None
                ]
            )
            if box is None:
                continue
            mesh = {}
            mesh_index = -1
        else:
            if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes):
                continue
            mesh = meshes[mesh_index] if isinstance(meshes[mesh_index], dict) else {}
            box = _box_of(doc, mesh_index, worlds[index])
            if box is None:
                continue
        node_name = str(node.get("name") or "").strip()
        mesh_name = str(mesh.get("name") or "").strip()
        name = node_name or mesh_name or f"part_{len(parts)}"
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        found = _label_from(node.get("extras"), "node") or _label_from(mesh.get("extras"), "mesh")
        if found is not None:
            labelled = True
        elif node_name and not is_generic_name(node_name):
            found = (node_name, "node_name")
        elif mesh_name and not is_generic_name(mesh_name):
            found = (mesh_name, "mesh_name")
        parts.append(
            GlbPart(
                name=name,
                node_index=index,
                mesh_index=mesh_index,
                label=found[0] if found else None,
                label_source=found[1] if found else None,
                bbox_min=box[0],
                bbox_max=box[1],
                primitive_count=len(mesh.get("primitives") or []),
                triangle_count=_triangles(doc, mesh_index) if mesh_index >= 0 else None,
                extras=dict(node.get("extras") or {}) if isinstance(node.get("extras"), dict) else {},
            )
        )
    if not parts:
        raise GlbPartsError("the GLB draws no mesh with readable bounds")
    if len(parts) >= 2:
        return PartsReading(
            status="segmented",
            parts=parts,
            note=f"{len(parts)} parts, one node each",
            labelled=labelled,
        )
    only = parts[0]
    if only.primitive_count > 1:
        note = (
            f"one mesh with {only.primitive_count} primitives and no part nodes: the "
            "parts were not returned as separate nodes, so none can be addressed by name"
        )
    else:
        note = "one mesh, no part nodes: the model came back whole"
    return PartsReading(status="not_segmented", parts=parts, note=note, labelled=labelled)


def set_node_extras(glb: bytes, updates: dict[int, dict[str, Any]]) -> bytes:
    """``glb`` with ``extras`` keys set on nodes (a ``None`` value removes the key).

    Only the JSON chunk changes; the binary chunk is copied byte for byte.
    """
    doc, binary, version = split_glb(glb)
    nodes = doc.get("nodes") or []
    for index, fields in updates.items():
        if not 0 <= index < len(nodes) or not isinstance(nodes[index], dict):
            raise GlbPartsError(f"the GLB has no node {index}")
        extras = dict(nodes[index].get("extras") or {})
        for key, value in fields.items():
            if value is None:
                extras.pop(key, None)
            else:
                extras[key] = value
        if extras:
            nodes[index]["extras"] = extras
        else:
            nodes[index].pop("extras", None)
    return build_glb(doc, binary, version)


# ---------------------------------------------------------------------------
# Geometry bytes and triangles of one node
# ---------------------------------------------------------------------------


def _accessor_bytes(doc: dict[str, Any], binary: bytes, index: Any) -> bytes:
    accessors = doc.get("accessors") or []
    views = doc.get("bufferViews") or []
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        return b""
    acc = accessors[index]
    view_index = acc.get("bufferView")
    if not isinstance(view_index, int) or not 0 <= view_index < len(views):
        return b""
    view = views[view_index]
    elem = _COMPONENT_SIZES.get(int(acc.get("componentType") or 0), 0) * _TYPE_COUNTS.get(
        str(acc.get("type") or ""), 0
    )
    count = int(acc.get("count") or 0)
    start = int(view.get("byteOffset") or 0) + int(acc.get("byteOffset") or 0)
    stride = int(view.get("byteStride") or 0)
    if not elem:
        return b""
    if not stride or stride == elem:
        return binary[start : start + elem * count]
    return b"".join(binary[start + i * stride : start + i * stride + elem] for i in range(count))


def node_mesh_bytes(glb: bytes, node_index: int) -> bytes:
    """Every accessor byte the node's own mesh draws with, in a fixed order.

    Two files whose node gives the same bytes draw the same geometry there.
    """
    doc, binary, _version = split_glb(glb)
    nodes = doc.get("nodes") or []
    if not 0 <= node_index < len(nodes):
        raise GlbPartsError(f"the GLB has no node {node_index}")
    mesh_index = nodes[node_index].get("mesh")
    meshes = doc.get("meshes") or []
    if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes):
        return b""
    out = []
    for prim in meshes[mesh_index].get("primitives") or []:
        for _name, ref in sorted((prim.get("attributes") or {}).items()):
            out.append(_accessor_bytes(doc, binary, ref))
        out.append(_accessor_bytes(doc, binary, prim.get("indices")))
    return b"".join(out)


def _read_floats(doc: dict[str, Any], binary: bytes, index: Any) -> list[tuple[float, ...]]:
    accessors = doc.get("accessors") or []
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        return []
    acc = accessors[index]
    if int(acc.get("componentType") or 0) != 5126 or acc.get("type") != "VEC3":
        return []
    raw = _accessor_bytes(doc, binary, index)
    count = len(raw) // 12
    flat = struct.unpack_from(f"<{count * 3}f", raw) if count else ()
    return [tuple(flat[i * 3 : i * 3 + 3]) for i in range(count)]


def _read_indices(doc: dict[str, Any], binary: bytes, index: Any) -> list[int] | None:
    accessors = doc.get("accessors") or []
    if not isinstance(index, int) or not 0 <= index < len(accessors):
        return None
    acc = accessors[index]
    fmt = {5121: "B", 5123: "H", 5125: "I"}.get(int(acc.get("componentType") or 0))
    if fmt is None:
        return None
    raw = _accessor_bytes(doc, binary, index)
    size = struct.calcsize(fmt)
    count = len(raw) // size
    return list(struct.unpack_from(f"<{count}{fmt}", raw)) if count else []


def node_face_samples(
    glb: bytes, node_index: int, *, max_triangles: int = 200_000, bins: int = 12
) -> list[dict[str, Any]]:
    """A node's triangles as area-weighted normal bins, in world axes with Z up.

    glTF is Y up; a normal ``(x, y, z)`` is returned as ``(x, -z, y)`` so the
    build axis is +Z as in the DFM rules. Each bin is one sample: its mean
    normal and the summed area in the file's units squared, times 1e6 (square
    millimetres when the file is in metres). Empty when the node's geometry
    cannot be read (compressed, quantised or not triangles).
    """
    doc, binary, _version = split_glb(glb)
    nodes = doc.get("nodes") or []
    if not 0 <= node_index < len(nodes):
        raise GlbPartsError(f"the GLB has no node {node_index}")
    meshes = doc.get("meshes") or []
    worlds = _world_matrices(doc)
    extras = nodes[node_index].get("extras")
    members = (
        _subtree(doc, node_index)
        if isinstance(extras, dict) and extras.get(REGENERATED_KEY)
        else [node_index]
    )
    drawn = [
        (nodes[i]["mesh"], worlds.get(i, _IDENTITY))
        for i in members
        if isinstance(nodes[i].get("mesh"), int) and 0 <= nodes[i]["mesh"] < len(meshes)
    ]
    acc: dict[tuple[int, int], list[float]] = {}
    seen = 0
    prims = [(p, world) for mesh_index, world in drawn for p in meshes[mesh_index].get("primitives") or []]
    for prim, world in prims:
        if prim.get("mode", 4) != 4:
            continue
        positions = _read_floats(doc, binary, (prim.get("attributes") or {}).get("POSITION"))
        if not positions:
            continue
        pts = [_apply(world, (p[0], p[1], p[2])) for p in positions]
        idx = _read_indices(doc, binary, prim.get("indices"))
        if idx is None:
            if prim.get("indices") is not None:
                continue
            idx = list(range(len(pts)))
        for t in range(0, len(idx) - 2, 3):
            if seen >= max_triangles:
                break
            a, b, c = idx[t], idx[t + 1], idx[t + 2]
            if max(a, b, c) >= len(pts):
                continue
            pa, pb, pc = pts[a], pts[b], pts[c]
            u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
            v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
            if length <= 0:
                continue
            seen += 1
            nz = (n[0] / length, -n[2] / length, n[1] / length)
            # Bin by elevation (from -90 to 90 degrees) and azimuth.
            elev = math.degrees(math.asin(max(-1.0, min(1.0, nz[2]))))
            azim = math.degrees(math.atan2(nz[1], nz[0])) % 360
            key = (min(bins - 1, int((elev + 90) / 180 * bins)), int(azim // 90))
            slot = acc.setdefault(key, [0.0, 0.0, 0.0, 0.0])
            area = length / 2
            slot[0] += nz[0] * area
            slot[1] += nz[1] * area
            slot[2] += nz[2] * area
            slot[3] += area
    out: list[dict[str, Any]] = []
    for (e, a), (x, y, z, area) in sorted(acc.items()):
        norm = math.sqrt(x * x + y * y + z * z)
        if norm <= 0 or area <= 0:
            continue
        out.append(
            {
                "face_id": f"bin-{e}-{a}",
                "normal": [round(x / norm, 6), round(y / norm, 6), round(z / norm, 6)],
                "area_mm2": round(area * 1e6, 3),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Swapping what one node draws
# ---------------------------------------------------------------------------


def _local_mesh_box(
    doc: dict[str, Any], mesh_index: int
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    return _box_of(doc, mesh_index, _IDENTITY)


def _matrix_trs(
    translation: tuple[float, float, float],
    quarter_turn_y: bool,
    scale: float,
    pre_translation: tuple[float, float, float],
) -> Matrix:
    """``T(translation) * R * S(scale) * T(pre_translation)``, column-major."""
    if quarter_turn_y:
        # +90 degrees about Y: x -> -z, z -> x.
        r = [0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0]
    else:
        r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    rs: Matrix = [
        r[0] * scale, r[1] * scale, r[2] * scale, 0.0,
        r[3] * scale, r[4] * scale, r[5] * scale, 0.0,
        r[6] * scale, r[7] * scale, r[8] * scale, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]  # fmt: skip
    pre: Matrix = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        pre_translation[0], pre_translation[1], pre_translation[2], 1.0,
    ]  # fmt: skip
    m = _mul(rs, pre)
    m[12] += translation[0]
    m[13] += translation[1]
    m[14] += translation[2]
    return m


def _shift(value: Any, by: int) -> Any:
    return value + by if isinstance(value, int) and not isinstance(value, bool) else value


def _shift_textures(material: dict[str, Any], by: int) -> None:
    def fix(info: Any) -> None:
        if isinstance(info, dict) and isinstance(info.get("index"), int):
            info["index"] += by

    pbr = material.get("pbrMetallicRoughness")
    if isinstance(pbr, dict):
        fix(pbr.get("baseColorTexture"))
        fix(pbr.get("metallicRoughnessTexture"))
    for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
        fix(material.get(key))


def swap_part(base: bytes, node_index: int, part: bytes) -> tuple[bytes, dict[str, Any]]:
    """``base`` with node ``node_index`` drawing ``part``'s scene, fitted to its own box.

    The node keeps its name, ``extras``, transform and children; only its
    ``mesh`` is dropped, and a child node (:data:`REGENERATED_NODE_NAME`)
    holds ``part``'s scene, scaled uniformly (the envelope rule of
    :func:`envelope_fit`, the node's box standing for the envelope),
    turned a quarter about +Y when that pairs the axes better, and
    centred on the node's box. ``part``'s binary data is appended after
    ``base``'s, so no other node's bytes move. The replaced mesh stays in the
    file, unreferenced by this node.

    Returns ``(glb, record)`` where the record carries the fit.
    """
    doc, binary, version = split_glb(base)
    pdoc, pbinary, _pversion = split_glb(part)
    buffers = doc.get("buffers") or []
    if len(buffers) > 1 or any(isinstance(b, dict) and b.get("uri") for b in buffers):
        raise GlbPartsError("the stored model refers to external buffers; it cannot be edited")
    if any(isinstance(b, dict) and b.get("uri") for b in pdoc.get("buffers") or []):
        raise GlbPartsError("the new part refers to external buffers")
    nodes = doc.setdefault("nodes", [])
    if not 0 <= node_index < len(nodes) or not isinstance(nodes[node_index], dict):
        raise GlbPartsError(f"the stored model has no node {node_index}")
    target = nodes[node_index]
    mesh_index = target.get("mesh")
    prior = (target.get("extras") or {}).get(REGENERATED_KEY)
    box: tuple[tuple[float, float, float], tuple[float, float, float]] | None
    if isinstance(prior, dict) and isinstance(prior.get("target_box_local"), list):
        # Swapped before: fit to the box the original part had, and drop the
        # previous replacement from under the node.
        lo_hi = prior["target_box_local"]
        box = (
            (float(lo_hi[0][0]), float(lo_hi[0][1]), float(lo_hi[0][2])),
            (float(lo_hi[1][0]), float(lo_hi[1][1]), float(lo_hi[1][2])),
        )
        target["children"] = [
            c
            for c in target.get("children") or []
            if not (
                isinstance(c, int)
                and 0 <= c < len(nodes)
                and nodes[c].get("name") == REGENERATED_NODE_NAME
            )
        ]
        if not target["children"]:
            target.pop("children")
    elif isinstance(mesh_index, int):
        box = _local_mesh_box(doc, mesh_index)
    else:
        raise GlbPartsError(f"node {node_index} draws no mesh to replace")
    if box is None:
        raise GlbPartsError(f"node {node_index}'s mesh has no readable bounds")
    try:
        new_bounds = glb_bounds(part)
    except GlbBoundsError as exc:
        raise GlbPartsError(f"the new part's bounds could not be read: {exc}") from exc

    lo, hi = box
    target_size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    target_centre = ((hi[0] + lo[0]) / 2, (hi[1] + lo[1]) / 2, (hi[2] + lo[2]) / 2)
    part_size = new_bounds.size
    part_centre = (
        (new_bounds.max[0] + new_bounds.min[0]) / 2,
        (new_bounds.max[1] + new_bounds.min[1]) / 2,
        (new_bounds.max[2] + new_bounds.min[2]) / 2,
    )
    record: dict[str, Any] = {
        "target_bbox_size": [round(v, 6) for v in target_size],
        "part_bbox_size_before": [round(v, 6) for v in part_size],
    }
    # The envelope is (width, depth, height) in mm: glTF x, z and y.
    envelope = (target_size[0] * 1000, target_size[2] * 1000, target_size[1] * 1000)
    try:
        fitted = envelope_fit(part_size, envelope)
        factor = float(fitted["scale_factor"])
        mapping = str(fitted["axis_mapping"])
        record["proportion_error_pct"] = fitted["proportion_error_pct"]
        record["scale_method"] = "uniform, least squares of the log ratio per axis"
    except ValueError as exc:
        largest_part = max(part_size)
        largest_target = max(target_size)
        if not largest_part > 0 or not largest_target > 0:
            raise GlbPartsError(f"the new part cannot be fitted: {exc}") from exc
        factor = largest_target / largest_part
        mapping = "x=width, z=depth"
        record["scale_method"] = f"largest axis matched ({exc})"
    turn = mapping != "x=width, z=depth"
    record.update(
        {
            "scale_factor": round(factor, 9),
            "axis_mapping": mapping,
            "quarter_turn_about_y": turn,
            "fit": "the node's own box: size and centre kept, pose kept",
        }
    )
    matrix = _matrix_trs(
        target_centre, turn, factor, (-part_centre[0], -part_centre[1], -part_centre[2])
    )

    # Merge the part's document after the base's own arrays.
    offset = len(binary) + (-len(binary) % 8)
    merged_bin = binary + b"\x00" * (offset - len(binary)) + pbinary
    counts = {
        key: len(doc.get(key) or [])
        for key in (
            "bufferViews",
            "accessors",
            "images",
            "samplers",
            "textures",
            "materials",
            "meshes",
            "nodes",
        )
    }
    for view in pdoc.get("bufferViews") or []:
        view = dict(view)
        view["buffer"] = 0
        view["byteOffset"] = int(view.get("byteOffset") or 0) + offset
        doc.setdefault("bufferViews", []).append(view)
    for acc in pdoc.get("accessors") or []:
        acc = dict(acc)
        acc["bufferView"] = _shift(acc.get("bufferView"), counts["bufferViews"])
        sparse = acc.get("sparse")
        if isinstance(sparse, dict):
            sparse = json.loads(json.dumps(sparse))
            for sub in ("indices", "values"):
                if isinstance(sparse.get(sub), dict):
                    sparse[sub]["bufferView"] = _shift(
                        sparse[sub].get("bufferView"), counts["bufferViews"]
                    )
            acc["sparse"] = sparse
        doc.setdefault("accessors", []).append(acc)
    for image in pdoc.get("images") or []:
        image = dict(image)
        if "bufferView" in image:
            image["bufferView"] = _shift(image["bufferView"], counts["bufferViews"])
        doc.setdefault("images", []).append(image)
    for sampler in pdoc.get("samplers") or []:
        doc.setdefault("samplers", []).append(dict(sampler))
    for texture in pdoc.get("textures") or []:
        texture = dict(texture)
        texture["source"] = _shift(texture.get("source"), counts["images"])
        texture["sampler"] = _shift(texture.get("sampler"), counts["samplers"])
        doc.setdefault("textures", []).append(texture)
    for material in pdoc.get("materials") or []:
        material = json.loads(json.dumps(material))
        _shift_textures(material, counts["textures"])
        doc.setdefault("materials", []).append(material)
    for mesh in pdoc.get("meshes") or []:
        mesh = json.loads(json.dumps(mesh))
        for prim in mesh.get("primitives") or []:
            prim["attributes"] = {
                k: _shift(v, counts["accessors"]) for k, v in (prim.get("attributes") or {}).items()
            }
            if "indices" in prim:
                prim["indices"] = _shift(prim["indices"], counts["accessors"])
            if "material" in prim:
                prim["material"] = _shift(prim["material"], counts["materials"])
            for tgt in prim.get("targets") or []:
                for k in list(tgt):
                    tgt[k] = _shift(tgt[k], counts["accessors"])
            draco = (prim.get("extensions") or {}).get("KHR_draco_mesh_compression")
            if isinstance(draco, dict):
                draco["bufferView"] = _shift(draco.get("bufferView"), counts["bufferViews"])
                draco["attributes"] = dict(draco.get("attributes") or {})
        doc.setdefault("meshes", []).append(mesh)
    part_nodes = pdoc.get("nodes") or []
    for pnode in part_nodes:
        pnode = json.loads(json.dumps(pnode))
        pnode.pop("skin", None)
        pnode.pop("camera", None)
        if "mesh" in pnode:
            pnode["mesh"] = _shift(pnode["mesh"], counts["meshes"])
        pnode["children"] = [_shift(c, counts["nodes"]) for c in pnode.get("children") or []]
        if not pnode["children"]:
            pnode.pop("children")
        nodes.append(pnode)
    pscenes = pdoc.get("scenes") or []
    pscene = pdoc.get("scene", 0)
    if isinstance(pscene, int) and 0 <= pscene < len(pscenes):
        part_roots = list(pscenes[pscene].get("nodes") or [])
    else:
        kids = {c for n in part_nodes if isinstance(n, dict) for c in (n.get("children") or [])}
        part_roots = [i for i in range(len(part_nodes)) if i not in kids]
    holder = {
        "name": REGENERATED_NODE_NAME,
        "matrix": [round(v, 12) for v in matrix],
        "children": [r + counts["nodes"] for r in part_roots if isinstance(r, int)],
    }
    nodes.append(holder)
    target.pop("mesh", None)
    target["children"] = [*list(target.get("children") or []), len(nodes) - 1]
    extras = dict(target.get("extras") or {})
    extras[REGENERATED_KEY] = {
        "target_box_local": [[round(v, 9) for v in lo], [round(v, 9) for v in hi]],
        "scale_factor": record["scale_factor"],
        "axis_mapping": mapping,
        "quarter_turn_about_y": turn,
    }
    target["extras"] = extras
    for key in ("extensionsUsed", "extensionsRequired"):
        names = list(doc.get(key) or []) + [
            e for e in pdoc.get(key) or [] if e not in (doc.get(key) or [])
        ]
        if names:
            doc[key] = names
    if not doc.get("buffers"):
        doc["buffers"] = [{}]
    doc["buffers"][0]["byteLength"] = len(merged_bin) + (-len(merged_bin) % 4)
    record["replaced_mesh_index"] = mesh_index if isinstance(mesh_index, int) else None
    return build_glb(doc, merged_bin, version), record


def subset_glb(glb: bytes, root: int) -> bytes:
    """A GLB of node ``root`` and everything under it, and only what they use.

    The root keeps its own transform; buffer views are copied byte for byte
    into a new binary chunk in the order they are first used.
    """
    doc, binary, version = split_glb(glb)
    nodes = doc.get("nodes") or []
    if not 0 <= root < len(nodes):
        raise GlbPartsError(f"the GLB has no node {root}")
    keep_nodes = _subtree(doc, root)
    node_map = {old: new for new, old in enumerate(keep_nodes)}
    meshes = doc.get("meshes") or []
    mesh_map: dict[int, int] = {}
    acc_map: dict[int, int] = {}
    view_map: dict[int, int] = {}
    mat_map: dict[int, int] = {}
    tex_map: dict[int, int] = {}
    img_map: dict[int, int] = {}
    smp_map: dict[int, int] = {}
    out: dict[str, Any] = {
        "asset": doc.get("asset") or {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [],
        "meshes": [],
        "accessors": [],
        "bufferViews": [],
        "materials": [],
        "textures": [],
        "images": [],
        "samplers": [],
    }
    chunks: list[bytes] = []
    size = 0

    def view(index: Any) -> Any:
        nonlocal size
        views = doc.get("bufferViews") or []
        if not isinstance(index, int) or not 0 <= index < len(views):
            return index
        if index not in view_map:
            v = dict(views[index])
            start = int(v.get("byteOffset") or 0)
            data = binary[start : start + int(v.get("byteLength") or 0)]
            pad = -size % 8
            chunks.append(b"\x00" * pad)
            size += pad
            v["buffer"] = 0
            v["byteOffset"] = size
            chunks.append(data)
            size += len(data)
            view_map[index] = len(out["bufferViews"])
            out["bufferViews"].append(v)
        return view_map[index]

    def accessor(index: Any) -> Any:
        accessors = doc.get("accessors") or []
        if not isinstance(index, int) or not 0 <= index < len(accessors):
            return index
        if index not in acc_map:
            a = json.loads(json.dumps(accessors[index]))
            if "bufferView" in a:
                a["bufferView"] = view(a["bufferView"])
            acc_map[index] = len(out["accessors"])
            out["accessors"].append(a)
        return acc_map[index]

    def image(index: Any) -> Any:
        images = doc.get("images") or []
        if not isinstance(index, int) or not 0 <= index < len(images):
            return index
        if index not in img_map:
            im = dict(images[index])
            if "bufferView" in im:
                im["bufferView"] = view(im["bufferView"])
            img_map[index] = len(out["images"])
            out["images"].append(im)
        return img_map[index]

    def sampler(index: Any) -> Any:
        samplers = doc.get("samplers") or []
        if not isinstance(index, int) or not 0 <= index < len(samplers):
            return index
        if index not in smp_map:
            smp_map[index] = len(out["samplers"])
            out["samplers"].append(dict(samplers[index]))
        return smp_map[index]

    def texture(index: Any) -> Any:
        textures = doc.get("textures") or []
        if not isinstance(index, int) or not 0 <= index < len(textures):
            return index
        if index not in tex_map:
            t = dict(textures[index])
            if "source" in t:
                t["source"] = image(t["source"])
            if "sampler" in t:
                t["sampler"] = sampler(t["sampler"])
            tex_map[index] = len(out["textures"])
            out["textures"].append(t)
        return tex_map[index]

    def material(index: Any) -> Any:
        materials = doc.get("materials") or []
        if not isinstance(index, int) or not 0 <= index < len(materials):
            return index
        if index not in mat_map:
            m = json.loads(json.dumps(materials[index]))
            infos = [m.get(k) for k in ("normalTexture", "occlusionTexture", "emissiveTexture")]
            pbr = m.get("pbrMetallicRoughness")
            if isinstance(pbr, dict):
                infos += [pbr.get("baseColorTexture"), pbr.get("metallicRoughnessTexture")]
            for info in infos:
                if isinstance(info, dict) and isinstance(info.get("index"), int):
                    info["index"] = texture(info["index"])
            mat_map[index] = len(out["materials"])
            out["materials"].append(m)
        return mat_map[index]

    for old in keep_nodes:
        n = json.loads(json.dumps(nodes[old]))
        n.pop("skin", None)
        n.pop("camera", None)
        mesh_index = n.get("mesh")
        if isinstance(mesh_index, int) and 0 <= mesh_index < len(meshes):
            if mesh_index not in mesh_map:
                m = json.loads(json.dumps(meshes[mesh_index]))
                for prim in m.get("primitives") or []:
                    prim["attributes"] = {
                        k: accessor(v) for k, v in (prim.get("attributes") or {}).items()
                    }
                    if "indices" in prim:
                        prim["indices"] = accessor(prim["indices"])
                    if "material" in prim:
                        prim["material"] = material(prim["material"])
                    for tgt in prim.get("targets") or []:
                        for k in list(tgt):
                            tgt[k] = accessor(tgt[k])
                    draco = (prim.get("extensions") or {}).get("KHR_draco_mesh_compression")
                    if isinstance(draco, dict):
                        draco["bufferView"] = view(draco.get("bufferView"))
                mesh_map[mesh_index] = len(out["meshes"])
                out["meshes"].append(m)
            n["mesh"] = mesh_map[mesh_index]
        else:
            n.pop("mesh", None)
        kids = [node_map[c] for c in n.get("children") or [] if c in node_map]
        if kids:
            n["children"] = kids
        else:
            n.pop("children", None)
        out["nodes"].append(n)
    for key in ("extensionsUsed", "extensionsRequired"):
        if doc.get(key):
            out[key] = list(doc[key])
    new_binary = b"".join(chunks)
    out["buffers"] = [{"byteLength": len(new_binary) + (-len(new_binary) % 4)}]
    for key in ("meshes", "accessors", "bufferViews", "materials", "textures", "images", "samplers"):
        if not out[key]:
            out.pop(key)
    return build_glb(out, new_binary, version)


__all__ = [
    "LABEL_EXTRAS_KEYS",
    "REGENERATED_KEY",
    "REGENERATED_NODE_NAME",
    "subset_glb",
    "GlbPart",
    "GlbPartsError",
    "PartsReading",
    "build_glb",
    "is_generic_name",
    "node_face_samples",
    "node_mesh_bytes",
    "read_parts",
    "set_node_extras",
    "split_glb",
    "swap_part",
]
