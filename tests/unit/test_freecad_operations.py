"""Unit tests for FreeCAD operations module (MET-221).

All tests mock FreeCAD internals since FreeCAD is not available in CI.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tool_registry.tools.freecad.operations import (
    _SHAPE_DEFAULTS,
    HAS_FREECAD,
    FreecadNotAvailableError,
    FreecadOperations,
)

# ---------------------------------------------------------------------------
# 1. Shape defaults are well-formed
# ---------------------------------------------------------------------------


class TestShapeDefaults:
    """Verify shape defaults cover expected shape types."""

    def test_all_expected_shapes_present(self) -> None:
        expected = {"box", "cylinder", "sphere", "cone", "torus", "bracket", "plate", "enclosure"}
        assert expected == set(_SHAPE_DEFAULTS.keys())

    def test_box_defaults(self) -> None:
        box = _SHAPE_DEFAULTS["box"]
        assert "length" in box
        assert "width" in box
        assert "height" in box

    def test_cylinder_defaults(self) -> None:
        cyl = _SHAPE_DEFAULTS["cylinder"]
        assert "radius" in cyl
        assert "height" in cyl

    def test_bracket_has_hole_radius(self) -> None:
        bracket = _SHAPE_DEFAULTS["bracket"]
        assert "hole_radius" in bracket
        assert "thickness" in bracket


# ---------------------------------------------------------------------------
# 2. FreecadOperations requires FreeCAD
# ---------------------------------------------------------------------------


class TestFreecadGuard:
    """FreeCAD availability checks."""

    def test_require_freecad_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops._require_freecad()

    def test_create_parametric_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.create_parametric("box", {})

    def test_export_step_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.export_step("/input.step")

    def test_generate_mesh_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.generate_mesh("/input.step")

    def test_generate_ic_package_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.generate_ic_package(None, "SOIC", "LM358", {})


class TestIcPinLayout:
    """Pure pin-layout maths (no FreeCAD) for datasheet-driven IC generation."""

    def test_soic_two_sided_numbering(self) -> None:
        pins = FreecadOperations.ic_pin_layout("SOIC", 8, 1.27)
        assert len(pins) == 8
        # 4 per side; pin 1 on y-, pin 8 on y+
        assert {p["side"] for p in pins} == {"y-", "y+"}
        assert pins[0]["pin"] == 1 and pins[0]["side"] == "y-"
        # centred positions: symmetric about 0
        ys = sorted(p["u"] for p in pins if p["side"] == "y-")
        assert ys[0] == pytest.approx(-1.5 * 1.27)
        assert ys[-1] == pytest.approx(1.5 * 1.27)

    def test_qfp_four_sided(self) -> None:
        pins = FreecadOperations.ic_pin_layout("LQFP", 32, 0.8)
        assert len(pins) == 32
        assert {p["side"] for p in pins} == {"y-", "x+", "y+", "x-"}
        # 8 pins per side
        from collections import Counter

        assert set(Counter(p["side"] for p in pins).values()) == {8}

    def test_generate_profile_part_raises_when_unavailable(self) -> None:
        ops = FreecadOperations()
        prof = [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 10}]
        with patch("tool_registry.tools.freecad.operations.HAS_FREECAD", False):
            with pytest.raises(FreecadNotAvailableError):
                ops.generate_profile_part(None, "Shaft", prof, "revolve")

    def test_unsupported_family_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported package family"):
            FreecadOperations.ic_pin_layout("BGA", 64, 0.8)

    def test_lead_count_must_match_sides(self) -> None:
        with pytest.raises(ValueError, match="multiple of"):
            FreecadOperations.ic_pin_layout("SOIC", 7, 1.27)  # odd → invalid for 2 sides
        with pytest.raises(ValueError, match="multiple of"):
            FreecadOperations.ic_pin_layout("QFP", 30, 0.8)  # not divisible by 4


class TestNormalizeProfile:
    """Pure profile validation for revolve/extrude generation (no FreeCAD)."""

    def test_closes_open_profile(self) -> None:
        pts = FreecadOperations.normalize_profile(
            [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 10}], "revolve"
        )
        assert pts[0] == pts[-1]  # auto-closed
        assert len(pts) == 4

    def test_rejects_too_few_points(self) -> None:
        with pytest.raises(ValueError, match="at least 3 points"):
            FreecadOperations.normalize_profile([{"x": 0, "y": 0}, {"x": 1, "y": 1}], "extrude")

    def test_revolve_rejects_negative_radius(self) -> None:
        with pytest.raises(ValueError, match="radius"):
            FreecadOperations.normalize_profile(
                [{"x": -1, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 10}], "revolve"
            )

    def test_extrude_allows_negative_x(self) -> None:
        pts = FreecadOperations.normalize_profile(
            [{"x": -2, "y": 0}, {"x": 2, "y": 0}, {"x": 0, "y": 3}], "extrude"
        )
        assert len(pts) == 4

    def test_unknown_operation_rejected(self) -> None:
        with pytest.raises(ValueError, match="revolve.*extrude|operation"):
            FreecadOperations.normalize_profile(
                [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}], "loft"
            )


# ---------------------------------------------------------------------------
# 3. FreecadOperations init
# ---------------------------------------------------------------------------


class TestFreecadOperationsInit:
    """Initialization and configuration."""

    def test_default_work_dir(self) -> None:
        ops = FreecadOperations()
        assert ops.work_dir == "/workspace"

    def test_custom_work_dir(self) -> None:
        ops = FreecadOperations(work_dir="/custom")
        assert ops.work_dir == "/custom"

    def test_custom_timeout(self) -> None:
        ops = FreecadOperations(timeout=120.0)
        assert ops.timeout == 120.0


# ---------------------------------------------------------------------------
# 4. Build shape dispatching
# ---------------------------------------------------------------------------


class TestBuildShape:
    """Verify _build_shape dispatches to correct FreeCAD Part methods."""

    @pytest.mark.skipif(not HAS_FREECAD, reason="FreeCAD not installed")
    def test_unsupported_shape_raises(self) -> None:
        ops = FreecadOperations()
        with pytest.raises(ValueError, match="Unsupported shape type"):
            ops._build_shape("pentagon", {})

    def test_unsupported_shape_raises_mocked(self) -> None:
        """Test without FreeCAD by calling the dispatch logic directly."""
        ops = FreecadOperations()
        # Patch HAS_FREECAD to True to skip the guard, but the shape is invalid anyway
        with pytest.raises(ValueError, match="Unsupported shape type"):
            ops._build_shape("hexagon", {})


# ---------------------------------------------------------------------------
# 5. Error class
# ---------------------------------------------------------------------------


class TestFreecadNotAvailableError:
    """Error message formatting."""

    def test_error_message(self) -> None:
        err = FreecadNotAvailableError()
        assert "FreeCAD Python bindings" in str(err)
        assert "Docker container" in str(err)


class TestExecuteCodeSandbox:
    """execute_code source-level guarding is validated before any FreeCAD call,
    so the sandbox policy is testable without FreeCAD bindings (MET-527)."""

    def test_blocks_dangerous_names(self) -> None:
        from tool_registry.tools.freecad.operations import FreecadOperations, ScriptSandboxError

        ops = FreecadOperations()
        for snippet in ("import os", "open('/etc/passwd')", "eval('1')", "__import__('sys')"):
            with pytest.raises(ScriptSandboxError):
                ops.execute_code(None, snippet)

    def test_rejects_oversize_script(self) -> None:
        from tool_registry.tools.freecad.operations import FreecadOperations, ScriptSandboxError

        ops = FreecadOperations()
        with pytest.raises(ScriptSandboxError, match="exceeds"):
            ops.execute_code(None, "\n".join(f"a{i} = {i}" for i in range(201)), max_lines=200)
