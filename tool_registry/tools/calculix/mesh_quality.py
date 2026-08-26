"""Element-geometry quality metrics for finite element meshes.

Mesh quality is the dominant error source in a linear FEA result. A sliver
tetrahedron -- one whose four nodes are nearly coplanar -- has a near-singular
Jacobian, and the stresses it reports can be wrong by an order of magnitude
while the solver still converges and exits zero. Counting nodes and elements
cannot detect that; measuring the elements can.

Every metric here is computed from actual nodal coordinates:

``aspect_ratio``
    Longest edge divided by shortest edge. ``1.0`` for a regular element and
    unbounded as the element degenerates. This is the ratio the meshing skill's
    ``max_aspect_ratio_threshold`` is expressed against.

``dihedral angles``
    Angles between adjacent faces of a tetrahedron, in degrees. A regular tet
    measures 70.53 degrees at every one; a sliver drives the minimum toward 0
    and the maximum toward 180. The single most reliable sliver detector.

``scaled_jacobian``
    Determinant of the corner Jacobian normalised by its edge lengths, in
    ``[-1, 1]``. ``1.0`` is a perfectly shaped element, values at or below 0
    mean the element is degenerate or inverted and its results are meaningless.

``volume``
    Signed element volume. Negative means inverted connectivity -- the solver
    may still run, but the element contributes negative stiffness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import structlog

from tool_registry.tools.calculix.mesh import Element, Mesh, Vec3

logger = structlog.get_logger(__name__)

#: Below this dihedral angle a tetrahedron is treated as a sliver.
SLIVER_MIN_ANGLE_DEG = 5.0

#: Scaled Jacobian at or below this value indicates a degenerate element.
DEGENERATE_JACOBIAN = 0.0

#: Quality below this normalised score is reported as a poorly shaped element.
POOR_QUALITY_SCORE = 0.2

#: Corner-node index triples defining the four faces of a tetrahedron, wound so
#: every normal points outward for a positively-oriented element.
_TET_FACES: tuple[tuple[int, int, int], ...] = (
    (0, 2, 1),
    (0, 1, 3),
    (1, 2, 3),
    (0, 3, 2),
)

#: Corner-node index pairs for the six edges of a tetrahedron.
_TET_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1),
    (0, 2),
    (0, 3),
    (1, 2),
    (1, 3),
    (2, 3),
)

#: For each tetrahedron corner, the three neighbouring corners whose edge
#: vectors form the local Jacobian frame, wound to keep the determinant
#: positive for a correctly-oriented element.
_TET_CORNER_FRAMES: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 2, 3),
    (1, 0, 3, 2),
    (2, 0, 1, 3),
    (3, 0, 2, 1),
)

#: Corner-node index pairs for the twelve edges of a hexahedron, in CalculiX
#: node ordering (bottom face 0-3, top face 4-7).
_HEX_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 0),
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 4),
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
)

#: For each hexahedron corner, the three neighbouring corners whose edge vectors
#: form the local Jacobian frame.
_HEX_CORNER_FRAMES: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 3, 4),
    (1, 2, 0, 5),
    (2, 3, 1, 6),
    (3, 0, 2, 7),
    (4, 7, 5, 0),
    (5, 4, 6, 1),
    (6, 5, 7, 2),
    (7, 6, 4, 3),
)


def _sub(a: Vec3, b: Vec3) -> Vec3:
    """Vector ``a - b``."""
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    """Cross product ``a x b``."""
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec3, b: Vec3) -> float:
    """Dot product ``a . b``."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    """Euclidean length of ``a``."""
    return math.sqrt(_dot(a, a))


@dataclass
class ElementQuality:
    """Quality metrics for one element."""

    eid: int
    etype: str
    volume: float = 0.0
    aspect_ratio: float = 0.0
    #: Dihedral angles are defined for tetrahedra; None means this element
    #: family was not angle-measured, which is not the same as measuring zero.
    min_angle_deg: float | None = None
    max_angle_deg: float | None = None
    scaled_jacobian: float = 0.0
    quality: float = 0.0
    issues: list[str] = field(default_factory=list)

    @property
    def is_degenerate(self) -> bool:
        """Whether this element's results should not be trusted."""
        return bool(self.issues)


