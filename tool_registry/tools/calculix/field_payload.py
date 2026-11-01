"""Result fields for the 3D viewer: parse a CalculiX ``.frd`` into mesh +
nodal fields and build a compact surface payload (FORGE-532).

``result_parser.parse_frd_file`` only keeps per-node *scalars* (von Mises,
displacement magnitude, temperature) and never reads the mesh itself, so a
contour plot was impossible: nothing downstream knew where a node was or
which nodes formed a face. This module reads the rest of the ``.frd``:

- the ``2C`` node block (coordinates),
- the ``3C`` element block (type + connectivity, ``-2`` continuation lines),
- the ``DISP`` / ``STRESS`` / ``NDTEMP`` result blocks (all components, so
  displacement keeps its vector and von Mises is computed from the six
  stress components when no ready-made equivalent is in the file).

From that it extracts the *outer surface* of the volume mesh (faces used by
exactly one element), triangulates it on corner nodes, and writes the
**viewer payload**: gzipped JSON, schema ``metaforge.sim_field`` v1::

    {
      "format": "metaforge.sim_field", "version": 1,
      "analysis_type": "static_stress" | "modal" | "thermal",
      "units": {"length": "mm", ...},
      "positions": [x0, y0, z0, x1, ...],       # surface vertices, flat
      "indices":   [a0, b0, c0, a1, ...],       # triangles, outward wound
      "displacement": [dx0, dy0, dz0, ...] | null,
      "fields": {"von_mises": {"label", "unit", "values", "min", "max",
                               "peak": {"position", "value"}}, ...},
      "markers": [{"kind": "fixture" | "load" | "heat_source" | "sink", ...}],
      "bbox": {"min": [...], "max": [...]},
      "decimation": {"applied", "source_triangle_count", "cell_size_mm"},
    }

Why gzipped JSON and not glTF: the viewer needs per-vertex custom scalars
(several selectable quantities) plus a displacement vector, and markers
that are not geometry at all. glTF can carry custom ``_ATTRIBUTES`` but
the adapter container has no glTF writer, the dashboard would need a
loader path for non-standard attributes, and the markers/units would end
up in ``extras`` anyway. Gzipped JSON is stdlib on both ends (the gateway
serves it with ``Content-Encoding: gzip`` so the browser inflates it for
free) and, after rounding to 4-5 significant digits, compresses to roughly
the same size as a quantised binary buffer for meshes this size.

Size is capped (``DEFAULT_MAX_BYTES`` compressed): over the cap the surface
is decimated by vertex clustering on a coarsening grid until it fits, and
the payload says so (``decimation.applied``). Field min/max/peak are always
taken from the FULL nodal field before any decimation, so the legend and
the probe report the real peak, not a smoothed one.
"""

from __future__ import annotations

import base64
import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.calculix.field_payload")

PAYLOAD_FORMAT = "metaforge.sim_field"
PAYLOAD_VERSION = 1
#: MIME type the gateway serves the (inflated) payload as.
PAYLOAD_MEDIA_TYPE = "application/vnd.metaforge.sim-field+json"
#: Compressed size cap. At the measured ~25 bytes per surface vertex
#: (stress + displacement), about 60k vertices fit before decimation.
DEFAULT_MAX_BYTES = 1_500_000
#: Hard ceiling on triangles fed to the encoder at all, so a pathological
#: mesh is decimated before we spend time serialising it.
_MAX_TRIANGLES_BEFORE_ENCODE = 250_000
_MAX_DECIMATION_PASSES = 12


class FieldPayloadError(Exception):
    """Raised when an ``.frd`` has no usable mesh or field to build a payload from."""


