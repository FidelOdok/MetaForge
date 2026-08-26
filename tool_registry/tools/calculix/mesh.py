"""Parser for CalculiX/Abaqus ``.inp`` mesh files.

Meshers in the MetaForge pipeline (FreeCAD, gmsh, Netgen) emit *geometry-only*
decks: ``*NODE`` and ``*ELEMENT`` blocks, sometimes a few named sets, and
nothing else. Reading that geometry back is what lets the deck builder attach
real physics to it and lets the quality checker measure elements instead of
counting lines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

Vec3 = tuple[float, float, float]

#: Node count per supported CalculiX element type, and the subset of those
#: nodes that form the linear "corner" geometry. Midside nodes are ignored for
#: quality metrics because they do not define the element's shape envelope.
ELEMENT_TOPOLOGY: dict[str, tuple[int, int, str]] = {
    # element type -> (total nodes, corner nodes, family)
    "C3D4": (4, 4, "tet"),
    "C3D10": (10, 4, "tet"),
    "C3D8": (8, 8, "hex"),
    "C3D8R": (8, 8, "hex"),
    "C3D8I": (8, 8, "hex"),
    "C3D20": (20, 8, "hex"),
    "C3D20R": (20, 8, "hex"),
    "C3D6": (6, 6, "wedge"),
    "C3D15": (15, 6, "wedge"),
    "S3": (3, 3, "tri"),
    "S6": (6, 3, "tri"),
    "S4": (4, 4, "quad"),
    "S4R": (4, 4, "quad"),
    "S8": (8, 4, "quad"),
    "S8R": (8, 4, "quad"),
    "CPS3": (3, 3, "tri"),
    "CPS4": (4, 4, "quad"),
}

#: Element families that enclose a volume. Only these can carry a
#: ``*SOLID SECTION`` and only these have a meaningful Jacobian volume.
SOLID_FAMILIES = frozenset({"tet", "hex", "wedge"})


class MeshParseError(ValueError):
    """Raised when an ``.inp`` file cannot be read as a mesh."""


@dataclass(frozen=True)
class Element:
    """A single finite element."""

    eid: int
    etype: str
    nodes: tuple[int, ...]

    @property
    def family(self) -> str:
        """Topological family (``tet``, ``hex``, ``wedge``, ``tri``, ``quad``)."""
        return ELEMENT_TOPOLOGY.get(self.etype, (0, 0, "unknown"))[2]

    @property
    def corner_nodes(self) -> tuple[int, ...]:
        """Node ids defining the element's shape, excluding midside nodes."""
        corners = ELEMENT_TOPOLOGY.get(self.etype, (0, len(self.nodes), ""))[1]
        return self.nodes[:corners]

    @property
    def is_solid(self) -> bool:
        """Whether this element encloses a volume."""
        return self.family in SOLID_FAMILIES


@dataclass
class Mesh:
    """A parsed finite element mesh.

    Attributes:
        nodes: Node id -> (x, y, z) coordinates.
        elements: Element id -> :class:`Element`.
        node_sets: ``*NSET`` name -> node ids.
        element_sets: ``*ELSET`` name -> element ids.
        source: Path the mesh was read from.
    """

    nodes: dict[int, Vec3] = field(default_factory=dict)
    elements: dict[int, Element] = field(default_factory=dict)
    node_sets: dict[str, list[int]] = field(default_factory=dict)
    element_sets: dict[str, list[int]] = field(default_factory=dict)
    source: str = ""

    @property
    def node_count(self) -> int:
        """Number of nodes in the mesh."""
        return len(self.nodes)

    @property
    def element_count(self) -> int:
        """Number of elements in the mesh."""
        return len(self.elements)

    @property
    def element_types(self) -> list[str]:
        """Distinct element types present, sorted."""
        return sorted({e.etype for e in self.elements.values()})

    @property
    def solid_elements(self) -> list[Element]:
        """Every volume-enclosing element."""
        return [e for e in self.elements.values() if e.is_solid]

    def bounding_box(self) -> tuple[Vec3, Vec3]:
        """Return ``(min_corner, max_corner)`` of the nodal coordinates.

        Raises:
            MeshParseError: If the mesh has no nodes.
        """
        if not self.nodes:
            raise MeshParseError("Cannot compute a bounding box for a mesh with no nodes")

        xs = [c[0] for c in self.nodes.values()]
        ys = [c[1] for c in self.nodes.values()]
        zs = [c[2] for c in self.nodes.values()]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def characteristic_length(self) -> float:
        """Diagonal of the bounding box -- the model's overall scale in mm."""
        (x0, y0, z0), (x1, y1, z1) = self.bounding_box()
        return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)

    def resolve_node_set(self, name: str) -> list[int]:
        """Look up a node set case-insensitively.

        Raises:
            KeyError: If no set with that name exists.
        """
        lowered = name.strip().lower()
        for set_name, ids in self.node_sets.items():
            if set_name.lower() == lowered:
                return ids
        raise KeyError(name)

    def resolve_element_set(self, name: str) -> list[int]:
        """Look up an element set case-insensitively.

        Raises:
            KeyError: If no set with that name exists.
        """
        lowered = name.strip().lower()
        for set_name, ids in self.element_sets.items():
            if set_name.lower() == lowered:
                return ids
        raise KeyError(name)


