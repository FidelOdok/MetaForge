"""A minimal binary glTF box for render tests, written with the standard library."""

from __future__ import annotations

import json
import struct


def box_glb(sx: float = 40.0, sy: float = 20.0, sz: float = 10.0) -> bytes:
    """A closed box ``sx`` x ``sy`` x ``sz`` (mm, Z up as the STEP converter writes)."""
    faces = [
        ((1, 0, 0), [(sx, 0, 0), (sx, sy, 0), (sx, sy, sz), (sx, 0, sz)]),
        ((-1, 0, 0), [(0, sy, 0), (0, 0, 0), (0, 0, sz), (0, sy, sz)]),
        ((0, 1, 0), [(sx, sy, 0), (0, sy, 0), (0, sy, sz), (sx, sy, sz)]),
        ((0, -1, 0), [(0, 0, 0), (sx, 0, 0), (sx, 0, sz), (0, 0, sz)]),
        ((0, 0, 1), [(0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz)]),
        ((0, 0, -1), [(0, sy, 0), (sx, sy, 0), (sx, 0, 0), (0, 0, 0)]),
    ]
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    for normal, quad in faces:
        base = len(positions) // 3
        for p in quad:
            positions.extend(float(c) for c in p)
            normals.extend(float(c) for c in normal)
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    pos = struct.pack(f"<{len(positions)}f", *positions)
    nor = struct.pack(f"<{len(normals)}f", *normals)
    idx = struct.pack(f"<{len(indices)}H", *indices)
    while len(idx) % 4:
        idx += b"\x00"
    binary = pos + nor + idx
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "box"}],
        "meshes": [
            {
                "primitives": [
                    {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}
                ]
            }
        ],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.7, 0.72, 0.75, 1.0]}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nor)},
            {"buffer": 0, "byteOffset": len(pos) + len(nor), "byteLength": len(indices) * 2},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(positions) // 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [float(sx), float(sy), float(sz)],
            },
            {"bufferView": 1, "componentType": 5126, "count": len(normals) // 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(js) % 4:
        js += b" "
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(js), b"JSON")
        + js
        + struct.pack("<I4s", len(binary), b"BIN\x00")
        + binary
    )