# frd element type code -> (name, corner node count, faces over corner indices).
# Winding in these tables does not matter: every face is re-oriented outward
# against its element's centroid in ``extract_surface``.
_HEX_FACES = ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
_WEDGE_FACES = ((0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5))
_TET_FACES = ((0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3))
_VOLUME_TYPES: dict[int, tuple[str, int, tuple[tuple[int, ...], ...]]] = {
    1: ("he8", 8, _HEX_FACES),
    4: ("he20", 8, _HEX_FACES),
    2: ("pe6", 6, _WEDGE_FACES),
    5: ("pe15", 6, _WEDGE_FACES),
    3: ("te4", 4, _TET_FACES),
    6: ("te10", 4, _TET_FACES),
}
# Shell / 2D elements are already surfaces: (corner count).
_SURFACE_TYPES: dict[int, int] = {7: 3, 8: 3, 9: 4, 10: 4}

# Abaqus/CalculiX .inp element type -> frd type code, for the fallback when
# an .frd carries no 3C block and the mesh comes from the .inp instead.
_INP_TYPE_TO_FRD: dict[str, int] = {
    "C3D8": 1,
    "C3D8R": 1,
    "C3D8I": 1,
    "C3D20": 4,
    "C3D20R": 4,
    "C3D6": 2,
    "C3D15": 5,
    "C3D4": 3,
    "C3D10": 6,
    "C3D10T": 6,
    "S3": 7,
    "CPS3": 7,
    "S6": 8,
    "CPS6": 8,
    "S4": 9,
    "S4R": 9,
    "CPS4": 9,
    "S8": 10,
    "S8R": 10,
    "CPS8": 10,
}


@dataclass
class FrdResultBlock:
    """One ``-4`` result block (e.g. ``DISP``/``STRESS``/``NDTEMP``)."""

    name: str
    components: list[str]
    values: dict[int, list[float]] = field(default_factory=dict)


@dataclass
class FrdModel:
    """Mesh + every result block of one ``.frd`` file, in file order."""

    nodes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    # element id -> (frd type code, node ids)
    elements: dict[int, tuple[int, list[int]]] = field(default_factory=dict)
    blocks: list[FrdResultBlock] = field(default_factory=list)

    def blocks_named(self, name: str) -> list[FrdResultBlock]:
        return [b for b in self.blocks if b.name == name]


def _fixed_floats(text: str, width: int = 12) -> list[float]:
    out: list[float] = []
    for i in range(0, len(text), width):
        chunk = text[i : i + width].strip()
        if chunk:
            out.append(float(chunk))
    return out


def _fixed_ints(text: str, width: int = 10) -> list[int]:
    out: list[int] = []
    for i in range(0, len(text), width):
        chunk = text[i : i + width].strip()
        if chunk:
            out.append(int(chunk))
    return out