def _parse_keyword(line: str) -> tuple[str, dict[str, str]]:
    """Split a ``*KEYWORD, PARAM=VALUE`` line into its name and parameters."""
    body = line.strip().lstrip("*")
    parts = [p.strip() for p in body.split(",")]
    keyword = parts[0].upper()

    params: dict[str, str] = {}
    for part in parts[1:]:
        if not part:
            continue
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip().upper()] = value.strip()
        else:
            params[part.upper()] = ""

    return keyword, params


def _iter_data_lines(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect the data lines following a keyword, honouring continuations.

    A data line ending in ``,`` continues onto the next physical line -- a
    C3D20 connectivity record spans three of them. Returns the logical records
    and the index of the next keyword line.
    """
    records: list[str] = []
    pending = ""
    index = start

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()

        if stripped.startswith("**"):
            index += 1
            continue
        if stripped.startswith("*"):
            break
        if not stripped:
            index += 1
            continue

        pending += stripped
        index += 1

        if pending.endswith(","):
            continue

        records.append(pending)
        pending = ""

    if pending:
        records.append(pending)

    return records, index


def _split_fields(record: str) -> list[str]:
    """Split a comma-separated data record into non-empty trimmed fields."""
    return [f.strip() for f in record.split(",") if f.strip()]


def parse_inp_mesh(mesh_file: str | Path) -> Mesh:
    """Read nodes, elements and named sets from a CalculiX ``.inp`` file.

    Unknown keywords -- including any physics already present in the file -- are
    skipped rather than rejected, so a deck that has been solved before still
    parses as a mesh.

    Args:
        mesh_file: Path to the ``.inp`` file.

    Returns:
        The parsed :class:`Mesh`.

    Raises:
        FileNotFoundError: If the file does not exist.
        MeshParseError: If the file contains no nodes or no elements.
    """
    path = Path(mesh_file)
    if not path.exists():
        raise FileNotFoundError(f"Mesh file not found: {mesh_file}")

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    mesh = Mesh(source=str(path))

    index = 0
    malformed = 0

    while index < len(lines):
        stripped = lines[index].strip()

        if not stripped or stripped.startswith("**"):
            index += 1
            continue
        if not stripped.startswith("*"):
            index += 1
            continue

        keyword, params = _parse_keyword(stripped)
        records, index = _iter_data_lines(lines, index + 1)

        if keyword == "NODE":
            set_name = params.get("NSET")
            collected: list[int] = []
            for record in records:
                fields = _split_fields(record)
                if len(fields) < 4:
                    malformed += 1
                    continue
                try:
                    nid = int(float(fields[0]))
                    mesh.nodes[nid] = (
                        float(fields[1]),
                        float(fields[2]),
                        float(fields[3]),
                    )
                    collected.append(nid)
                except ValueError:
                    malformed += 1
            if set_name and collected:
                mesh.node_sets.setdefault(set_name, []).extend(collected)

        elif keyword == "ELEMENT":
            etype = params.get("TYPE", "").upper()
            set_name = params.get("ELSET")
            collected_elems: list[int] = []
            for record in records:
                fields = _split_fields(record)
                if len(fields) < 2:
                    malformed += 1
                    continue
                try:
                    eid = int(fields[0])
                    connectivity = tuple(int(f) for f in fields[1:])
                except ValueError:
                    malformed += 1
                    continue
                mesh.elements[eid] = Element(eid=eid, etype=etype, nodes=connectivity)
                collected_elems.append(eid)
            if set_name and collected_elems:
                mesh.element_sets.setdefault(set_name, []).extend(collected_elems)

        elif keyword == "NSET":
            set_name = params.get("NSET", "")
            if set_name:
                ids = _expand_set_records(records, mesh.node_sets, params)
                mesh.node_sets.setdefault(set_name, []).extend(ids)

        elif keyword == "ELSET":
            set_name = params.get("ELSET", "")
            if set_name:
                ids = _expand_set_records(records, mesh.element_sets, params)
                mesh.element_sets.setdefault(set_name, []).extend(ids)

    if malformed:
        logger.warning(
            "Skipped malformed mesh records",
            mesh_file=str(path),
            skipped=malformed,
        )

    if not mesh.nodes:
        raise MeshParseError(f"No nodes found in mesh file: {mesh_file}")
    if not mesh.elements:
        raise MeshParseError(f"No elements found in mesh file: {mesh_file}")

    logger.info(
        "Parsed mesh",
        mesh_file=str(path),
        nodes=mesh.node_count,
        elements=mesh.element_count,
        element_types=mesh.element_types,
    )
    return mesh


def _expand_set_records(
    records: list[str],
    existing: dict[str, list[int]],
    params: dict[str, str],
) -> list[int]:
    """Expand ``*NSET``/``*ELSET`` members, including ranges and set references.

    Handles the ``GENERATE`` form (``first, last, increment``) and members that
    name another set rather than an id.
    """
    ids: list[int] = []

    if "GENERATE" in params:
        for record in records:
            fields = _split_fields(record)
            if len(fields) < 2:
                continue
            try:
                first = int(fields[0])
                last = int(fields[1])
                step = int(fields[2]) if len(fields) > 2 else 1
            except ValueError:
                continue
            if step <= 0:
                continue
            ids.extend(range(first, last + 1, step))
        return ids

    for record in records:
        for member in _split_fields(record):
            try:
                ids.append(int(member))
            except ValueError:
                for name, members in existing.items():
                    if name.lower() == member.lower():
                        ids.extend(members)
                        break

    return ids


def select_nodes_on_face(
    mesh: Mesh,
    face: str,
    tolerance: float | None = None,
) -> list[int]:
    """Select the nodes lying on one bounding-box face of the mesh.

    Meshers rarely emit named sets for the faces an engineer wants to constrain
    or load, so a load case can name a face (``"zmin"``, ``"xmax"``, ...) and
    have the nodes picked geometrically.

    Args:
        mesh: The mesh to select from.
        face: One of ``xmin``, ``xmax``, ``ymin``, ``ymax``, ``zmin``, ``zmax``.
        tolerance: Absolute distance from the plane, in mm. Defaults to 0.1% of
            the bounding-box diagonal, which tracks a coordinate round-trip
            through STEP/mesh export without catching a second element layer.

    Returns:
        Sorted node ids on that face.

    Raises:
        ValueError: If ``face`` is not a recognised face name.
    """
    axis_index = {"x": 0, "y": 1, "z": 2}
    normalized = face.strip().lower().replace("_", "").replace("-", "")

    if len(normalized) < 4 or normalized[0] not in axis_index:
        raise ValueError(
            f"Unknown face '{face}'. Expected one of: xmin, xmax, ymin, ymax, zmin, zmax"
        )

    axis = axis_index[normalized[0]]
    side = normalized[1:]
    if side not in ("min", "max"):
        raise ValueError(
            f"Unknown face '{face}'. Expected one of: xmin, xmax, ymin, ymax, zmin, zmax"
        )

    low, high = mesh.bounding_box()
    target = low[axis] if side == "min" else high[axis]

    if tolerance is None:
        tolerance = max(mesh.characteristic_length() * 1e-3, 1e-9)

    selected = [nid for nid, coord in mesh.nodes.items() if abs(coord[axis] - target) <= tolerance]

    logger.debug(
        "Selected nodes on face",
        face=normalized,
        selected=len(selected),
        tolerance=tolerance,
    )
    return sorted(selected)
