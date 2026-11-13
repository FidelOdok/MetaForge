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
   a zip is opened and the member named by the artifact's ``path`` is taken; when the
   maker ships the model inside a larger file, ``embedded`` walks the streams of an OLE
   compound file (an Altium library) to the STEP, ``extracted_sha256`` checks what comes
   out, and ``part`` asks the converter for one named product of an assembly STEP;
2. the STEP goes to the OCCT converter's ``/faces`` route, which returns its triangles in
   one group per part and colour, each face carrying the colour its STEP states (its own
   style, else its placed instance's, its part's or its assembly's) as sRGB screen values,
   or no colour when the file states none; nothing is painted grey. (The GLB route kept
   one colour per part and dropped every face colour, so a blue board with gold pads
   came out grey; recipe 2 replaces it.)
3. each group is kept as a part of its own, with its ``colour_source``;
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
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.model_mesh")

#: Bump when the mesh recipe changes, so cached meshes are made again.
RECIPE = 2
#: The studio's per-part triangle budget (IMG-FORM-006).
MAX_TRIANGLES = 20_000
#: Coordinates are kept to a micrometre.
PLACES = 3

Fetch = Callable[[str], bytes]
Convert = Callable[..., list[dict[str, Any]]]


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


# -- a model inside a larger maker file (2026-11-12) -----------------------------------

_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_END = 0xFFFFFFF0  # sector ids at or above this end a chain


def ole_streams(data: bytes) -> dict[str, bytes]:
    """Every stream of an OLE compound file (Altium's IntLib, PcbLib, PcbDoc), by path.

    Read only, and only the parts of the format the files need: the FAT and DIFAT, the
    directory tree and the mini stream for small streams.
    """
    import struct

    if not data.startswith(_OLE_MAGIC):
        raise MeshError("the embedded file is not an OLE compound file")
    ssz = 1 << struct.unpack_from("<H", data, 30)[0]
    mini = 1 << struct.unpack_from("<H", data, 32)[0]
    n_fat, dir_start = struct.unpack_from("<II", data, 44)
    cutoff, minifat_start = struct.unpack_from("<II", data, 56)
    dif_start, n_dif = struct.unpack_from("<II", data, 68)
    per = ssz // 4

    def sector(n: int) -> bytes:
        return data[ssz * (n + 1) : ssz * (n + 2)]

    difat = list(struct.unpack_from("<109I", data, 76))
    s = dif_start
    for _ in range(n_dif):
        if s >= _END:
            break
        vals = struct.unpack(f"<{per}I", sector(s))
        difat += vals[:-1]
        s = vals[-1]
    fat: list[int] = []
    for f in difat[:n_fat]:
        if f < _END:
            fat += struct.unpack(f"<{per}I", sector(f))

    def chain(start: int, table: list[int], read: Callable[[int], bytes]) -> bytes:
        out, seen, n = [], set(), start
        while n < _END:
            if n in seen or n >= len(table):
                raise MeshError("the embedded file's sector chain is broken")
            seen.add(n)
            out.append(read(n))
            n = table[n]
        return b"".join(out)

    directory = chain(dir_start, fat, sector)
    entries = []
    for i in range(len(directory) // 128):
        e = directory[i * 128 : (i + 1) * 128]
        name_len = struct.unpack_from("<H", e, 64)[0]
        left, right, child = struct.unpack_from("<III", e, 68)
        start, size = struct.unpack_from("<II", e, 116)
        name = e[: max(name_len - 2, 0)].decode("utf-16le", "replace")
        entries.append(
            {"name": name, "type": e[66], "start": start, "size": size}
            | {"left": left, "right": right, "child": child}
        )
    root = entries[0]
    ministream = chain(root["start"], fat, sector) if root["start"] < _END else b""
    minifat: list[int] = []
    if minifat_start < _END:
        raw = chain(minifat_start, fat, sector)
        minifat = list(struct.unpack(f"<{len(raw) // 4}I", raw))

    streams: dict[str, bytes] = {}

    def walk(i: int, prefix: str, depth: int = 0) -> None:
        if i >= len(entries) or depth > 64:
            return
        e = entries[i]
        walk(e["left"], prefix, depth + 1)
        walk(e["right"], prefix, depth + 1)
        path = f"{prefix}/{e['name']}" if prefix else e["name"]
        if e["type"] == 2:
            if e["size"] < cutoff:
                body = chain(e["start"], minifat, lambda n: ministream[n * mini : (n + 1) * mini])
            else:
                body = chain(e["start"], fat, sector)
            streams[path] = body[: e["size"]]
        elif e["type"] == 1:
            walk(e["child"], path, depth + 1)

    walk(root["child"], "")
    return streams


def _inflated(data: bytes) -> bytes:
    """``data`` decompressed when it is zlib data (Altium puts a byte or two in front)."""
    import zlib

    for offset in range(0, 8):
        head = data[offset : offset + 2]
        if len(head) == 2 and head[0] == 0x78 and head[1] in (0x01, 0x5E, 0x9C, 0xDA):
            try:
                return zlib.decompressobj().decompress(data[offset:], 300 * 1024 * 1024)
            except zlib.error:
                continue
    return data


def embedded_step(data: bytes, steps: list[str]) -> bytes:
    """The STEP reached by walking ``steps``: each a stream of the OLE file in hand."""
    for step in steps:
        streams = ole_streams(_inflated(data))
        if step not in streams:
            raise MeshError(f"the embedded file has no stream {step!r}")
        data = streams[step]
    data = _inflated(data)
    if not data.lstrip().startswith(b"ISO-10303-21"):
        raise MeshError("the embedded stream is not a STEP file")
    return data


def selected_step(data: bytes, artifact: dict[str, Any]) -> bytes:
    """The artifact's STEP: the zip member, then any embedded stream, checked if pinned."""
    step = step_bytes(data, str(artifact["path"]))
    if artifact.get("embedded"):
        step = embedded_step(step, list(artifact["embedded"]))
    pinned = artifact.get("extracted_sha256")
    if pinned and hashlib.sha256(step).hexdigest() != pinned:
        raise MeshError("the STEP taken from the file does not match its recorded sha256")
    return step


# -- the converter's colour groups -----------------------------------------------------


def parts_from_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The converter's groups as parts with point tuples; an empty group is dropped."""
    parts: list[dict[str, Any]] = []
    for g in groups:
        flat = [float(v) for v in g.get("positions") or []]
        idx = [int(i) for i in g.get("indices") or []]
        if len(flat) < 9 or len(idx) < 3:
            continue
        colour = g.get("colour")
        parts.append(
            {
                "name": str(g.get("name") or ""),
                "colour": [float(c) for c in colour[:3]] if colour else None,
                "colour_source": str(g.get("colour_source") or ("part" if colour else "none")),
                "points": [tuple(flat[i : i + 3]) for i in range(0, len(flat) - 2, 3)],
                "indices": idx,
            }
        )
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
            "colour": [round(c, 3) for c in p["colour"]] if p["colour"] else None,
            "colour_source": p.get("colour_source", "none"),
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
    picked = "|".join([*(artifact.get("embedded") or []), str(artifact.get("part") or "")])
    pick = f"-{hashlib.sha256(picked.encode()).hexdigest()[:12]}" if picked.strip("|") else ""
    key = f"{artifact['sha256']}{pick}-{up}-r{RECIPE}"
    cached = cache_dir / "meshes" / f"{key}.json"
    if cached.is_file():
        return dict(json.loads(cached.read_text()))
    with tracer.start_as_current_span("library.model_mesh") as span:
        span.set_attribute("entry", str(entry.get("name")))
        data = verified_bytes(artifact, fetch, cache_dir)
        step = selected_step(data, artifact)
        part = artifact.get("part")
        name = str(artifact["path"]).rsplit("/", 1)[-1]
        if artifact.get("embedded"):
            name = str(artifact["embedded"][-1]).rsplit("/", 1)[-1] + ".step"
        groups = convert(step, name, part=str(part)) if part else convert(step, name)
        if part and not groups:
            raise MeshError(f"the STEP has no part named {part!r}")
        parts = reduce(parts_from_groups(groups), MAX_TRIANGLES)
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


def occt_faces(step: bytes, filename: str, part: str | None = None) -> list[dict[str, Any]]:
    """The STEP's triangles grouped by each face's stated colour, from the OCCT converter."""
    import httpx

    from api_gateway.convert.service import _OCCT_URL

    name = filename.rsplit("/", 1)[-1]
    if not name.lower().endswith((".step", ".stp")):
        name += ".step"
    try:
        resp = httpx.post(
            f"{_OCCT_URL}/faces",
            params={"quality": "standard", **({"part": part} if part else {})},
            files={"file": (name, step, "application/octet-stream")},
            timeout=300.0,
        )
    except httpx.ConnectError as exc:
        raise MeshError("the CAD converter is not running; try again when it is") from exc
    except httpx.RequestError as exc:
        raise MeshError(f"the CAD converter failed on this file ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        try:
            why = str(resp.json().get("error") or resp.text)
        except ValueError:
            why = resp.text
        raise MeshError(f"the CAD converter refused the file: {why[:300]}")
    groups = resp.json().get("groups")
    if not isinstance(groups, list):
        raise MeshError("the CAD converter returned no colour groups")
    return groups


def cache_root() -> Path:
    root = os.environ.get("METAFORGE_LIBRARY_MESH_DIR")
    return Path(root) if root else Path.home() / ".metaforge" / "library-meshes"