def parse_frd_model(frd_path: str) -> FrdModel:
    """Parse nodes, elements and all result blocks from an ASCII ``.frd``.

    Format (ccx 2.x ASCII, "short" layout)::

            2C                            12                     1
         -1         1 0.00000E+00 0.00000E+00 0.00000E+00
         -3
            3C                             2                     1
         -1         1    1    0    1          <- id, type, group, material
         -2         1         2  ...          <- node ids, 10 wide, may wrap
         -3
          100CL  101 1.00000000         12                     0    1  1
         -4  DISP        4    1
         -5  D1          1    2    1    0
         -1         1 1.00000E-03 ...         <- node id, 12-wide values
         -3

    Values are fixed-width (12 chars) and can touch (``-1.0E+00-2.0E+00``),
    so they are sliced, never ``split()``. Raises ``FileNotFoundError`` for
    a missing file and :class:`FieldPayloadError` if it holds no nodes.
    """
    path = Path(frd_path)
    if not path.exists():
        raise FileNotFoundError(f"FRD file not found: {frd_path}")

    model = FrdModel()
    section: str | None = None  # "nodes" | "elements" | "result"
    block: FrdResultBlock | None = None
    current_element: tuple[int, int] | None = None  # (element id, type)
    current_conn: list[int] = []

    def _flush_element() -> None:
        nonlocal current_element, current_conn
        if current_element is not None:
            model.elements[current_element[0]] = (current_element[1], current_conn)
        current_element = None
        current_conn = []

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            head = stripped.split(maxsplit=1)[0]
            if head == "2C":
                section, block = "nodes", None
                continue
            if head == "3C":
                section, block = "elements", None
                continue
            if head == "-4":
                parts = stripped.split()
                name = parts[1].upper() if len(parts) > 1 else ""
                block = FrdResultBlock(name=name, components=[])
                model.blocks.append(block)
                section = "result"
                continue
            if head == "-5" and section == "result" and block is not None:
                parts = stripped.split()
                if len(parts) > 1:
                    block.components.append(parts[1].upper())
                continue
            if head == "-3":
                if section == "elements":
                    _flush_element()
                section, block = None, None
                continue
            if head == "-1" and section is not None:
                body = line[3:]
                try:
                    if section == "nodes":
                        node_id = int(body[:10])
                        xyz = _fixed_floats(body[10:])
                        if len(xyz) >= 3:
                            model.nodes[node_id] = (xyz[0], xyz[1], xyz[2])
                    elif section == "elements":
                        _flush_element()
                        ints = [int(v) for v in body.split()]
                        if len(ints) >= 2:
                            current_element = (ints[0], ints[1])
                    elif section == "result" and block is not None:
                        node_id = int(body[:10])
                        block.values[node_id] = _fixed_floats(body[10:])
                except ValueError:
                    logger.debug("frd_line_skipped", line=stripped[:80])
                continue
            if head == "-2" and section == "elements" and current_element is not None:
                try:
                    current_conn.extend(_fixed_ints(line[3:]))
                except ValueError:
                    logger.debug("frd_line_skipped", line=stripped[:80])
                continue
            # Anything else (1C/1U headers, 100CL step lines, 9999) ends
            # nothing by itself; a result block only ever ends at -3.
            if head.startswith("100C") or head == "9999":
                section, block = None, None
    _flush_element()

    if not model.nodes:
        raise FieldPayloadError(f"{frd_path}: no 2C node block -- nothing to draw")
    return model


def elements_from_inp_mesh(mesh: Any) -> dict[int, tuple[int, list[int]]]:
    """Convert an ``inp_mesh.MeshData``'s elements into frd-coded elements
    (fallback for an ``.frd`` written without a ``3C`` block)."""
    out: dict[int, tuple[int, list[int]]] = {}
    for element_id, (etype, node_ids) in mesh.elements.items():
        code = _INP_TYPE_TO_FRD.get(str(etype).upper())
        if code is not None:
            out[element_id] = (code, list(node_ids))
    return out


def von_mises(components: list[float]) -> float:
    """Von Mises equivalent from (SXX, SYY, SZZ, SXY, SYZ, SZX).

    The shear order does not matter: the formula is symmetric in the three
    shear terms.
    """
    sxx, syy, szz, s1, s2, s3 = components[:6]
    term1 = (sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
    term2 = 6.0 * (s1 * s1 + s2 * s2 + s3 * s3)
    return math.sqrt((term1 + term2) / 2.0)


def _pick_block(model: FrdModel, name: str, *, first: bool) -> FrdResultBlock | None:
    blocks = [b for b in model.blocks_named(name) if b.values]
    if not blocks:
        return None
    return blocks[0] if first else blocks[-1]


def nodal_fields(model: FrdModel, analysis_type: str) -> dict[str, Any]:
    """Per-node fields keyed by quantity.

    Returns ``{"displacement": {nid: (dx, dy, dz)}, "von_mises": {nid: v},
    "displacement_magnitude": {nid: v}, "temperature": {nid: v}}`` with only
    the keys present in the file. Static/thermal take the LAST block of a
    kind (the converged final increment); modal takes the FIRST ``DISP``
    block, i.e. the mode shape of mode 1.
    """
    first = analysis_type == "modal"
    out: dict[str, Any] = {}

    disp = _pick_block(model, "DISP", first=first)
    if disp is not None:
        vectors = {
            nid: (v[0], v[1], v[2]) if len(v) >= 3 else (0.0, 0.0, 0.0)
            for nid, v in disp.values.items()
        }
        out["displacement"] = vectors
        out["displacement_magnitude"] = {
            nid: math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) for nid, d in vectors.items()
        }

    stress = _pick_block(model, "STRESS", first=first)
    if stress is not None:
        mises_idx = next(
            (i for i, c in enumerate(stress.components) if c in ("MISES", "SEQV", "VMISES")),
            None,
        )
        vm: dict[int, float] = {}
        for nid, v in stress.values.items():
            if mises_idx is not None and len(v) > mises_idx:
                vm[nid] = v[mises_idx]
            elif len(v) >= 6:
                vm[nid] = von_mises(v)
        if vm:
            out["von_mises"] = vm

    temp = _pick_block(model, "NDTEMP", first=first)
    if temp is not None:
        out["temperature"] = {nid: v[0] for nid, v in temp.values.items() if v}
    return out


