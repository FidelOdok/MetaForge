"""STL surfaces for OpenFOAM adapter tests: a straight round pipe (FORGE-557).

The pipe is the one internal flow with a closed-form answer. Fully developed
laminar flow has a pressure drop of ``32 * mu * L * v / D^2``
(Hagen-Poiseuille), so a run can be checked against physics rather than against
itself.
"""

from __future__ import annotations

import math
from pathlib import Path

from tool_registry.tools.openfoam.stl import Triangle, write_ascii_stl


def pipe_triangles(radius: float, length: float, segments: int = 64) -> dict[str, list[Triangle]]:
    """Inlet disc at x=0, outlet disc at x=length, lateral wall between."""
    ring = [
        (
            math.cos(2 * math.pi * i / segments) * radius,
            math.sin(2 * math.pi * i / segments) * radius,
        )
        for i in range(segments)
    ]
    inlet: list[Triangle] = []
    outlet: list[Triangle] = []
    wall: list[Triangle] = []
    for i in range(segments):
        (y0, z0), (y1, z1) = ring[i], ring[(i + 1) % segments]
        inlet.append((0, 0, 0, 0, y1, z1, 0, y0, z0))
        outlet.append((length, 0, 0, length, y0, z0, length, y1, z1))
        wall.append((0, y0, z0, length, y0, z0, length, y1, z1))
        wall.append((0, y0, z0, length, y1, z1, 0, y1, z1))
    return {"inlet": inlet, "outlet": outlet, "wall": wall}


def write_pipe(
    directory: Path, radius: float, length: float, segments: int = 64, scale: float = 1.0
) -> dict[str, Path]:
    """Write the three surfaces, vertices multiplied by ``scale`` (1000 for mm)."""
    directory.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name, tris in pipe_triangles(radius, length, segments).items():
        path = directory / f"{name}.stl"
        write_ascii_stl(path, name, tris, scale)
        out[name] = path
    return out
