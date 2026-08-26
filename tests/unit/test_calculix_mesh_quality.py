"""Unit tests for element-geometry mesh quality metrics.

Every metric is asserted against geometry whose answer is known analytically --
a regular tetrahedron's dihedral angle is arccos(1/3) = 70.5288 degrees, a unit
cube has volume 1 and scaled Jacobian 1 -- rather than against the
implementation's own output.
"""

from __future__ import annotations

import math

import pytest

from tool_registry.tools.calculix.mesh import Element, Mesh
from tool_registry.tools.calculix.mesh_quality import (
    evaluate_element,
    evaluate_mesh,
    hex_scaled_jacobian,
    tet_dihedral_angles,
    tet_scaled_jacobian,
    tet_volume,
)

#: A regular tetrahedron, positively oriented. Every dihedral angle is
#: arccos(1/3); volume is 8/3 for this coordinate set.
REGULAR_TET = [(1.0, 1.0, 1.0), (-1.0, 1.0, -1.0), (1.0, -1.0, -1.0), (-1.0, -1.0, 1.0)]

#: The unit corner tetrahedron: three orthogonal unit edges from the origin.
CORNER_TET = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]

#: Four nearly-coplanar nodes -- the classic sliver.
SLIVER_TET = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.3, 0.3, 0.001)]

#: A unit cube in CalculiX C3D8 node order: bottom face, then top face.
UNIT_CUBE = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (1.0, 0.0, 1.0),
    (1.0, 1.0, 1.0),
    (0.0, 1.0, 1.0),
]

REGULAR_TET_DIHEDRAL_DEG = math.degrees(math.acos(1.0 / 3.0))


def make_mesh(points: list[tuple[float, float, float]], etype: str) -> Mesh:
    """Build a single-element mesh from a corner point list."""
    nodes = {i + 1: p for i, p in enumerate(points)}
    connectivity = tuple(range(1, len(points) + 1))
    return Mesh(nodes=nodes, elements={1: Element(1, etype, connectivity)})


class TestTetPrimitives:
    def test_regular_tet_volume(self) -> None:
        assert tet_volume(*REGULAR_TET) == pytest.approx(8.0 / 3.0)

    def test_corner_tet_volume_is_one_sixth(self) -> None:
        assert tet_volume(*CORNER_TET) == pytest.approx(1.0 / 6.0)

    def test_reversed_winding_gives_negative_volume(self) -> None:
        flipped = [CORNER_TET[0], CORNER_TET[2], CORNER_TET[1], CORNER_TET[3]]
        assert tet_volume(*flipped) == pytest.approx(-1.0 / 6.0)

    def test_regular_tet_dihedral_angles_match_arccos_one_third(self) -> None:
        angles = tet_dihedral_angles(REGULAR_TET)
        assert len(angles) == 6
        for angle in angles:
            assert angle == pytest.approx(REGULAR_TET_DIHEDRAL_DEG, abs=1e-6)

    def test_corner_tet_has_three_right_angles(self) -> None:
        angles = sorted(tet_dihedral_angles(CORNER_TET))
        assert angles[3:] == pytest.approx([90.0, 90.0, 90.0])

    def test_degenerate_tet_has_no_dihedral_angles(self) -> None:
        """Coincident nodes give a zero-length normal, not a crash."""
        assert tet_dihedral_angles([(0.0, 0.0, 0.0)] * 4) == []

    def test_regular_tet_scaled_jacobian_is_one(self) -> None:
        assert tet_scaled_jacobian(REGULAR_TET) == pytest.approx(1.0)

    def test_scaled_jacobian_is_bounded_by_one(self) -> None:
        """Sampling every corner is what keeps a right-angled tet under 1.0."""
        assert tet_scaled_jacobian(CORNER_TET) == pytest.approx(math.sqrt(2.0) / 2.0)

    def test_sliver_scaled_jacobian_approaches_zero(self) -> None:
        assert tet_scaled_jacobian(SLIVER_TET) < 0.01


class TestHexPrimitives:
    def test_unit_cube_scaled_jacobian_is_one(self) -> None:
        assert hex_scaled_jacobian(UNIT_CUBE) == pytest.approx(1.0)

    def test_sheared_hex_scores_worse_than_a_cube(self) -> None:
        """The scaled Jacobian measures angular skew, so shearing degrades it."""
        sheared = [(x + z * 2.0, y, z) for x, y, z in UNIT_CUBE]
        assert hex_scaled_jacobian(sheared) < hex_scaled_jacobian(UNIT_CUBE)

    def test_uniformly_squashed_hex_keeps_a_perfect_jacobian(self) -> None:
        """A thin box is still rectangular: every corner frame stays orthogonal.

        This is the division of labour between the two metrics -- the scaled
        Jacobian sees skew, and aspect ratio is what catches thinness. Reporting
        only one of them would miss half the bad meshes.
        """
        squashed = [(x, y, z * 0.01) for x, y, z in UNIT_CUBE]
        assert hex_scaled_jacobian(squashed) == pytest.approx(1.0)

        mesh = make_mesh(squashed, "C3D8")
        quality = evaluate_element(mesh, mesh.elements[1])
        assert quality is not None
        assert quality.aspect_ratio == pytest.approx(100.0)