def extract_surface(
    nodes: dict[int, tuple[float, float, float]],
    elements: dict[int, tuple[int, list[int]]],
) -> list[tuple[int, int, int]]:
    """Outer-surface triangles (node-id triples), wound outward.

    A face of a volume element is on the outer surface when no other
    element shares it (matched on its sorted corner nodes). Quads split
    into two triangles. Second-order elements contribute their corner
    nodes only, so the surface is drawn as linear facets. Shell elements
    are passed through as-is.
    """
    face_owner: dict[
        tuple[int, ...], tuple[tuple[int, ...], tuple[float, float, float]] | None
    ] = {}
    surface_faces: list[tuple[int, ...]] = []

    for etype, conn in elements.values():
        spec = _VOLUME_TYPES.get(etype)
        if spec is None:
            corners = _SURFACE_TYPES.get(etype)
            if corners is not None and len(conn) >= corners:
                surface_faces.append(tuple(conn[:corners]))
            continue
        _name, corner_count, faces = spec
        if len(conn) < corner_count:
            continue
        corner_ids = conn[:corner_count]
        try:
            pts = [nodes[n] for n in corner_ids]
        except KeyError:
            continue
        centroid = (
            sum(p[0] for p in pts) / corner_count,
            sum(p[1] for p in pts) / corner_count,
            sum(p[2] for p in pts) / corner_count,
        )
        for face in faces:
            ids = tuple(corner_ids[i] for i in face)
            key = tuple(sorted(ids))
            if key in face_owner:
                face_owner[key] = None  # shared: interior face
            else:
                face_owner[key] = (ids, centroid)

    triangles: list[tuple[int, int, int]] = []
    for entry in face_owner.values():
        if entry is None:
            continue
        ids, centroid = entry
        triangles.extend(_oriented_triangles(nodes, ids, centroid))
    for ids in surface_faces:
        if all(n in nodes for n in ids):
            triangles.extend(_split(ids))
    return triangles


def _split(ids: tuple[int, ...]) -> list[tuple[int, int, int]]:
    if len(ids) == 3:
        return [(ids[0], ids[1], ids[2])]
    return [(ids[0], ids[1], ids[2]), (ids[0], ids[2], ids[3])]


def _oriented_triangles(
    nodes: dict[int, tuple[float, float, float]],
    ids: tuple[int, ...],
    centroid: tuple[float, float, float],
) -> list[tuple[int, int, int]]:
    tris = _split(ids)
    a, b, c = (nodes[i] for i in tris[0])
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    fc = [sum(nodes[i][k] for i in ids) / len(ids) for k in range(3)]
    outward = (fc[0] - centroid[0]) * nx + (fc[1] - centroid[1]) * ny + (fc[2] - centroid[2]) * nz
    if outward < 0:
        return [(t[0], t[2], t[1]) for t in tris]
    return tris