@dataclass
class MeshQualityReport:
    """Aggregate mesh quality across every measurable element."""

    node_count: int = 0
    element_count: int = 0
    measured_count: int = 0
    element_types: list[str] = field(default_factory=list)
    max_aspect_ratio: float = 0.0
    avg_aspect_ratio: float = 0.0
    min_angle_deg: float | None = None
    max_angle_deg: float | None = None
    min_scaled_jacobian: float = 0.0
    avg_quality: float = 0.0
    total_volume: float = 0.0
    inverted_elements: list[int] = field(default_factory=list)
    sliver_elements: list[int] = field(default_factory=list)
    degenerate_elements: list[int] = field(default_factory=list)
    worst_elements: list[ElementQuality] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Render the report as the MCP tool's JSON response body."""
        return {
            "valid": self.valid,
            "node_count": self.node_count,
            "element_count": self.element_count,
            "measured_element_count": self.measured_count,
            "element_types": self.element_types,
            "max_aspect_ratio": round(self.max_aspect_ratio, 4),
            "avg_aspect_ratio": round(self.avg_aspect_ratio, 4),
            # None rather than 0.0 when no element family in this mesh has a
            # defined dihedral angle -- a hexahedral mesh reporting "0 degrees"
            # would read as fully degenerate.
            "min_angle": None if self.min_angle_deg is None else round(self.min_angle_deg, 4),
            "max_angle": None if self.max_angle_deg is None else round(self.max_angle_deg, 4),
            "min_scaled_jacobian": round(self.min_scaled_jacobian, 6),
            "avg_quality": round(self.avg_quality, 4),
            "total_volume_mm3": round(self.total_volume, 6),
            "inverted_element_count": len(self.inverted_elements),
            "sliver_element_count": len(self.sliver_elements),
            "degenerate_element_count": len(self.degenerate_elements),
            "worst_elements": [
                {
                    "element_id": q.eid,
                    "element_type": q.etype,
                    "aspect_ratio": round(q.aspect_ratio, 4),
                    "min_angle": None if q.min_angle_deg is None else round(q.min_angle_deg, 4),
                    "scaled_jacobian": round(q.scaled_jacobian, 6),
                    "quality": round(q.quality, 4),
                    "issues": q.issues,
                }
                for q in self.worst_elements
            ],
            "issues": self.issues,
        }


def _edge_lengths(points: list[Vec3], edges: tuple[tuple[int, int], ...]) -> list[float]:
    """Lengths of the given edges of a corner-point list."""
    return [_norm(_sub(points[a], points[b])) for a, b in edges]


def tet_volume(p0: Vec3, p1: Vec3, p2: Vec3, p3: Vec3) -> float:
    """Signed volume of a tetrahedron.

    Positive for CalculiX's expected node ordering; negative means the element's
    connectivity is inverted.
    """
    return _dot(_sub(p1, p0), _cross(_sub(p2, p0), _sub(p3, p0))) / 6.0


def tet_dihedral_angles(points: list[Vec3]) -> list[float]:
    """Return the six dihedral angles of a tetrahedron, in degrees.

    Each angle is measured between the two faces sharing an edge, computed from
    the outward face normals.
    """
    normals: list[Vec3] = []
    for i, j, k in _TET_FACES:
        normal = _cross(_sub(points[j], points[i]), _sub(points[k], points[i]))
        length = _norm(normal)
        if length <= 0.0:
            return []
        normals.append((normal[0] / length, normal[1] / length, normal[2] / length))

    angles: list[float] = []
    for a in range(len(normals)):
        for b in range(a + 1, len(normals)):
            # Adjacent faces share an edge; the dihedral angle along it is the
            # supplement of the angle between the outward normals.
            cosine = max(-1.0, min(1.0, _dot(normals[a], normals[b])))
            angles.append(180.0 - math.degrees(math.acos(cosine)))

    return angles


def tet_scaled_jacobian(points: list[Vec3]) -> float:
    """Minimum scaled Jacobian across the four corners of a tetrahedron.

    Sampling every corner rather than just the first is what bounds the metric
    at 1.0: a right-angled corner tetrahedron has an orthogonal frame at one
    vertex (which alone would score above 1.0) and three worse ones, and it is
    the worst corner that governs how the element behaves.

    A regular tetrahedron scores 1.0, a right-angled corner tet 0.707, and a
    sliver approaches 0.
    """
    worst = float("inf")

    for origin, na, nb, nc in _TET_CORNER_FRAMES:
        e1 = _sub(points[na], points[origin])
        e2 = _sub(points[nb], points[origin])
        e3 = _sub(points[nc], points[origin])

        lengths = _norm(e1) * _norm(e2) * _norm(e3)
        if lengths <= 0.0:
            return 0.0

        worst = min(worst, _dot(e1, _cross(e2, e3)) / lengths)

    if worst == float("inf"):
        return 0.0

    # A regular tetrahedron's normalised determinant is sqrt(2)/2; dividing by
    # it puts a perfect element at exactly 1.0.
    return worst / (math.sqrt(2.0) / 2.0)


