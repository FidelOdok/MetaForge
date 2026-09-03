"""Unit tests for USD (.usda) export -- STL parsing, axis-aligned inertia
check, and .usda text generation (see usd_export.py's module docstring for
why this hand-authors USD text rather than depending on usd-core/pxr)."""

from __future__ import annotations

import struct

import pytest

from tool_registry.tools.cadquery.usd_export import (
    NonAxisAlignedInertiaError,
    _check_axis_aligned,
    _looks_like_binary_stl,
    build_usda,
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