def _decimate(
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    attributes: list[list[float]],
    cell: float,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], list[list[float]]]:
    """Vertex-clustering decimation on a ``cell``-sized grid.

    Vertices in one grid cell merge into their average (position and every
    per-vertex attribute); triangles that collapse are dropped. Crude, but
    it is deterministic, needs no dependency, and preserves the overall
    shape and the large-scale field gradients a colour map shows.
    """
    cluster_of: dict[tuple[int, int, int], int] = {}
    remap: list[int] = []
    sums: list[list[float]] = []
    counts: list[int] = []
    width = 3 + (len(attributes[0]) if attributes else 0)
    for i, p in enumerate(positions):
        key = (math.floor(p[0] / cell), math.floor(p[1] / cell), math.floor(p[2] / cell))
        idx = cluster_of.get(key)
        if idx is None:
            idx = len(sums)
            cluster_of[key] = idx
            sums.append([0.0] * width)
            counts.append(0)
        acc = sums[idx]
        acc[0] += p[0]
        acc[1] += p[1]
        acc[2] += p[2]
        if attributes:
            for k, val in enumerate(attributes[i]):
                acc[3 + k] += val
        counts[idx] += 1
        remap.append(idx)

    new_positions = [(s[0] / n, s[1] / n, s[2] / n) for s, n in zip(sums, counts, strict=True)]
    new_attrs = [[v / n for v in s[3:]] for s, n in zip(sums, counts, strict=True)]
    seen: set[tuple[int, int, int]] = set()
    new_tris: list[tuple[int, int, int]] = []
    for a, b, c in triangles:
        ra, rb, rc = remap[a], remap[b], remap[c]
        if ra == rb or rb == rc or ra == rc:
            continue
        lo, mid, hi = sorted((ra, rb, rc))
        key = (lo, mid, hi)
        if key in seen:
            continue
        seen.add(key)
        new_tris.append((ra, rb, rc))
    return new_positions, new_tris, new_attrs


def _sig(value: float, digits: int = 4) -> float:
    if value == 0 or not math.isfinite(value):
        return 0.0
    return float(f"{value:.{digits}g}")


_FIELD_META: dict[str, tuple[str, str]] = {
    "von_mises": ("Von Mises stress", "MPa"),
    "displacement_magnitude": ("Displacement magnitude", "mm"),
    "temperature": ("Temperature", "C"),
}


def _bbox(points: list[tuple[float, float, float]]) -> dict[str, list[float]]:
    return {
        "min": [min(p[k] for p in points) for k in range(3)],
        "max": [max(p[k] for p in points) for k in range(3)],
    }


@dataclass
class FieldPayload:
    """An encoded payload plus the summary the adapter returns inline."""

    gz_bytes: bytes
    summary: dict[str, Any]

    def to_result(self, file_path: str | None = None) -> dict[str, Any]:
        out = dict(self.summary)
        out["encoding"] = "gzip"
        out["media_type"] = PAYLOAD_MEDIA_TYPE
        out["size_bytes"] = len(self.gz_bytes)
        out["base64"] = base64.b64encode(self.gz_bytes).decode("ascii")
        if file_path:
            out["file"] = file_path
        return out


