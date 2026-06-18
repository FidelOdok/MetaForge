"""Unit tests for analytic single-DOF joint kinematics (MET-530).

Pure math — no FreeCAD. Verifies DOF hints + constrained follower motion for
each joint type the live solver supports.
"""

from __future__ import annotations

import math

import pytest

from api_gateway.constraint.kinematics import (
    Joint,
    JointSolution,
    allowed_dof,
    solve_joint,
)


def _j(jtype: str, **kw: object) -> Joint:
    return Joint.from_dict({"base": "B", "follower": "F", "type": jtype, **kw})


class TestJointModel:
    def test_from_dict_normalises_and_defaults(self) -> None:
        j = Joint.from_dict({"type": "Revolute", "base": "a", "follower": "b"})
        assert j.type == "revolute"
        assert j.axis == (0.0, 0.0, 1.0)
        assert j.anchor == (0.0, 0.0, 0.0)
        assert j.name == "a-b"

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown joint type"):
            Joint.from_dict({"type": "weld", "base": "a", "follower": "b"})


class TestDofHints:
    def test_fixed_has_no_dof(self) -> None:
        d = allowed_dof(_j("fixed"))
        assert d.translation_axes == [] and d.rotation_axes == [] and not d.free_rotation

    def test_slider_translates_along_axis(self) -> None:
        d = allowed_dof(_j("slider", axis=[1, 0, 0]))
        assert d.translation_axes == [(1.0, 0.0, 0.0)] and d.rotation_axes == []

    def test_revolute_rotates_about_axis(self) -> None:
        d = allowed_dof(_j("revolute", axis=[0, 0, 1]))
        assert d.rotation_axes == [(0.0, 0.0, 1.0)] and d.translation_axes == []

    def test_cylindrical_has_both(self) -> None:
        d = allowed_dof(_j("cylindrical", axis=[0, 0, 1]))
        assert d.translation_axes == [(0.0, 0.0, 1.0)]
        assert d.rotation_axes == [(0.0, 0.0, 1.0)]

    def test_ball_is_free_rotation(self) -> None:
        d = allowed_dof(_j("ball"))
        assert d.free_rotation is True


class TestSolve:
    def test_fixed_never_moves(self) -> None:
        sol = solve_joint(_j("fixed"), (5.0, 5.0, 5.0), grab_point=(1, 2, 3))
        assert sol.delta == (0.0, 0.0, 0.0)
        assert sol.rotation is None

    def test_slider_projects_drag_onto_axis(self) -> None:
        # Axis X; an off-axis drag is clamped to its X component.
        sol = solve_joint(_j("slider", axis=[1, 0, 0]), (5.0, 3.0, -2.0))
        assert sol.delta == pytest.approx((5.0, 0.0, 0.0))

    def test_slider_diagonal_axis(self) -> None:
        sol = solve_joint(_j("slider", axis=[1, 1, 0]), (2.0, 0.0, 0.0))
        # projection of (2,0,0) onto normalized (1,1,0)/√2 → (1,1,0)
        assert sol.delta == pytest.approx((1.0, 1.0, 0.0))

    def test_revolute_maps_tangential_drag_to_rotation(self) -> None:
        # Z axis through origin, grab at (10,0,0); a +Y drag is tangential.
        j = _j("revolute", axis=[0, 0, 1], anchor=[0, 0, 0])
        sol = solve_joint(j, (0.0, 1.0, 0.0), grab_point=(10.0, 0.0, 0.0))
        assert sol.rotation is not None
        # angle = tangential(1) / radius(10) = 0.1 rad
        assert sol.rotation["angle_deg"] == pytest.approx(math.degrees(0.1), abs=1e-3)
        # follower point swings ~+Y, slightly -X
        assert sol.delta[1] == pytest.approx(math.sin(0.1) * 10.0, abs=1e-3)
        assert sol.delta[0] == pytest.approx(math.cos(0.1) * 10.0 - 10.0, abs=1e-3)
        assert sol.delta[2] == pytest.approx(0.0, abs=1e-9)

    def test_revolute_radial_drag_produces_no_rotation(self) -> None:
        # A drag straight toward the axis (radial) has no tangential component.
        j = _j("revolute", axis=[0, 0, 1], anchor=[0, 0, 0])
        sol = solve_joint(j, (-1.0, 0.0, 0.0), grab_point=(10.0, 0.0, 0.0))
        assert sol.rotation is None
        assert sol.delta == pytest.approx((0.0, 0.0, 0.0))

    def test_revolute_without_grab_point_locks(self) -> None:
        j = _j("revolute", axis=[0, 0, 1])
        sol = solve_joint(j, (0.0, 1.0, 0.0))
        assert sol.rotation is None
        assert sol.delta == (0.0, 0.0, 0.0)

    def test_cylindrical_combines_slide_and_turn(self) -> None:
        # Drag has an axial (+Z) part → slide, and a tangential (+Y) part → turn.
        j = _j("cylindrical", axis=[0, 0, 1], anchor=[0, 0, 0])
        sol = solve_joint(j, (0.0, 1.0, 2.0), grab_point=(10.0, 0.0, 0.0))
        assert sol.rotation is not None
        # Z component is the pure slide (rotation about Z doesn't move Z of the point).
        assert sol.delta[2] == pytest.approx(2.0, abs=1e-6)

    def test_ball_rotates_about_arm_cross_drag(self) -> None:
        j = _j("ball", anchor=[0, 0, 0])
        sol = solve_joint(j, (0.0, 1.0, 0.0), grab_point=(10.0, 0.0, 0.0))
        assert sol.rotation is not None
        # arm=X, drag=Y → rotation axis Z
        assert sol.rotation["axis"] == pytest.approx([0.0, 0.0, 1.0])
        assert sol.delta[1] == pytest.approx(math.sin(0.1) * 10.0, abs=1e-3)

    def test_solution_transform_dict_shape(self) -> None:
        sol = JointSolution(follower="F", delta=(1.0, 2.0, 3.0))
        d = sol.transform_dict()
        assert d == {"group_name": "F", "delta": [1.0, 2.0, 3.0]}
        assert "rotation" not in d
