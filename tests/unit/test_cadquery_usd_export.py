"""Unit tests for USD (.usda) export -- STL parsing, axis-aligned inertia
check, and .usda text generation (see usd_export.py's module docstring for
why this hand-authors USD text rather than depending on usd-core/pxr)."""

from __future__ import annotations

import math
import struct

import pytest

from tool_registry.tools.cadquery.joints import (
    MissingJointLimitsError,
    UnsupportedJointTypeError,
)
from tool_registry.tools.cadquery.usd_export import (
    NonAxisAlignedInertiaError,
    _check_axis_aligned,
    _looks_like_binary_stl,
    _quat_align_x_to,
    build_usda,
    build_usda_assembly,
    parse_stl_mesh,
)

_TRIANGLE = (
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
)


def _make_binary_stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    header = b"\x00" * 80
    body = struct.pack("<I", len(triangles))
    for tri in triangles:
        body += struct.pack("<3f", 0.0, 0.0, 1.0)  # normal (unused by the parser)
        for vertex in tri:
            body += struct.pack("<3f", *vertex)
        body += struct.pack("<H", 0)  # attribute byte count
    return header + body


def _make_ascii_stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> str:
    lines = ["solid test"]
    for tri in triangles:
        lines.append("  facet normal 0 0 1")
        lines.append("    outer loop")
        for x, y, z in tri:
            lines.append(f"      vertex {x} {y} {z}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    return "\n".join(lines)


class TestBinaryStlDetectionAndParsing:
    def test_looks_like_binary_stl_true_for_well_formed_binary(self):
        data = _make_binary_stl([_TRIANGLE])
        assert _looks_like_binary_stl(data)

    def test_looks_like_binary_stl_false_for_ascii(self):
        data = _make_ascii_stl([_TRIANGLE]).encode("ascii")
        assert not _looks_like_binary_stl(data)

    def test_parse_binary_stl_one_triangle(self, tmp_path):
        path = tmp_path / "tri.stl"
        path.write_bytes(_make_binary_stl([_TRIANGLE]))
        points, indices, counts = parse_stl_mesh(str(path))
        assert points == list(_TRIANGLE)
        assert indices == [0, 1, 2]
        assert counts == [3]

    def test_parse_binary_stl_two_triangles(self, tmp_path):
        tri2 = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (1.0, 2.0, 0.0))
        path = tmp_path / "two.stl"
        path.write_bytes(_make_binary_stl([_TRIANGLE, tri2]))
        points, indices, counts = parse_stl_mesh(str(path))
        assert len(points) == 6
        assert indices == [0, 1, 2, 3, 4, 5]
        assert counts == [3, 3]


class TestAsciiStlParsing:
    def test_parse_ascii_stl_one_triangle(self, tmp_path):
        path = tmp_path / "tri.stl"
        path.write_text(_make_ascii_stl([_TRIANGLE]))
        points, indices, counts = parse_stl_mesh(str(path))
        assert points == list(_TRIANGLE)
        assert indices == [0, 1, 2]
        assert counts == [3]


class TestAxisAlignedCheck:
    def test_diagonal_only_passes(self):
        _check_axis_aligned(1.0, 0.0, 0.0, 2.0, 0.0, 3.0)  # should not raise

    def test_negligible_off_diagonal_passes(self):
        _check_axis_aligned(1.0, 1e-12, 0.0, 2.0, -1e-12, 3.0)  # should not raise

    def test_significant_off_diagonal_raises(self):
        with pytest.raises(NonAxisAlignedInertiaError):
            _check_axis_aligned(1.0, 0.5, 0.0, 2.0, 0.0, 3.0)


class TestBuildUsda:
    def test_writes_expected_structure(self):
        text = build_usda(
            prim_name="widget",
            points=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            face_vertex_indices=[0, 1, 2],
            face_vertex_counts=[3],
            mass_kg=2.5,
            com_m=(0.01, 0.02, 0.03),
            inertia_kgm2=(1e-4, 0.0, 0.0, 2e-4, 0.0, 3e-4),
        )
        assert text.startswith("#usda 1.0")
        assert 'defaultPrim = "widget"' in text
        assert 'def Xform "widget"' in text
        assert "PhysicsRigidBodyAPI" in text
        assert "PhysicsCollisionAPI" in text
        assert "PhysicsMassAPI" in text
        assert "float physics:mass = 2.5" in text
        assert 'def Mesh "geometry"' in text
        assert "point3f[] points" in text

    def test_raises_for_non_axis_aligned_inertia(self):
        with pytest.raises(NonAxisAlignedInertiaError):
            build_usda(
                prim_name="widget",
                points=[(0.0, 0.0, 0.0)],
                face_vertex_indices=[0],
                face_vertex_counts=[1],
                mass_kg=1.0,
                com_m=(0.0, 0.0, 0.0),
                inertia_kgm2=(1.0, 0.5, 0.0, 2.0, 0.0, 3.0),
            )


def _quat_rotate(q: tuple[float, float, float, float], v: tuple[float, float, float]):
    """Rotate vector v by quaternion q=(w,x,y,z) -- used only to verify
    _quat_align_x_to's output actually aligns (1,0,0) with the target."""
    w, x, y, z = q
    vx, vy, vz = v
    # v' = q * v * q^-1, expanded (q is unit-length)
    uvx = y * vz - z * vy
    uvy = z * vx - x * vz
    uvz = x * vy - y * vx
    uuvx = y * uvz - z * uvy
    uuvy = z * uvx - x * uvz
    uuvz = x * uvy - y * uvx
    return (
        vx + 2 * (w * uvx + uuvx),
        vy + 2 * (w * uvy + uuvy),
        vz + 2 * (w * uvz + uuvz),
    )