def build_field_payload(
    model: FrdModel,
    analysis_type: str,
    *,
    markers: list[dict[str, Any]] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    source_name: str | None = None,
) -> FieldPayload:
    """Build the gzipped viewer payload for one solved ``.frd`` model.

    Raises :class:`FieldPayloadError` when the model has no element block
    to take a surface from, or no nodal field at all.
    """
    with tracer.start_as_current_span("calculix.build_field_payload") as span:
        span.set_attribute("calculix.analysis_type", analysis_type)
        if not model.elements:
            raise FieldPayloadError("no element connectivity -- cannot extract a surface")
        fields = nodal_fields(model, analysis_type)
        scalar_keys = [
            k for k in ("von_mises", "displacement_magnitude", "temperature") if k in fields
        ]
        if not scalar_keys:
            raise FieldPayloadError("no DISP/STRESS/NDTEMP result block with node values")

        tri_ids = extract_surface(model.nodes, model.elements)
        if not tri_ids:
            raise FieldPayloadError("mesh has no outer surface faces")
        source_triangle_count = len(tri_ids)

        # Compact re-index of surface vertices.
        index_of: dict[int, int] = {}
        positions: list[tuple[float, float, float]] = []
        node_order: list[int] = []
        triangles: list[tuple[int, int, int]] = []
        for tri in tri_ids:
            mapped = []
            for nid in tri:
                idx = index_of.get(nid)
                if idx is None:
                    idx = len(positions)
                    index_of[nid] = idx
                    positions.append(model.nodes[nid])
                    node_order.append(nid)
                mapped.append(idx)
            triangles.append((mapped[0], mapped[1], mapped[2]))

        has_disp = "displacement" in fields
        disp_map: dict[int, tuple[float, float, float]] = fields.get("displacement", {})
        attributes: list[list[float]] = []
        for nid in node_order:
            row: list[float] = []
            if has_disp:
                row.extend(disp_map.get(nid, (0.0, 0.0, 0.0)))
            for key in scalar_keys:
                row.append(float(fields[key].get(nid, 0.0)))
            attributes.append(row)

        # Field statistics come from the FULL nodal field (never decimated).
        stats: dict[str, dict[str, Any]] = {}
        for key in scalar_keys:
            values = fields[key]
            peak_nid = max(values, key=lambda n: values[n])
            peak_pos = model.nodes.get(peak_nid)
            stats[key] = {
                "min": _sig(min(values.values()), 6),
                "max": _sig(values[peak_nid], 6),
                "peak": (
                    {"position": [_sig(c, 6) for c in peak_pos], "value": _sig(values[peak_nid], 6)}
                    if peak_pos is not None
                    else None
                ),
            }

        bbox = _bbox(positions)
        diag = math.dist(bbox["min"], bbox["max"]) or 1.0
        pos_decimals = max(0, 5 - math.ceil(math.log10(diag)))

        def _encode(
            pos: list[tuple[float, float, float]],
            tris: list[tuple[int, int, int]],
            attrs: list[list[float]],
            decimation: dict[str, Any],
        ) -> tuple[bytes, dict[str, Any]]:
            offset = 3 if has_disp else 0
            payload: dict[str, Any] = {
                "format": PAYLOAD_FORMAT,
                "version": PAYLOAD_VERSION,
                "analysis_type": analysis_type,
                "units": {
                    "length": "mm",
                    "stress": "MPa",
                    "displacement": "mm",
                    "temperature": "C",
                },
                "vertex_count": len(pos),
                "triangle_count": len(tris),
                "positions": [round(c, pos_decimals) for p in pos for c in p],
                "indices": [i for t in tris for i in t],
                "displacement": (
                    [_sig(a[k]) for a in attrs for k in range(3)] if has_disp else None
                ),
                "fields": {
                    key: {
                        "label": _FIELD_META[key][0],
                        "unit": _FIELD_META[key][1],
                        "values": [_sig(a[offset + j]) for a in attrs],
                        **stats[key],
                    }
                    for j, key in enumerate(scalar_keys)
                },
                "markers": markers or [],
                "bbox": {
                    "min": [round(c, pos_decimals) for c in bbox["min"]],
                    "max": [round(c, pos_decimals) for c in bbox["max"]],
                },
                "decimation": decimation,
                "source": {
                    "frd_file": source_name,
                    "mode": 1 if analysis_type == "modal" else None,
                },
            }
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            return gzip.compress(raw, compresslevel=6, mtime=0), payload

        decimation: dict[str, Any] = {
            "applied": False,
            "source_triangle_count": source_triangle_count,
            "cell_size_mm": None,
        }
        cur_pos, cur_tris, cur_attrs = positions, triangles, attributes
        cell = diag / 400.0
        passes = 0
        while len(cur_tris) > _MAX_TRIANGLES_BEFORE_ENCODE and passes < _MAX_DECIMATION_PASSES:
            cur_pos, cur_tris, cur_attrs = _decimate(positions, triangles, attributes, cell)
            decimation = {
                "applied": True,
                "source_triangle_count": source_triangle_count,
                "cell_size_mm": _sig(cell),
            }
            cell *= 1.5
            passes += 1
        gz, payload = _encode(cur_pos, cur_tris, cur_attrs, decimation)
        while len(gz) > max_bytes and passes < _MAX_DECIMATION_PASSES:
            cur_pos, cur_tris, cur_attrs = _decimate(positions, triangles, attributes, cell)
            decimation = {
                "applied": True,
                "source_triangle_count": source_triangle_count,
                "cell_size_mm": _sig(cell),
            }
            gz, payload = _encode(cur_pos, cur_tris, cur_attrs, decimation)
            cell *= 1.5
            passes += 1
        if len(gz) > max_bytes:
            raise FieldPayloadError(
                f"field payload is {len(gz)} bytes after {passes} decimation passes, "
                f"over the {max_bytes}-byte cap"
            )

        summary = {
            "format": f"{PAYLOAD_FORMAT}/{PAYLOAD_VERSION}",
            "analysis_type": analysis_type,
            "quantities": scalar_keys,
            "has_displacement": has_disp,
            "vertex_count": payload["vertex_count"],
            "triangle_count": payload["triangle_count"],
            "decimated": bool(decimation["applied"]),
            "source_triangle_count": source_triangle_count,
            "ranges": {k: {"min": stats[k]["min"], "max": stats[k]["max"]} for k in scalar_keys},
            "marker_count": len(markers or []),
        }
        span.set_attribute("calculix.field.size_bytes", len(gz))
        span.set_attribute("calculix.field.triangles", payload["triangle_count"])
        span.set_attribute("calculix.field.decimated", bool(decimation["applied"]))
        logger.info(
            "calculix_field_payload_built",
            analysis_type=analysis_type,
            size_bytes=len(gz),
            vertex_count=payload["vertex_count"],
            triangle_count=payload["triangle_count"],
            source_triangle_count=source_triangle_count,
            decimated=bool(decimation["applied"]),
            quantities=scalar_keys,
        )
        return FieldPayload(gz_bytes=gz, summary=summary)


