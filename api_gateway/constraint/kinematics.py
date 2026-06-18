"""Analytic single-DOF joint kinematics for live solving (MET-530).

The Tier-3 live solve (MET-521) streamed a fixed-fraction follower stub. This
module replaces that with **real joint-aware kinematics** for the 10 Hz drag
loop: given an assembly joint and the user's free-space drag, it computes the
*constrained* follower motion the joint actually permits, plus the allowed-DOF
hint the gizmo uses to clamp the handle (Tier-2, MET-520).

Why analytic (not a full constraint solve) for the live loop: single-DOF joints
(revolute/slider/cylindrical) have closed-form motion, so each tick is a handful
of float ops — fast, deterministic, and unit-testable with no FreeCAD. FreeCAD's
authoritative full solve + collision check runs on *Apply/commit*, not per tick
(the hybrid model). This module is pure math; it imports nothing from FreeCAD.

Conventions: all vectors are ``(x, y, z)`` tuples in world mm. ``axis`` is the
joint axis (normalised here), ``anchor`` the pivot point for rotation joints.
The drag is the follower's grab point being pulled by ``delta``; we project that
onto the joint's DOF to get the realised motion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

Vec = tuple[float, float, float]

JointType = Literal["fixed", "revolute", "slider", "cylindrical", "ball"]
_JOINT_TYPES: frozenset[str] = frozenset({"fixed", "revolute", "slider", "cylindrical", "ball"})


# ---- tiny 3-vector helpers (no numpy in the hot path) ---------------------


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vec) -> Vec:
    n = _norm(a)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _rotate_about_axis(point: Vec, axis: Vec, anchor: Vec, angle: float) -> Vec:
    """Rotate ``point`` by ``angle`` (radians) about the line ``anchor`` + t·axis
    (Rodrigues' rotation formula)."""
    u = _normalize(axis)
    p = _sub(point, anchor)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    # p_rot = p·cos + (u×p)·sin + u·(u·p)·(1-cos)
    term1 = _scale(p, cos_a)
    term2 = _scale(_cross(u, p), sin_a)
    term3 = _scale(u, _dot(u, p) * (1.0 - cos_a))
    return _add(anchor, _add(_add(term1, term2), term3))


# ---- joint model ----------------------------------------------------------


@dataclass
class Joint:
    """An assembly joint constraining ``follower`` relative to ``base``.

    Authored by the FreeCAD assembly tools (follow-up slice) or supplied over the
    solve-stream init. Single-DOF types have closed-form live kinematics.
    """

    name: str
    type: JointType
    base: str
    follower: str
    axis: Vec = (0.0, 0.0, 1.0)
    anchor: Vec = (0.0, 0.0, 0.0)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Joint:
        jtype = str(d.get("type", "")).lower()
        if jtype not in _JOINT_TYPES:
            raise ValueError(
                f"unknown joint type: {jtype!r} (expected one of {sorted(_JOINT_TYPES)})"
            )
        axis = d.get("axis") or [0.0, 0.0, 1.0]
        anchor = d.get("anchor") or [0.0, 0.0, 0.0]
        return cls(
            name=str(d.get("name", f"{d.get('base', '?')}-{d.get('follower', '?')}")),
            type=jtype,  # type: ignore[arg-type]
            base=str(d["base"]),
            follower=str(d["follower"]),
            axis=(float(axis[0]), float(axis[1]), float(axis[2])),
            anchor=(float(anchor[0]), float(anchor[1]), float(anchor[2])),
        )


@dataclass
class DofHint:
    """Allowed degrees of freedom for the follower — drives gizmo clamping (MET-520)."""

    translation_axes: list[Vec] = field(default_factory=list)
    rotation_axes: list[Vec] = field(default_factory=list)
    free_rotation: bool = False  # ball joint: rotation about any axis through anchor

    def to_dict(self) -> dict[str, Any]:
        return {
            "translation_axes": [list(a) for a in self.translation_axes],
            "rotation_axes": [list(a) for a in self.rotation_axes],
            "free_rotation": self.free_rotation,
        }


@dataclass
class JointSolution:
    """Realised follower motion for one drag tick."""

    follower: str
    delta: Vec  # net translation of the follower's grab/reference point (mm)
    rotation: dict[str, Any] | None = None  # {axis, angle_deg, anchor} when rotational
    dof: DofHint = field(default_factory=DofHint)

    def transform_dict(self) -> dict[str, Any]:
        t: dict[str, Any] = {
            "group_name": self.follower,
            "delta": [round(c, 4) for c in self.delta],
        }
        if self.rotation is not None:
            t["rotation"] = self.rotation
        return t


def allowed_dof(joint: Joint) -> DofHint:
    """Degrees of freedom the joint permits for the follower."""
    axis = _normalize(joint.axis)
    if joint.type == "fixed":
        return DofHint()
    if joint.type == "slider":
        return DofHint(translation_axes=[axis])
    if joint.type == "revolute":
        return DofHint(rotation_axes=[axis])
    if joint.type == "cylindrical":
        return DofHint(translation_axes=[axis], rotation_axes=[axis])
    if joint.type == "ball":
        return DofHint(free_rotation=True)
    return DofHint()


def solve_joint(joint: Joint, drag: Vec, grab_point: Vec | None = None) -> JointSolution:
    """Project a free-space ``drag`` of the follower onto the joint's DOF.

    ``grab_point`` (where the user grabbed the follower) is required to map a drag
    into a rotation for revolute/cylindrical/ball joints; without it those joints
    yield no rotation (translation-only DOF still apply).
    """
    axis = _normalize(joint.axis)
    dof = allowed_dof(joint)

    if joint.type == "fixed":
        return JointSolution(follower=joint.follower, delta=(0.0, 0.0, 0.0), dof=dof)

    if joint.type == "slider":
        # Realised motion = drag projected onto the slide axis.
        slide = _scale(axis, _dot(drag, axis))
        return JointSolution(follower=joint.follower, delta=slide, dof=dof)

    if joint.type in ("revolute", "cylindrical"):
        translation: Vec = (0.0, 0.0, 0.0)
        rotation: dict[str, Any] | None = None
        net_delta: Vec = (0.0, 0.0, 0.0)
        if joint.type == "cylindrical":
            translation = _scale(axis, _dot(drag, axis))
            net_delta = translation
        if grab_point is not None:
            angle = _rotation_angle_from_drag(axis, joint.anchor, grab_point, drag)
            if abs(angle) > 1e-9:
                rotated = _rotate_about_axis(grab_point, axis, joint.anchor, angle)
                rot_delta = _sub(rotated, grab_point)
                net_delta = _add(net_delta, rot_delta)
                rotation = {
                    "axis": [round(c, 6) for c in axis],
                    "angle_deg": round(math.degrees(angle), 4),
                    "anchor": [round(c, 4) for c in joint.anchor],
                }
        return JointSolution(follower=joint.follower, delta=net_delta, rotation=rotation, dof=dof)

    if joint.type == "ball":
        if grab_point is None:
            return JointSolution(follower=joint.follower, delta=(0.0, 0.0, 0.0), dof=dof)
        # Rotate about the axis ⟂ to both the lever arm and the drag, by the
        # tangential drag over the lever length.
        arm = _sub(grab_point, joint.anchor)
        r = _norm(arm)
        if r < 1e-9:
            return JointSolution(follower=joint.follower, delta=(0.0, 0.0, 0.0), dof=dof)
        rot_axis = _cross(arm, drag)
        if _norm(rot_axis) < 1e-12:
            return JointSolution(follower=joint.follower, delta=(0.0, 0.0, 0.0), dof=dof)
        rot_axis = _normalize(rot_axis)
        tangential = _sub(drag, _scale(_normalize(arm), _dot(drag, _normalize(arm))))
        angle = _norm(tangential) / r
        rotated = _rotate_about_axis(grab_point, rot_axis, joint.anchor, angle)
        return JointSolution(
            follower=joint.follower,
            delta=_sub(rotated, grab_point),
            rotation={
                "axis": [round(c, 6) for c in rot_axis],
                "angle_deg": round(math.degrees(angle), 4),
                "anchor": [round(c, 4) for c in joint.anchor],
            },
            dof=dof,
        )

    return JointSolution(follower=joint.follower, delta=(0.0, 0.0, 0.0), dof=dof)


def _rotation_angle_from_drag(axis: Vec, anchor: Vec, grab_point: Vec, drag: Vec) -> float:
    """Angle (radians) a revolute drag induces: tangential drag / lever radius.

    The lever arm is grab_point→anchor with the on-axis component removed (the
    radius in the rotation plane). The tangent direction is ``axis × arm_perp``;
    the signed tangential drag over the radius gives the rotation angle.
    """
    arm = _sub(grab_point, anchor)
    arm_perp = _sub(arm, _scale(axis, _dot(arm, axis)))  # component ⟂ to axis
    radius = _norm(arm_perp)
    if radius < 1e-9:
        return 0.0
    tangent = _normalize(_cross(axis, arm_perp))
    return _dot(drag, tangent) / radius