class TestEvaluateElement:
    def test_regular_tet_is_perfect(self) -> None:
        mesh = make_mesh(REGULAR_TET, "C3D4")
        quality = evaluate_element(mesh, mesh.elements[1])
        assert quality is not None
        assert quality.aspect_ratio == pytest.approx(1.0)
        assert quality.quality == pytest.approx(1.0)
        assert quality.issues == []

    def test_unit_cube_volume_and_quality(self) -> None:
        mesh = make_mesh(UNIT_CUBE, "C3D8")
        quality = evaluate_element(mesh, mesh.elements[1])
        assert quality is not None
        assert quality.volume == pytest.approx(1.0)
        assert quality.aspect_ratio == pytest.approx(1.0)
        assert quality.issues == []

    def test_sliver_is_flagged(self) -> None:
        mesh = make_mesh(SLIVER_TET, "C3D4")
        quality = evaluate_element(mesh, mesh.elements[1])
        assert quality is not None
        assert quality.is_degenerate
        assert any("sliver" in issue for issue in quality.issues)

    def test_inverted_element_is_flagged(self) -> None:
        flipped = [CORNER_TET[0], CORNER_TET[2], CORNER_TET[1], CORNER_TET[3]]
        mesh = make_mesh(flipped, "C3D4")
        quality = evaluate_element(mesh, mesh.elements[1])
        assert quality is not None
        assert any("inverted" in issue for issue in quality.issues)

    def test_shell_element_is_not_measured(self) -> None:
        mesh = make_mesh(CORNER_TET[:3], "S3")
        assert evaluate_element(mesh, mesh.elements[1]) is None

    def test_element_referencing_missing_node_is_skipped(self) -> None:
        mesh = make_mesh(CORNER_TET, "C3D4")
        mesh.elements[1] = Element(1, "C3D4", (1, 2, 3, 99))
        assert evaluate_element(mesh, mesh.elements[1]) is None

    def test_quadratic_tet_is_measured_on_its_corners(self) -> None:
        """Midside nodes do not define the shape envelope."""
        nodes = {i + 1: p for i, p in enumerate(REGULAR_TET)}
        for extra in range(5, 11):
            nodes[extra] = (0.0, 0.0, 0.0)
        mesh = Mesh(nodes=nodes, elements={1: Element(1, "C3D10", tuple(range(1, 11)))})
        quality = evaluate_element(mesh, mesh.elements[1])
        assert quality is not None
        assert quality.quality == pytest.approx(1.0)


class TestEvaluateMesh:
    def test_perfect_mesh_is_valid(self) -> None:
        report = evaluate_mesh(make_mesh(REGULAR_TET, "C3D4"))
        assert report.valid
        assert report.issues == []
        assert report.measured_count == 1

    def test_sliver_mesh_is_invalid_and_explains_why(self) -> None:
        report = evaluate_mesh(make_mesh(SLIVER_TET, "C3D4"))
        assert not report.valid
        assert report.sliver_elements == [1]
        assert any("sliver" in issue for issue in report.issues)

    def test_aspect_ratio_threshold_is_enforced(self) -> None:
        stretched = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
        report = evaluate_mesh(make_mesh(stretched, "C3D4"), max_aspect_ratio=5.0)
        assert not report.valid
        assert any("aspect ratio" in issue.lower() for issue in report.issues)

    def test_hex_mesh_reports_no_dihedral_angle_rather_than_zero(self) -> None:
        """0 degrees would read as fully degenerate; None means not measured."""
        report = evaluate_mesh(make_mesh(UNIT_CUBE, "C3D8"))
        assert report.min_angle_deg is None
        assert report.to_dict()["min_angle"] is None
        assert report.sliver_elements == []
        assert report.valid

    def test_mesh_with_only_shells_is_reported_unmeasurable(self) -> None:
        report = evaluate_mesh(make_mesh(CORNER_TET[:3], "S3"))
        assert not report.valid
        assert report.measured_count == 0
        assert any("no measurable" in issue.lower() for issue in report.issues)

    def test_total_volume_sums_elements(self) -> None:
        report = evaluate_mesh(make_mesh(UNIT_CUBE, "C3D8"))
        assert report.total_volume == pytest.approx(1.0)

    def test_worst_elements_are_ranked_and_capped(self) -> None:
        nodes: dict[int, tuple[float, float, float]] = {}
        elements: dict[int, Element] = {}
        for index in range(5):
            base = index * 4
            offset = float(index)
            for local, point in enumerate(CORNER_TET):
                nodes[base + local + 1] = (point[0] + offset, point[1], point[2])
            elements[index + 1] = Element(index + 1, "C3D4", tuple(base + n for n in range(1, 5)))
        report = evaluate_mesh(Mesh(nodes=nodes, elements=elements), worst_element_limit=3)
        assert len(report.worst_elements) == 3
        qualities = [q.quality for q in report.worst_elements]
        assert qualities == sorted(qualities)

    def test_report_dict_is_json_friendly(self) -> None:
        payload = evaluate_mesh(make_mesh(REGULAR_TET, "C3D4")).to_dict()
        assert payload["valid"] is True
        assert payload["element_count"] == 1
        assert payload["min_angle"] == pytest.approx(REGULAR_TET_DIHEDRAL_DEG, abs=1e-3)
        assert isinstance(payload["worst_elements"], list)
