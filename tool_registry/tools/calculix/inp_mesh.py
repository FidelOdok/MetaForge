"""Parses a CalculiX/Abaqus ``.inp`` mesh file into structured node/element/
elset data (FORGE-234).

``freecad.generate_mesh`` shells out to ``gmsh`` to mesh a STEP file
directly (no explicit ``.geo`` script, no physical groups authored by
anyone) -- but gmsh's own Abaqus/CalculiX writer still emits one named
``*ELEMENT ELSET=<name>`` block per ORIGINAL STEP entity (confirmed against
real gmsh 4.x output: ``Surface1``..``SurfaceN`` for each face, ``Line1``..
``LineN`` for each edge, ``Volume1`` for the solid). Those are ELEMENT sets
(surface/edge elements lying on that entity), not NODE sets -- CalculiX's
``*BOUNDARY``/``*CLOAD`` cards need node sets to apply a constraint or load
to. This module derives the node set for a named elset (the union of nodes
referenced by its elements), so a specific STEP face is addressable by name
for boundary conditions and loads without any new physical-group tagging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MeshData:
    """Parsed contents of one ``.inp`` mesh file."""

    nodes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    # element_id -> (type, [node_ids in element order])
    elements: dict[int, tuple[str, list[int]]] = field(default_factory=dict)
    # elset name (e.g. "Surface1", "Volume1") -> [element_ids]
    elsets: dict[str, list[int]] = field(default_factory=dict)

    def node_ids_for_elset(self, name: str) -> list[int]:
        """Sorted, deduplicated union of node ids referenced by every
        element in the named elset -- the node set CalculiX's *BOUNDARY /
        *CLOAD cards actually need."""
        if name not in self.elsets:
            raise KeyError(
                f"no such element set {name!r} in this mesh -- available: {sorted(self.elsets)}"
            )
        seen: set[int] = set()
        for element_id in self.elsets[name]:
            _etype, node_ids = self.elements[element_id]
            seen.update(node_ids)
        return sorted(seen)

    def bounding_box_for_nodes(self, node_ids: list[int]) -> dict[str, float]:
        if not node_ids:
            raise ValueError("bounding_box_for_nodes: node_ids is empty")
        xs = [self.nodes[n][0] for n in node_ids]
        ys = [self.nodes[n][1] for n in node_ids]
        zs = [self.nodes[n][2] for n in node_ids]
        return {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "min_z": min(zs),
            "max_z": max(zs),
        }

    def face_summaries(self) -> dict[str, dict[str, Any]]:
        """Per-named-face (``Surface*`` elset) node count + bounding box, so
        a caller can identify which face is which by geometry -- e.g. "the
        face whose bounding box has min_x == the part's own min_x is the
        fixed end" -- without guessing from the opaque name alone."""
        summaries: dict[str, dict[str, Any]] = {}
        for name in sorted(self.elsets):
            if not name.startswith("Surface"):
                continue
            node_ids = self.node_ids_for_elset(name)
            summaries[name] = {
                "node_count": len(node_ids),
                "bounding_box": self.bounding_box_for_nodes(node_ids),
            }
        return summaries


def parse_mesh_inp(path: str) -> MeshData:
    """Parse nodes/elements/elsets from a gmsh-written CalculiX ``.inp`` file.

    Format (confirmed against real gmsh 4.x STEP-to-tetrahedra output)::

        *NODE
        <id>, <x>, <y>, <z>
        ...
        *ELEMENT, type=<T>, ELSET=<name>
        <id>, <n1>, <n2>, ...
        ...

    Every block ends at the next ``*``-prefixed line (including gmsh's own
    ``******* E L E M E N T S *************`` banner, which starts a new
    line but names no real section -- correctly falls through to "no
    active section" below) or end of file.
    """
    mesh = MeshData()
    section: str | None = None  # "node" | "element" | None
    current_elset: str | None = None
    current_type: str | None = None

    with open(path, encoding="utf-8", errors="replace") as f:  # noqa: PTH123
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("*"):
                upper = line.upper()
                if upper.startswith("*NODE"):
                    section = "node"
                    current_elset = None
                    current_type = None
                elif upper.startswith("*ELEMENT"):
                    section = "element"
                    current_type = "UNKNOWN"
                    current_elset = None
                    for part in line.split(","):
                        key, _, value = part.strip().partition("=")
                        key = key.strip().upper()
                        if key == "TYPE":
                            current_type = value.strip()
                        elif key == "ELSET":
                            current_elset = value.strip()
                else:
                    section = None
                    current_elset = None
                    current_type = None
                continue
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if not parts:
                continue
            if section == "node":
                node_id = int(parts[0])
                x, y, z = (float(v) for v in parts[1:4])
                mesh.nodes[node_id] = (x, y, z)
            elif section == "element":
                element_id = int(parts[0])
                node_ids = [int(v) for v in parts[1:]]
                mesh.elements[element_id] = (current_type or "UNKNOWN", node_ids)
                if current_elset:
                    mesh.elsets.setdefault(current_elset, []).append(element_id)
    return mesh
