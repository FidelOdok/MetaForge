"""A library part's real 3D model, as a mesh the studio and the viewer can draw (2026-11-12).

A component library entry can carry its maker's own model as a ``models`` representation
(``kind: cad``, ``engine: step``) whose artifact is pinned by URL, sha256 and size. Many
makers publish a model with no licence stated. The owner's policy (2026-11-12): such a model
is stored and shown for internal design work, with its licence recorded as published, and it
is still refused where a model leaves the building (``library.resolve_component_model``
keeps refusing a licence MetaForge does not accept, and that is the route into engineering
tools and deliverables).

The mesh is made here, never on the page (which has no network):

1. the artifact is fetched once, checked against its size and sha256, and kept by digest;
   a zip is opened and the member named by the artifact's ``path`` is taken;
2. the STEP goes through the gateway's OCCT conversion service to GLB, and a stub written
   while the converter is down is discarded, never shown as a model;
3. the GLB's nodes are flattened with their transforms into one list of coloured parts;
4. the part is turned so its ``up_axis`` points up the studio's Y, and centred on its
   bounding box, so x is its length, y its height and z its width, like a drawn model;
5. a mesh over the per-part triangle budget is reduced by vertex clustering.

The result is cached by artifact digest and recipe version.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.model_mesh")

#: Bump when the mesh recipe changes, so cached meshes are made again.
RECIPE = 1
#: The studio's per-part triangle budget (IMG-FORM-006).
MAX_TRIANGLES = 20_000
#: Coordinates are kept to a micrometre.
PLACES = 3

Fetch = Callable[[str], bytes]
Convert = Callable[[bytes, str], bytes]


class MeshError(Exception):
    """This entry's model could not be made into a mesh; the message says why."""


# -- choosing the model ---------------------------------------------------------------


def cad_model(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The entry's first active STEP model with an entrypoint artifact, or None."""
    for model in entry.get("models") or []:
        if model.get("status", "active") != "active" or model.get("engine") != "step":
            continue
        for artifact in model.get("artifacts") or []:
            if artifact.get("path") == model.get("entrypoint"):
                return {"model": model, "artifact": artifact}
    return None


# -- the file -------------------------------------------------------------------------


def verified_bytes(artifact: dict[str, Any], fetch: Fetch, cache_dir: Path) -> bytes:
    """The artifact's bytes, fetched once and checked against its sha256 and size."""
    digest = str(artifact["sha256"])
    path = cache_dir / "files" / digest[:2] / digest
    if path.is_file():
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() == digest:
            return data
    data = fetch(str(artifact["url"]))
    if len(data) != int(artifact["size"]):
        raise MeshError(f"the file is {len(data)} bytes, not the {artifact['size']} recorded")
    if hashlib.sha256(data).hexdigest() != digest:
        raise MeshError("the file does not match its recorded sha256; it changed upstream")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return data


def step_bytes(data: bytes, member: str) -> bytes:
    """The STEP itself: ``data`` when it is one, else the zip member named ``member``."""
    if not data.startswith(b"PK"):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        exact = [n for n in names if n == member or n.rsplit("/", 1)[-1] == member]
        if not exact:
            raise MeshError(f"the zip has no {member!r} (it holds {', '.join(names)[:200]})")
        info = zf.getinfo(exact[0])
        if info.file_size > 200 * 1024 * 1024:
            raise MeshError("the model in the zip is over 200 MB")
        return zf.read(info)


# -- GLB ------------------------------------------------------------------------------

_COMPONENTS = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _matmul(a: list[float], b: list[float]) -> list[float]:
    """Column-major 4x4 product a * b."""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def _node_matrix(node: dict[str, Any]) -> list[float]:
    if "matrix" in node:
        return [float(v) for v in node["matrix"]]
    tx, ty, tz = node.get("translation", [0, 0, 0])
    qx, qy, qz, qw = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    r = [
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy + qz * qw), 2 * (qx * qz - qy * qw), 0,
        2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw), 0,
        2 * (qx * qz + qy * qw), 2 * (qy * qz - qx * qw), 1 - 2 * (qx * qx + qy * qy), 0,
        0, 0, 0, 1,
    ]  # fmt: skip
    for i in range(3):
        r[i] *= sx
        r[4 + i] *= sy
        r[8 + i] *= sz
    r[12], r[13], r[14] = tx, ty, tz
    return [float(v) for v in r]


