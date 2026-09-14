"""Unit tests for GLB -> OpenUSD conversion (MET-634).

Validation-only tests run unconditionally. Tests that exercise real
trimesh/pxr conversion use ``pytest.importorskip`` (same convention as
``tests/unit/test_cadquery_operations.py``) since the 'omniverse-usd' extra
isn't part of the default ``.[dev]`` CI install.
"""

from __future__ import annotations

import pytest

from tool_registry.tools.omniverse_usd.converter import (
    UsdConversionError,
    _safe_prim_name,
)

# ---------------------------------------------------------------------------
# 1. Prim name sanitization (pure logic, no deps)
# ---------------------------------------------------------------------------


class TestSafePrimName:
    def test_alnum_passthrough(self) -> None:
        assert _safe_prim_name("bracket_body") == "bracket_body"

    def test_special_chars_replaced(self) -> None:
        assert _safe_prim_name("bracket-mount.v2") == "bracket_mount_v2"

    def test_leading_digit_prefixed(self) -> None:
        assert _safe_prim_name("2nd_part") == "_2nd_part"

    def test_empty_string(self) -> None:
        assert _safe_prim_name("") == "_"


# ---------------------------------------------------------------------------
# 2. convert_glb_to_usd validation (no trimesh/pxr needed -- fails before that)
# ---------------------------------------------------------------------------


