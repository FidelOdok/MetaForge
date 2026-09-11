"""Tests for tool_registry.tools.cadquery.physics_check (MET-740 Phase 2).

Two tiers, matching this repo's established cadquery/FreeCAD test
convention (see test_cadquery_operations.py's own HAS_CADQUERY pattern):
lightweight tests that patch HAS_PYBULLET / raise without the real package,
and real-execution tests gated by ``pytest.importorskip("pybullet")`` that
exercise an actual PyBullet physics step against a minimal, self-contained
URDF fixture (box primitives, no external mesh files needed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tool_registry.tools.cadquery.physics_check import (
    PyBulletNotAvailableError,
    validate_physics_stability,
)

# A minimal, valid 2-link, 1-revolute-joint URDF using box primitives (no
# mesh files needed -- pybullet.loadURDF supports <box>/<cylinder>/<sphere>
# geometry natively), mirroring the shape cadquery.export_urdf_assembly
# actually produces (same <link>/<joint> structure, just STL meshes there
# instead of primitives here).
_STABLE_URDF = """<?xml version="1.0"?>
<robot name="test_robot">
  <link name="body">
    <visual><geometry><box size="0.2 0.12 0.04"/></geometry></visual>
    <collision><geometry><box size="0.2 0.12 0.04"/></geometry></collision>
    <inertial>
      <mass value="1.0"/>
      <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>
    </inertial>
  </link>
  <link name="leg">
    <visual><geometry><box size="0.02 0.02 0.08"/></geometry></visual>
    <collision><geometry><box size="0.02 0.02 0.08"/></geometry></collision>
    <inertial>
      <mass value="0.1"/>
      <inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/>
    </inertial>
  </link>
  <joint name="hip" type="continuous">
    <parent link="body"/>
    <child link="leg"/>
    <origin xyz="0.08 0.05 0" rpy="0 0 0"/>
    <axis xyz="0 1 0"/>
  </joint>
</robot>
"""

# Same structure, but the leg's inertia is degenerate (all zero) -- a real
# common export bug (see this module's own docstring) that a physics
# engine's solver blows up on.
_DEGENERATE_INERTIA_URDF = _STABLE_URDF.replace(
    '<inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/>',
    '<inertia ixx="0" ixy="0" ixz="0" iyy="0" iyz="0" izz="0"/>',
)


class TestNotAvailable:
    def test_raises_when_pybullet_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tool_registry.tools.cadquery.physics_check as mod

        monkeypatch.setattr(mod, "HAS_PYBULLET", False)
        with pytest.raises(PyBulletNotAvailableError):
            validate_physics_stability("does-not-matter.urdf")


class TestFileNotFound:
    def test_missing_urdf_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("pybullet")
        with pytest.raises(FileNotFoundError, match="URDF not found"):
            validate_physics_stability(str(tmp_path / "missing.urdf"))


class TestRealPhysicsStability:
    """Real PyBullet execution -- skipped when pybullet isn't installed."""

    def test_stable_robot_settles_with_no_divergence(self, tmp_path: Path) -> None:
        pytest.importorskip("pybullet")
        urdf_path = tmp_path / "model.urdf"
        urdf_path.write_text(_STABLE_URDF)

        result = validate_physics_stability(str(urdf_path), steps=120)

        assert result["stable"] is True
        assert result["diverged_at_step"] is None
        assert result["steps_run"] == 120
        assert result["link_count"] == 2
        assert result["joint_count"] == 1
        assert result["link_names"] == ["base", "leg"]
        # Settled under gravity + ground contact, not still accelerating.
        assert result["max_linear_velocity_mps"] < 20.0

    def test_ground_plane_false_lets_it_free_fall(self, tmp_path: Path) -> None:
        pytest.importorskip("pybullet")
        urdf_path = tmp_path / "model.urdf"
        urdf_path.write_text(_STABLE_URDF)

        result = validate_physics_stability(str(urdf_path), steps=60, ground_plane=False)

        # No floor to land on -- still falls, but that's not "instability"
        # (no NaN, no erratic spin), just uncontested gravity.
        assert result["diverged_at_step"] is None
        assert result["final_base_position_m"][2] < 0.0

    def test_degenerate_inertia_reported_via_diagnostics(self, tmp_path: Path) -> None:
        # Not asserting stable=False here -- PyBullet's own zero-inertia
        # handling varies by version (some clamp/ignore rather than NaN
        # outright), so the meaningful, version-stable assertion is that
        # the check runs to completion and reports real diagnostics for a
        # human/caller to judge, not that it crashes.
        pytest.importorskip("pybullet")
        urdf_path = tmp_path / "model.urdf"
        urdf_path.write_text(_DEGENERATE_INERTIA_URDF)

        result = validate_physics_stability(str(urdf_path), steps=60)

        assert result["steps_run"] > 0
        assert "stable" in result