def parse_glb(data: bytes) -> list[dict[str, Any]]:
    """Every triangle primitive in the GLB's scene, in world space: name, colour, points."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise MeshError("the converter returned no model")
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    rest = 20 + json_len
    binary = b""
    if rest + 8 <= len(data):
        bin_len = struct.unpack_from("<I", data, rest)[0]
        binary = data[rest + 8 : rest + 8 + bin_len]

    def read(index: int) -> list[Any]:
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        kind, width = _COMPONENTS[acc["componentType"]], _WIDTH[acc["type"]]
        item = struct.calcsize("<" + kind)
        stride = view.get("byteStride") or item * width
        base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        out: list[Any] = []
        for n in range(acc["count"]):
            at = base + n * stride
            out.append(struct.unpack_from("<" + kind * width, binary, at))
        return out

    parts: list[dict[str, Any]] = []
    nodes = doc.get("nodes") or []

    def visit(i: int, parent: list[float]) -> None:
        node = nodes[i]
        world = _matmul(parent, _node_matrix(node))
        if "mesh" in node:
            for prim in doc["meshes"][node["mesh"]].get("primitives", []):
                if prim.get("mode", 4) != 4 or "POSITION" not in prim.get("attributes", {}):
                    continue
                raw = read(prim["attributes"]["POSITION"])
                pts = [
                    (
                        world[0] * x + world[4] * y + world[8] * z + world[12],
                        world[1] * x + world[5] * y + world[9] * z + world[13],
                        world[2] * x + world[6] * y + world[10] * z + world[14],
                    )
                    for x, y, z in raw
                ]
                idx = (
                    [v[0] for v in read(prim["indices"])]
                    if "indices" in prim
                    else list(range(len(pts)))
                )
                colour = [0.6, 0.6, 0.6]
                if "material" in prim:
                    mat = doc["materials"][prim["material"]]
                    rgba = mat.get("pbrMetallicRoughness", {}).get("baseColorFactor")
                    if rgba:
                        colour = [float(c) for c in rgba[:3]]
                parts.append({"name": str(node.get("name") or ""), "colour": colour,
                              "points": pts, "indices": idx})  # fmt: skip
        for child in node.get("children") or []:
            visit(child, world)

    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    scene = (doc.get("scenes") or [{}])[doc.get("scene", 0)] if doc.get("scenes") else {}
    roots = scene.get("nodes")
    if roots is None:
        children = {c for n in nodes for c in n.get("children") or []}
        roots = [i for i in range(len(nodes)) if i not in children]
    for root in roots:
        visit(root, identity)
    if not parts:
        raise MeshError("the model has no triangles")
    return parts


# -- placing it like a drawn part ------------------------------------------------------


def _to_studio(up_axis: str) -> Callable[[tuple[float, float, float]], tuple[float, ...]]:
    """A proper rotation that sends the model's ``up_axis`` to +Y."""
    table = {
        "y": lambda p: (p[0], p[1], p[2]),
        "-y": lambda p: (p[0], -p[1], -p[2]),
        "z": lambda p: (p[0], p[2], -p[1]),
        "-z": lambda p: (p[0], -p[2], p[1]),
        "x": lambda p: (-p[1], p[0], p[2]),
        "-x": lambda p: (p[1], -p[0], p[2]),
    }
    if up_axis not in table:
        raise MeshError(f"up_axis must be one of {', '.join(table)}")
    return table[up_axis]


def place(parts: list[dict[str, Any]], up_axis: str) -> dict[str, Any]:
    """Turn ``up_axis`` to +Y, centre on the bounding box; positions and size in mm."""
    turn = _to_studio(up_axis)
    for part in parts:
        part["points"] = [turn(p) for p in part["points"]]
    lo = [min(p[a] for part in parts for p in part["points"]) for a in range(3)]
    hi = [max(p[a] for part in parts for p in part["points"]) for a in range(3)]
    mid = [(lo[a] + hi[a]) / 2 for a in range(3)]
    for part in parts:
        part["points"] = [tuple(p[a] - mid[a] for a in range(3)) for p in part["points"]]
    return {"size": [round(hi[a] - lo[a], PLACES) for a in range(3)], "parts": parts}