class TestQuatAlignXTo:
    """MET-706 session (tier-2a): the +X->axis alignment quaternion, since
    UsdPhysics's physics:axis is a canonical token, not a free vector."""

    def test_identity_when_already_x(self):
        assert _quat_align_x_to((1.0, 0.0, 0.0)) == (1.0, 0.0, 0.0, 0.0)

    def test_180_when_negative_x(self):
        q = _quat_align_x_to((-1.0, 0.0, 0.0))
        rotated = _quat_rotate(q, (1.0, 0.0, 0.0))
        assert rotated == pytest.approx((-1.0, 0.0, 0.0), abs=1e-9)

    def test_aligns_to_arbitrary_axis(self):
        target = (0.0, 1.0, 0.0)
        q = _quat_align_x_to(target)
        rotated = _quat_rotate(q, (1.0, 0.0, 0.0))
        assert rotated == pytest.approx(target, abs=1e-9)

    def test_handles_unnormalized_input(self):
        target = (0.0, 0.0, 5.0)
        q = _quat_align_x_to(target)
        rotated = _quat_rotate(q, (1.0, 0.0, 0.0))
        assert rotated == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

    def test_quaternion_is_unit_length(self):
        q = _quat_align_x_to((1.0, 2.0, 3.0))
        mag = math.sqrt(sum(c * c for c in q))
        assert mag == pytest.approx(1.0)

    def test_zero_vector_raises(self):
        with pytest.raises(ValueError, match="non-zero"):
            _quat_align_x_to((0.0, 0.0, 0.0))


class TestBuildUsdaAssembly:
    """MET-706 session (tier-2a): multi-body USD with real UsdPhysics
    joints."""

    _LINKS = [
        {
            "name": "base",
            "points": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            "face_vertex_indices": [0, 1, 2],
            "face_vertex_counts": [3],
            "mass_kg": 1.0,
            "com_m": (0.0, 0.0, 0.0),
            "inertia_kgm2": (1.0, 0.0, 0.0, 1.0, 0.0, 1.0),
        },
        {
            "name": "arm",
            "points": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            "face_vertex_indices": [0, 1, 2],
            "face_vertex_counts": [3],
            "mass_kg": 0.5,
            "com_m": (0.1, 0.0, 0.0),
            "inertia_kgm2": (0.1, 0.0, 0.0, 0.1, 0.0, 0.1),
        },
    ]

    def test_fixed_joint(self):
        joints = [{"name": "j1", "type": "fixed", "base": "base", "follower": "arm"}]
        text = build_usda_assembly(robot_name="bot", links=self._LINKS, joints=joints)
        assert 'def Xform "bot"' in text
        assert 'def Xform "base"' in text
        assert 'def Xform "arm"' in text
        assert 'def PhysicsFixedJoint "j1"' in text
        assert "rel physics:body0 = </bot/base>" in text
        assert "rel physics:body1 = </bot/arm>" in text

    def test_revolute_maps_with_no_fabricated_limit(self):
        joints = [
            {
                "name": "j1",
                "type": "revolute",
                "base": "base",
                "follower": "arm",
                "axis": (0, 0, 1),
            },
        ]
        text = build_usda_assembly(robot_name="bot", links=self._LINKS, joints=joints)
        assert 'def PhysicsRevoluteJoint "j1"' in text
        assert 'uniform token physics:axis = "X"' in text
        assert "physics:lowerLimit" not in text
        assert "physics:upperLimit" not in text

    def test_ball_joint_is_supported_natively(self):
        joints = [{"name": "j1", "type": "ball", "base": "base", "follower": "arm"}]
        text = build_usda_assembly(robot_name="bot", links=self._LINKS, joints=joints)
        assert 'def PhysicsSphericalJoint "j1"' in text

    def test_slider_requires_limits(self):
        joints = [
            {"name": "j1", "type": "slider", "base": "base", "follower": "arm", "axis": (1, 0, 0)},
        ]
        with pytest.raises(MissingJointLimitsError):
            build_usda_assembly(robot_name="bot", links=self._LINKS, joints=joints)

    def test_slider_with_limits(self):
        joints = [
            {
                "name": "j1",
                "type": "slider",
                "base": "base",
                "follower": "arm",
                "axis": (1, 0, 0),
                "limits": {"lower": -0.05, "upper": 0.05},
            },
        ]
        text = build_usda_assembly(robot_name="bot", links=self._LINKS, joints=joints)
        assert "float physics:lowerLimit = -0.05" in text
        assert "float physics:upperLimit = 0.05" in text

    def test_cylindrical_raises(self):
        joints = [{"name": "j1", "type": "cylindrical", "base": "base", "follower": "arm"}]
        with pytest.raises(UnsupportedJointTypeError, match="no matching UsdPhysics"):
            build_usda_assembly(robot_name="bot", links=self._LINKS, joints=joints)

    def test_non_axis_aligned_link_inertia_still_raises(self):
        bad_links = [
            {**self._LINKS[0], "inertia_kgm2": (1.0, 0.5, 0.0, 2.0, 0.0, 3.0)},
            self._LINKS[1],
        ]
        with pytest.raises(NonAxisAlignedInertiaError):
            build_usda_assembly(robot_name="bot", links=bad_links, joints=[])
