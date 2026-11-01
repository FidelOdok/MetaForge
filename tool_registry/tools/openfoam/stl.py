"""Minimal STL reading and writing for the OpenFOAM adapter (FORGE-557).

snappyHexMesh takes its surfaces in metres, and a person's STL may be in
millimetres, so the adapter reads each surface, scales it and writes it back as
ASCII into the case. Pure Python, no numpy: the adapter image stays small.
"""

from __future__ import annotations

import struct
from pathlib import Path

# One triangle: nine floats, (x1 y1 z1 x2 y2 z2 x3 y3 z3).
Triangle = tuple[float, float, float, float, float, float, float, float, float]


class StlError(ValueError):
    """The file is not a readable STL surface."""


def read_stl(path: Path) -> list[Triangle]:
    """Triangles of an ASCII or binary STL.

    A binary file is recognised by its size matching the triangle count in its
    header. That check comes first because a binary header may itself begin with
    the word ``solid``.
    """
    data = path.read_bytes()
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * count:
            return _read_binary(data, count)
    return _read_ascii(data.decode("utf-8", errors="replace"), path)


def _read_binary(data: bytes, count: int) -> list[Triangle]:
    out: list[Triangle] = []
    offset = 84
    for _ in range(count):
        # 12 floats (normal + 3 vertices), then a 2-byte attribute.
        values = struct.unpack_from("<12f", data, offset)
        out.append(values[3:12])  # type: ignore[arg-type]
        offset += 50
    return out


def _read_ascii(text: str, path: Path) -> list[Triangle]:
    verts: list[float] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            try:
                verts.extend(float(p) for p in parts[1:])
            except ValueError as exc:
                raise StlError(f"{path.name}: bad vertex line {line!r}") from exc
    if not verts or len(verts) % 9:
        raise StlError(f"{path.name}: no triangles found (is it an STL surface?)")
    return [tuple(verts[i : i + 9]) for i in range(0, len(verts), 9)]  # type: ignore[misc]


def bounds(tris: list[Triangle]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Axis-aligned (min, max) of a set of triangles."""
    xs = [t[i] for t in tris for i in (0, 3, 6)]
    ys = [t[i] for t in tris for i in (1, 4, 7)]
    zs = [t[i] for t in tris for i in (2, 5, 8)]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def write_ascii_stl(path: Path, name: str, tris: list[Triangle], scale: float = 1.0) -> None:
    """Write triangles as ASCII STL, vertices multiplied by ``scale``.

    Normals are written as zero: snappyHexMesh recomputes them from the vertex
    order, and a stale normal would only mislead.
    """
    lines = [f"solid {name}"]
    for t in tris:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for i in (0, 3, 6):
            lines.append(
                f"      vertex {t[i] * scale:.9g} {t[i + 1] * scale:.9g} {t[i + 2] * scale:.9g}"
            )
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