def _triangles(parts: list[dict[str, Any]]) -> int:
    return sum(len(p["indices"]) // 3 for p in parts)


def reduce(parts: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Vertex clustering until the whole mesh is within ``budget`` triangles."""
    if _triangles(parts) <= budget:
        return parts
    pts = [p for part in parts for p in part["points"]]
    span = max(max(p[a] for p in pts) - min(p[a] for p in pts) for a in range(3)) or 1.0
    cell = span / 400
    for _ in range(40):
        out = []
        for part in parts:
            keys: dict[tuple[int, int, int], int] = {}
            sums: list[list[float]] = []
            remap: list[int] = []
            for p in part["points"]:
                k = (math.floor(p[0] / cell), math.floor(p[1] / cell), math.floor(p[2] / cell))
                if k not in keys:
                    keys[k] = len(sums)
                    sums.append([0.0, 0.0, 0.0, 0.0])
                s = sums[keys[k]]
                s[0] += p[0]
                s[1] += p[1]
                s[2] += p[2]
                s[3] += 1
                remap.append(keys[k])
            seen: set[tuple[int, int, int]] = set()
            idx: list[int] = []
            ix = part["indices"]
            for t in range(0, len(ix) - 2, 3):
                a, b, c = remap[ix[t]], remap[ix[t + 1]], remap[ix[t + 2]]
                if a == b or b == c or a == c:
                    continue
                key = (a, b, c) if a < b and a < c else (b, c, a) if b < c else (c, a, b)
                if key in seen:
                    continue
                seen.add(key)
                idx += [a, b, c]
            if idx:
                points = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) for s in sums]
                out.append({**part, "points": points, "indices": idx})
        if _triangles(out) <= budget:
            return out
        cell *= 1.3
    raise MeshError(f"the model could not be brought under {budget} triangles")


def to_json(placed: dict[str, Any], *, source: dict[str, Any]) -> dict[str, Any]:
    """The mesh as the page takes it: flat positions and indices per coloured part."""
    parts = [
        {
            "name": p["name"],
            "colour": [round(c, 3) for c in p["colour"]],
            "positions": [round(v, PLACES) for pt in p["points"] for v in pt],
            "indices": list(p["indices"]),
        }
        for p in placed["parts"]
    ]
    return {
        "recipe": RECIPE,
        "size_mm": placed["size"],
        "triangles": sum(len(p["indices"]) // 3 for p in parts),
        "parts": parts,
        "source": source,
    }


# -- the whole recipe -----------------------------------------------------------------


def make_mesh(
    entry: dict[str, Any], *, fetch: Fetch, convert: Convert, cache_dir: Path
) -> dict[str, Any]:
    """The entry's model as a placed, budgeted, coloured mesh; cached by file digest."""
    chosen = cad_model(entry)
    if chosen is None:
        raise MeshError("the entry has no active STEP model attached")
    model, artifact = chosen["model"], chosen["artifact"]
    up = str(model.get("up_axis") or "z")
    key = f"{artifact['sha256']}-{up}-r{RECIPE}"
    cached = cache_dir / "meshes" / f"{key}.json"
    if cached.is_file():
        return dict(json.loads(cached.read_text()))
    with tracer.start_as_current_span("library.model_mesh") as span:
        span.set_attribute("entry", str(entry.get("name")))
        data = verified_bytes(artifact, fetch, cache_dir)
        step = step_bytes(data, str(artifact["path"]))
        glb = convert(step, str(artifact["path"]))
        parts = reduce(parse_glb(glb), MAX_TRIANGLES)
        placed = place(parts, up)
        out = to_json(
            placed,
            source={
                "entry": entry.get("name"),
                "entry_version": entry.get("version"),
                "model_id": model.get("model_id"),
                "model_version": model.get("version"),
                "sha256": artifact["sha256"],
                "licence": artifact.get("licence"),
                "up_axis": up,
            },
        )
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(out))
        logger.info(
            "library_model_mesh_made",
            entry=entry.get("name"),
            triangles=out["triangles"],
            licence=artifact.get("licence"),
        )
        return out


# -- the gateway's own fetch and convert ---------------------------------------------


def http_fetch(url: str) -> bytes:
    import httpx

    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if len(resp.content) > 200 * 1024 * 1024:
            raise MeshError("the file is over 200 MB")
        return bytes(resp.content)


def occt_convert(step: bytes, filename: str) -> bytes:
    """STEP to GLB through the gateway's conversion service; a stub is discarded."""
    from api_gateway.convert.routes import get_service

    service = get_service()
    result = service.convert(step, filename if "." in filename else filename + ".step")
    meta = result.get("metadata") or {}
    if meta.get("converter_unavailable"):
        service.discard(result["hash"], "standard")
        raise MeshError("the CAD converter is not running; try again when it is")
    path = service.get_glb_path(result["hash"], "standard")
    if path is None or not path.is_file() or path.stat().st_size == 0:
        raise MeshError("the converter produced no model")
    return path.read_bytes()


def cache_root() -> Path:
    root = os.environ.get("METAFORGE_LIBRARY_MESH_DIR")
    return Path(root) if root else Path.home() / ".metaforge" / "library-meshes"