def hex_scaled_jacobian(points: list[Vec3]) -> float:
    """Minimum scaled Jacobian across the eight corners of a hexahedron."""
    worst = float("inf")

    for corner, na, nb, nc in _HEX_CORNER_FRAMES:
        origin = points[corner]
        e1 = _sub(points[na], origin)
        e2 = _sub(points[nb], origin)
        e3 = _sub(points[nc], origin)

        lengths = _norm(e1) * _norm(e2) * _norm(e3)
        if lengths <= 0.0:
            return 0.0

        worst = min(worst, _dot(e1, _cross(e2, e3)) / lengths)

    return worst if worst != float("inf") else 0.0


def _corner_points(mesh: Mesh, element: Element) -> list[Vec3] | None:
    """Resolve an element's corner node ids to coordinates, or ``None``."""
    points: list[Vec3] = []
    for nid in element.corner_nodes:
        coord = mesh.nodes.get(nid)
        if coord is None:
            return None
        points.append(coord)
    return points


def evaluate_element(mesh: Mesh, element: Element) -> ElementQuality | None:
    """Measure one element's geometry.

    Returns:
        The element's metrics, or ``None`` if the element is not a supported
        solid type or references nodes the mesh does not contain.
    """
    if not element.is_solid:
        return None

    points = _corner_points(mesh, element)
    if points is None:
        logger.warning(
            "Element references missing nodes",
            element_id=element.eid,
            element_type=element.etype,
        )
        return None

    quality = ElementQuality(eid=element.eid, etype=element.etype)
    family = element.family

    if family == "tet" and len(points) >= 4:
        quality.volume = tet_volume(points[0], points[1], points[2], points[3])
        edges = _edge_lengths(points, _TET_EDGES)
        angles = tet_dihedral_angles(points)
        quality.scaled_jacobian = tet_scaled_jacobian(points)
        if angles:
            quality.min_angle_deg = min(angles)
            quality.max_angle_deg = max(angles)
    elif family == "hex" and len(points) >= 8:
        edges = _edge_lengths(points, _HEX_EDGES)
        quality.scaled_jacobian = hex_scaled_jacobian(points)
        # Decompose into the six tetrahedra sharing the body diagonal 0-6.
        quality.volume = sum(
            tet_volume(points[a], points[b], points[c], points[d])
            for a, b, c, d in (
                (0, 1, 2, 6),
                (0, 2, 3, 6),
                (0, 3, 7, 6),
                (0, 7, 4, 6),
                (0, 4, 5, 6),
                (0, 5, 1, 6),
            )
        )
    elif family == "wedge" and len(points) >= 6:
        wedge_edges = ((0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5))
        edges = _edge_lengths(points, wedge_edges)
        quality.volume = sum(
            tet_volume(points[a], points[b], points[c], points[d])
            for a, b, c, d in ((0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5))
        )
        quality.scaled_jacobian = tet_scaled_jacobian([points[0], points[1], points[2], points[3]])
    else:
        return None

    shortest = min(edges) if edges else 0.0
    longest = max(edges) if edges else 0.0
    quality.aspect_ratio = longest / shortest if shortest > 0.0 else float("inf")

    quality.quality = _quality_score(quality)
    quality.issues = _element_issues(quality)
    return quality


def _quality_score(quality: ElementQuality) -> float:
    """Collapse an element's metrics into a single ``[0, 1]`` score.

    The score is the worse of the shape score (inverse aspect ratio) and the
    magnitude of the scaled Jacobian, so one bad dimension cannot be averaged
    away by a good one.
    """
    if quality.aspect_ratio <= 0.0 or math.isinf(quality.aspect_ratio):
        return 0.0

    shape_score = 1.0 / quality.aspect_ratio
    jacobian_score = max(0.0, min(1.0, quality.scaled_jacobian))
    return round(min(shape_score, jacobian_score), 6)


def _element_issues(quality: ElementQuality) -> list[str]:
    """List the reasons an element's results should not be trusted."""
    issues: list[str] = []

    if quality.volume < 0.0:
        issues.append("inverted (negative volume)")
    elif quality.volume == 0.0:
        issues.append("zero volume")

    if quality.scaled_jacobian <= DEGENERATE_JACOBIAN:
        issues.append(f"degenerate Jacobian ({quality.scaled_jacobian:.4f})")

    if quality.min_angle_deg is not None and quality.min_angle_deg < SLIVER_MIN_ANGLE_DEG:
        issues.append(f"sliver (min dihedral angle {quality.min_angle_deg:.2f} deg)")

    return issues