def node_set_marker(
    mesh: Any,
    node_set: str,
    *,
    kind: str,
    vector: list[float] | tuple[float, float, float] | None = None,
    value: float | None = None,
    unit: str | None = None,
) -> dict[str, Any] | None:
    """A viewer marker for a named node set of an ``inp_mesh.MeshData``:
    its centroid and bbox, plus the force vector (load) or the prescribed
    value (thermal source/sink). ``None`` if the set is not in the mesh."""
    try:
        ids = mesh.node_ids_for_elset(node_set)
        bbox = mesh.bounding_box_for_nodes(ids)
    except (KeyError, ValueError):
        return None
    pts = [mesh.nodes[n] for n in ids]
    centroid = [_sig(sum(p[k] for p in pts) / len(pts), 6) for k in range(3)]
    marker: dict[str, Any] = {
        "kind": kind,
        "label": node_set,
        "position": centroid,
        "bbox": {
            "min": [bbox["min_x"], bbox["min_y"], bbox["min_z"]],
            "max": [bbox["max_x"], bbox["max_y"], bbox["max_z"]],
        },
        "node_count": len(ids),
    }
    if vector is not None:
        marker["vector"] = [float(v) for v in vector]
    if value is not None:
        marker["value"] = float(value)
    if unit:
        marker["unit"] = unit
    return marker


def write_payload_file(payload: FieldPayload, frd_path: str) -> str:
    """Write ``<frd stem>_field.json.gz`` next to the ``.frd``; return its path.

    A file on the shared adapter workspace lets ``twin.record_document``
    take the field by reference (``field_file``), so a model never has to
    carry the base64 between two tool calls (see MET-684 for why that is
    unsafe for STEP blobs; the same applies here).
    """
    frd = Path(frd_path)
    out = frd.with_name(f"{frd.stem}_field.json.gz")
    out.write_bytes(payload.gz_bytes)
    return str(out)