class TestConvertGlbToUsdValidation:
    def test_missing_glb_file(self) -> None:
        from tool_registry.tools.omniverse_usd.converter import convert_glb_to_usd

        with pytest.raises(FileNotFoundError, match="GLB file not found"):
            convert_glb_to_usd("/nonexistent/model.glb", "/tmp/out.usda")

    def test_bad_output_extension(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from tool_registry.tools.omniverse_usd.converter import convert_glb_to_usd

        glb_file = tmp_path / "model.glb"
        glb_file.write_bytes(b"dummy")
        with pytest.raises(ValueError, match="output_path must be one of"):
            convert_glb_to_usd(str(glb_file), "/tmp/out.step")

    def test_bad_up_axis(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from tool_registry.tools.omniverse_usd.converter import convert_glb_to_usd

        glb_file = tmp_path / "model.glb"
        glb_file.write_bytes(b"dummy")
        with pytest.raises(ValueError, match="up_axis must be"):
            convert_glb_to_usd(str(glb_file), "/tmp/out.usda", up_axis="X")


class TestValidateUsdMinimumValidation:
    def test_missing_usd_file(self) -> None:
        from tool_registry.tools.omniverse_usd.converter import validate_usd_minimum

        with pytest.raises(FileNotFoundError, match="USD file not found"):
            validate_usd_minimum("/nonexistent/stage.usda")


class TestDescribeStageValidation:
    def test_missing_usd_file(self) -> None:
        from tool_registry.tools.omniverse_usd.converter import describe_stage

        with pytest.raises(FileNotFoundError, match="USD file not found"):
            describe_stage("/nonexistent/stage.usda")


class TestUsdConversionError:
    def test_is_exception(self) -> None:
        err = UsdConversionError("bad glb")
        assert isinstance(err, Exception)
        assert "bad glb" in str(err)


# ---------------------------------------------------------------------------
# 3. Real conversion round-trip (needs trimesh + pxr)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_glb(tmp_path):  # type: ignore[no-untyped-def]
    """A two-part GLB scene with named nodes and a non-identity transform,
    mirroring what occt-converter/cadquery actually produce for an assembly.
    """
    trimesh = pytest.importorskip("trimesh")
    import numpy as np

    box = trimesh.creation.box(extents=[10, 20, 5])
    box.visual.face_colors = [200, 50, 50, 255]
    scene = trimesh.Scene()
    scene.add_geometry(box, node_name="bracket_body")

    cyl = trimesh.creation.cylinder(radius=2, height=15)
    transform = np.eye(4)
    transform[0, 3] = 30.0
    scene.add_geometry(cyl, node_name="bracket_mount", transform=transform)

    glb_path = tmp_path / "assembly.glb"
    glb_path.write_bytes(scene.export(file_type="glb"))
    return str(glb_path)


class TestConvertGlbToUsdRealConversion:
    def test_part_names_preserved(self, sample_glb, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The naive trimesh.Scene.geometry dict keys are anonymous
        (geometry_0, geometry_1) after a GLB round-trip -- part names must
        come from scene.graph.nodes_geometry instead. Verified 2026-08-25.
        """
        pytest.importorskip("pxr")
        from tool_registry.tools.omniverse_usd.converter import convert_glb_to_usd

        output_path = str(tmp_path / "assembly.usda")
        result = convert_glb_to_usd(sample_glb, output_path)

        assert result["mesh_count"] == 2
        assert set(result["part_names"]) == {"bracket_body", "bracket_mount"}
        assert "geometry_0" not in result["part_names"]

    def test_transform_round_trips_with_correct_orientation(  # type: ignore[no-untyped-def]
        self, sample_glb, tmp_path
    ) -> None:
        """USD's Gf.Matrix4d is row-vector convention; trimesh/numpy is
        column-vector. Without transposing, every part collapses to the
        stage origin. Verified 2026-08-25.
        """
        pxr = pytest.importorskip("pxr")
        from tool_registry.tools.omniverse_usd.converter import convert_glb_to_usd

        output_path = str(tmp_path / "assembly.usda")
        convert_glb_to_usd(sample_glb, output_path)

        stage = pxr.Usd.Stage.Open(output_path)
        mount = pxr.UsdGeom.Xformable(stage.GetPrimAtPath("/Root/bracket_mount"))
        translation = mount.ComputeLocalToWorldTransform(
            pxr.Usd.TimeCode.Default()
        ).ExtractTranslation()
        assert abs(translation[0] - 30.0) < 1e-5

    def test_stage_metadata(self, sample_glb, tmp_path) -> None:  # type: ignore[no-untyped-def]
        pxr = pytest.importorskip("pxr")
        from tool_registry.tools.omniverse_usd.converter import convert_glb_to_usd

        output_path = str(tmp_path / "assembly.usda")
        convert_glb_to_usd(sample_glb, output_path, meters_per_unit=0.01, up_axis="Y")

        stage = pxr.Usd.Stage.Open(output_path)
        assert stage.GetMetadata("metersPerUnit") == 0.01
        assert pxr.UsdGeom.GetStageUpAxis(stage) == "Y"

    def test_empty_scene_raises(self, sample_glb, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """trimesh itself refuses to export a scene with zero geometries
        (``ValueError: Can't export empty scenes!``), so a genuinely empty
        GLB can't come from any real producer (occt-converter/cadquery skip
        empty shapes the same way) -- exercise the guard by mocking the
        loaded scene directly instead of via a real empty-GLB file.
        """
        pytest.importorskip("trimesh")
        pytest.importorskip("pxr")
        from unittest.mock import MagicMock, patch

        from tool_registry.tools.omniverse_usd.converter import (
            UsdConversionError,
            convert_glb_to_usd,
        )

        empty_scene = MagicMock()
        empty_scene.graph.nodes_geometry = []

        with patch("trimesh.load", return_value=empty_scene):
            with pytest.raises(UsdConversionError, match="No geometry found"):
                convert_glb_to_usd(sample_glb, str(tmp_path / "out.usda"))


class TestValidateUsdMinimumRealStage:
    def test_valid_stage_passes(self, sample_glb, tmp_path) -> None:  # type: ignore[no-untyped-def]
        pytest.importorskip("pxr")
        from tool_registry.tools.omniverse_usd.converter import (
            convert_glb_to_usd,
            validate_usd_minimum,
        )

        output_path = str(tmp_path / "assembly.usda")
        convert_glb_to_usd(sample_glb, output_path)

        result = validate_usd_minimum(output_path)
        assert result["valid"] is True
        assert result["mesh_count"] == 2
        assert result["has_default_prim"] is True
        assert result["issues"] == []


class TestDescribeStageRealStage:
    def test_describe_matches_conversion(self, sample_glb, tmp_path) -> None:  # type: ignore[no-untyped-def]
        pytest.importorskip("pxr")
        from tool_registry.tools.omniverse_usd.converter import (
            convert_glb_to_usd,
            describe_stage,
        )

        output_path = str(tmp_path / "assembly.usda")
        convert_glb_to_usd(sample_glb, output_path)

        desc = describe_stage(output_path)
        assert desc["mesh_count"] == 2
        assert "/Root/bracket_body" in desc["prim_paths"]
        assert "/Root/bracket_mount" in desc["prim_paths"]