def evaluate_mesh(
    mesh: Mesh,
    max_aspect_ratio: float = 10.0,
    min_angle_deg: float = 15.0,
    worst_element_limit: int = 10,
) -> MeshQualityReport:
    """Measure every solid element and summarise the mesh.

    Args:
        mesh: The parsed mesh.
        max_aspect_ratio: Aspect ratio above which the mesh is rejected.
        min_angle_deg: Minimum acceptable dihedral angle, in degrees.
        worst_element_limit: How many of the worst elements to report
            individually, so a caller can refine them instead of re-meshing.

    Returns:
        The aggregate :class:`MeshQualityReport`.
    """
    report = MeshQualityReport(
        node_count=mesh.node_count,
        element_count=mesh.element_count,
        element_types=mesh.element_types,
    )

    measured: list[ElementQuality] = []
    for element in mesh.elements.values():
        result = evaluate_element(mesh, element)
        if result is not None:
            measured.append(result)

    report.measured_count = len(measured)

    if not measured:
        report.valid = False
        report.issues.append(
            "No measurable solid elements found -- mesh quality could not be assessed"
        )
        logger.warning("Mesh has no measurable solid elements", mesh_file=mesh.source)
        return report

    aspect_ratios = [q.aspect_ratio for q in measured if not math.isinf(q.aspect_ratio)]
    angles = [q.min_angle_deg for q in measured if q.min_angle_deg is not None]
    max_angles = [q.max_angle_deg for q in measured if q.max_angle_deg is not None]

    report.max_aspect_ratio = max(aspect_ratios) if aspect_ratios else float("inf")
    report.avg_aspect_ratio = (
        sum(aspect_ratios) / len(aspect_ratios) if aspect_ratios else float("inf")
    )
    report.min_angle_deg = min(angles) if angles else None
    report.max_angle_deg = max(max_angles) if max_angles else None
    report.min_scaled_jacobian = min(q.scaled_jacobian for q in measured)
    report.avg_quality = sum(q.quality for q in measured) / len(measured)
    report.total_volume = sum(abs(q.volume) for q in measured)

    report.inverted_elements = [q.eid for q in measured if q.volume < 0.0]
    report.sliver_elements = [
        q.eid
        for q in measured
        if q.min_angle_deg is not None and q.min_angle_deg < SLIVER_MIN_ANGLE_DEG
    ]
    report.degenerate_elements = [q.eid for q in measured if q.is_degenerate]

    report.worst_elements = sorted(measured, key=lambda q: q.quality)[:worst_element_limit]

    if report.inverted_elements:
        report.issues.append(
            f"{len(report.inverted_elements)} element(s) have inverted connectivity "
            "(negative volume)"
        )
    if report.sliver_elements:
        report.issues.append(
            f"{len(report.sliver_elements)} sliver element(s) below "
            f"{SLIVER_MIN_ANGLE_DEG} deg dihedral angle"
        )
    if report.max_aspect_ratio > max_aspect_ratio:
        report.issues.append(
            f"Max aspect ratio {report.max_aspect_ratio:.2f} exceeds "
            f"threshold {max_aspect_ratio:.2f}"
        )
    if report.min_angle_deg is not None and report.min_angle_deg < min_angle_deg:
        report.issues.append(
            f"Min dihedral angle {report.min_angle_deg:.2f} deg is below "
            f"threshold {min_angle_deg:.2f} deg"
        )
    if report.min_scaled_jacobian <= DEGENERATE_JACOBIAN:
        report.issues.append(
            f"Minimum scaled Jacobian {report.min_scaled_jacobian:.4f} indicates "
            "degenerate elements -- stress results in those elements are unreliable"
        )
    if report.avg_quality < POOR_QUALITY_SCORE:
        report.issues.append(
            f"Average element quality {report.avg_quality:.3f} is below "
            f"{POOR_QUALITY_SCORE} -- consider re-meshing with a smaller element size"
        )

    report.valid = not report.issues

    logger.info(
        "Evaluated mesh quality",
        mesh_file=mesh.source,
        elements=report.element_count,
        measured=report.measured_count,
        max_aspect_ratio=round(report.max_aspect_ratio, 3),
        min_angle_deg=(None if report.min_angle_deg is None else round(report.min_angle_deg, 3)),
        avg_quality=round(report.avg_quality, 3),
        valid=report.valid,
    )
    return report
