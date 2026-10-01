"""Repeatability estimate derived from real joint kinematics and real
actuator encoder specs (FORGE-285, gap G-F9 -- the buildable half of a
ticket that also asks for controls validation).

**Why this ticket was split.** FORGE-285 bundles two genuinely different
things. "Tracking error, stability margins, controller tuning, Bode /
step-response plots" needs real transfer-function / closed-loop-controls
analysis and the Planner's own note marks the one library candidate for
that (python-control) as "not scheduled" -- an unscheduled dependency for
a whole new, uncommitted capability area, the same reasoning this session
applied to deferring FORGE-284's Gazebo/Isaac dynamics work. That half is
NOT built here.

"Repeatability estimate vs requirement (+/-0.5 mm)" is different: it is
serial-chain differential kinematics, not controls theory, and is
genuinely buildable from real data already in this codebase plus real,
cited manufacturer specs.

**What was missing and had to be built fresh.**
``api_gateway/constraint/kinematics.py`` only solves ONE joint's motion
given a drag on that joint (``solve_joint``, FORGE-250's single-joint
drag-to-pose). It has no multi-joint chain composition and no Jacobian.
This module adds both: a real forward-kinematics chain solve (reusing
``kinematics.py``'s vector helpers and Rodrigues-formula convention, just
promoted from "rotate one point" to "compose a 3x3 rotation matrix per
joint and chain them"), then a numerical (finite-difference) Jacobian --
perturb one joint's angle by a small real delta, re-solve the whole
chain, measure how far the end-effector moved. This is the same joint
data (``AssemblyJoint``: name/type/base/follower/axis/anchor/limits,
FORGE-271/245) that ``bringup_checklist.py``, ``firmware_scaffold.py``,
and ``harness_estimate.py`` already read, reusing their shared
``_topological_steps`` build-order helper (also the cyclic-graph error
check, for free).

**Only revolute joints rotate in this chain solve.** The real AR4 arm's
6 joints are all revolute, so this is not a limiting simplification for
the one project this is demonstrated on, but it is an honest, stated
scope boundary: a slider/cylindrical/ball joint is treated as rigid
(fixed) here, not modelled as a moving DOF. A general multi-type chain
solve is a natural but separate follow-up.

**Real encoder data: honestly incomplete, documented rather than
fabricated.** The real arm project's BOM has exactly 3 real actuator
part numbers: AK80-8 KV60 (CubeMars, joint_2), RL-SE-105-70 (igus,
joint_3), RL-SE-80-50 (igus, joint_4/5/6, qty 3) -- confirmed live
against the real deployed BOM. joint_1 has NO actuator recorded in the
BOM at all.

- AK80-8 KV60: the manufacturer's own product page states a 15-bit
  OUTPUT (outer-ring) magnetic encoder --
  https://www.cubemars.com/product/ak80-8-kv60-robotic-actuator.html
  (there is also a 14-bit inner-ring encoder, but that is pre-reduction,
  on the motor side; the output-side figure is what sets real joint
  position resolution). 2**15 = 32768 counts/rev.
- igus RL-SE-*: no per-joint encoder resolution is published anywhere
  for these strain-wave actuators -- checked the official product pages
  (igus.com/product/RL-SE-105-70-0120, the RL-SE-80-50 gearbox page),
  the ReBeL technical wiki (wiki.cpr-robots.com), and igus's own public
  ReBeL documentation PDFs. What IS consistently published, across every
  ReBeL cobot configuration igus sells, is a whole-arm repeatability
  figure: +/-1 mm (e.g. https://www.igus.com/product/22534). This module
  uses that real, cited figure as a conservative stand-in for "the
  combined contribution of the igus-actuated joints" rather than
  fabricating a per-joint decomposition that was never published --
  NOT decomposed and re-propagated per-joint through this module's own
  Jacobian, since igus's figure already reflects their actuators' real
  measured combined behaviour and re-decomposing it would double-count.
- joint_1 has no real actuator in the BOM at all, so it contributes
  nothing to this estimate -- a real, stated gap, not a zero assumed to
  mean "no error". The result's ``warnings`` list says so explicitly.

**Deliberately out of scope** (see Jira FORGE-285 for the full
reasoning): tracking error, stability margins, controller tuning,
Bode/step-response plots (needs the unscheduled python-control library);
a dashboard controller-tuning panel (no controls backend to drive it);
real connector/backlash/compliance error sources beyond encoder
resolution; non-revolute joint types in the chain solve.
"""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

import structlog

from api_gateway.constraint.kinematics import Vec, _add, _norm, _normalize, _scale, _sub
from api_gateway.twin.bringup_checklist import AssemblyGraphError, _topological_steps
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.repeatability")

__all__ = ["AssemblyGraphError", "make_repeatability_estimator"]

Mat3 = tuple[Vec, Vec, Vec]

_IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

# Central-difference step (rad) for the numerical Jacobian. Small enough
# to approximate the local derivative well for a smooth rotation, large
# enough to stay well clear of floating-point cancellation.
_FD_DELTA_RAD = 1.0e-6

# --- real, cited actuator data (see module docstring for sources) ---------

#: AK80-8 KV60 (CubeMars): 15-bit output (outer-ring) magnetic encoder.
#: https://www.cubemars.com/product/ak80-8-kv60-robotic-actuator.html
AK80_8_OUTPUT_ENCODER_BITS = 15

#: joint_2 is the AK80-8 KV60 per the real BOM's own description field
#: ("brushless_dc_actuator (J2_joint_actuator)").
AK80_8_JOINT_NAME = "joint_2"

#: igus ReBeL whole-arm repeatability, published consistently across every
#: ReBeL cobot configuration. https://www.igus.com/product/22534
#: Used as a conservative stand-in for the combined contribution of every
#: igus RL-SE-actuated joint (joint_3..joint_6) -- see module docstring.
IGUS_REBEL_WHOLE_ARM_REPEATABILITY_MM = 1.0

#: joints with no real actuator recorded in the BOM at all.
_UNMODELLED_JOINTS = ("joint_1",)


def _mat_vec(m: Mat3, v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _mat_mat(a: Mat3, b: Mat3) -> Mat3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _rodrigues_matrix(axis: Vec, angle: float) -> Mat3:
    """3x3 rotation matrix for a rotation by ``angle`` (rad) about ``axis``
    through the origin -- the matrix form of the same Rodrigues formula
    ``kinematics.py``'s ``_rotate_about_axis`` applies to a single point."""
    u = _normalize(axis)
    ux, uy, uz = u
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    k: Mat3 = ((0.0, -uz, uy), (uz, 0.0, -ux), (-uy, ux, 0.0))
    outer: Mat3 = (
        (ux * ux, ux * uy, ux * uz),
        (uy * ux, uy * uy, uy * uz),
        (uz * ux, uz * uy, uz * uz),
    )
    return tuple(  # type: ignore[return-value]
        tuple(
            cos_a * _IDENTITY[i][j] + sin_a * k[i][j] + (1.0 - cos_a) * outer[i][j]
            for j in range(3)
        )
        for i in range(3)
    )


def _forward_kinematics(
    joints: list[dict[str, Any]], angles: dict[str, float]
) -> dict[str, tuple[Mat3, Vec]]:
    """World ``(rotation, translation)`` frame for every part reachable
    from a real root, given a joint-angle dict (rad, keyed by joint name,
    defaulting to 0.0 -- the CAD-committed/design pose for any joint not
    given). Only ``revolute`` joints rotate; every other type is treated
    as a rigid (fixed) link for this honest first slice -- see module
    docstring."""
    ordered = _topological_steps(joints)
    frames: dict[str, tuple[Mat3, Vec]] = {}
    bases = {j["base"] for j in joints}
    followers = {j["follower"] for j in joints}
    for root in bases - followers:
        frames[root] = (_IDENTITY, (0.0, 0.0, 0.0))
    for j in ordered:
        parent_r, parent_t = frames[j["base"]]
        anchor_local: Vec = tuple(j.get("anchor") or (0.0, 0.0, 0.0))  # type: ignore[assignment]
        world_t = _add(parent_t, _mat_vec(parent_r, anchor_local))
        if j["type"] == "revolute":
            theta = angles.get(j["name"], 0.0)
            axis_local: Vec = tuple(j.get("axis") or (0.0, 0.0, 1.0))  # type: ignore[assignment]
            world_r = _mat_mat(parent_r, _rodrigues_matrix(axis_local, theta))
        else:
            world_r = parent_r
        frames[j["follower"]] = (world_r, world_t)
    return frames


def _jacobian_column_mm_per_rad(
    joints: list[dict[str, Any]],
    joint_name: str,
    target_part: str,
    base_angles: dict[str, float],
) -> Vec:
    """Numerical (central-difference) Jacobian column: how the
    ``target_part``'s world position changes per radian of ``joint_name``'s
    angle, holding every other joint at ``base_angles``. mm/rad."""
    plus = dict(base_angles)
    plus[joint_name] = plus.get(joint_name, 0.0) + _FD_DELTA_RAD
    minus = dict(base_angles)
    minus[joint_name] = minus.get(joint_name, 0.0) - _FD_DELTA_RAD
    frames_plus = _forward_kinematics(joints, plus)
    frames_minus = _forward_kinematics(joints, minus)
    if target_part not in frames_plus or target_part not in frames_minus:
        raise ValueError(f"target part {target_part!r} is not reachable from a real joint root")
    _, t_plus = frames_plus[target_part]
    _, t_minus = frames_minus[target_part]
    delta = _sub(t_plus, t_minus)
    return _scale(delta, 1.0 / (2.0 * _FD_DELTA_RAD))


def _encoder_resolution_rad(bits: int) -> float:
    """Angular resolution (rad) of an encoder reporting ``bits`` bits per
    revolution (``2**bits`` counts/rev)."""
    return (2.0 * math.pi) / (2**bits)


def make_repeatability_estimator(twin: Any) -> Any:
    """Return an async ``get(*, work_product_id, requirement_mm=0.5,
    end_effector_part=None) -> dict`` bound to a twin. Computes a
    worst-case end-effector repeatability estimate from real
    ``metadata.assembly.joints`` kinematics and real, cited actuator
    encoder specs, compared against a stated requirement (default the
    ticket's own +/-0.5mm)."""

    async def get(
        *,
        work_product_id: str,
        requirement_mm: float = 0.5,
        end_effector_part: str | None = None,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.get_repeatability_estimate") as span:
            wp_id = UUID(work_product_id)
            span.set_attribute("repeatability.work_product_id", work_product_id)

            wp = await twin.get_work_product(wp_id)
            if wp is None:
                raise ValueError(
                    f"twin.get_repeatability_estimate: no work_product {work_product_id!r}"
                )

            assembly = wp.metadata.get("assembly") if wp.metadata else None
            joints = assembly.get("joints", []) if isinstance(assembly, dict) else []
            if not joints:
                raise ValueError(
                    f"twin.get_repeatability_estimate: work_product {work_product_id!r} "
                    "has no assembly.joints metadata to derive a repeatability estimate from"
                )

            ordered = _topological_steps(joints)  # validates the graph; raises if cyclic
            target = end_effector_part or ordered[-1]["follower"]
            base_angles: dict[str, float] = {}

            contributions: list[dict[str, Any]] = []
            total_mm = 0.0

            ak80_joint = next((j for j in joints if j.get("name") == AK80_8_JOINT_NAME), None)
            if ak80_joint is not None:
                resolution_rad = _encoder_resolution_rad(AK80_8_OUTPUT_ENCODER_BITS)
                jacobian = _jacobian_column_mm_per_rad(
                    joints, AK80_8_JOINT_NAME, target, base_angles
                )
                jacobian_mag = _norm(jacobian)
                contribution_mm = jacobian_mag * resolution_rad
                contributions.append(
                    {
                        "joint_name": AK80_8_JOINT_NAME,
                        "actuator": "AK80-8 KV60 (CubeMars)",
                        "source": (
                            "https://www.cubemars.com/product/ak80-8-kv60-robotic-actuator.html "
                            "-- 15-bit output encoder (2**15 counts/rev)"
                        ),
                        "resolution_rad": round(resolution_rad, 8),
                        "jacobian_mm_per_rad": round(jacobian_mag, 4),
                        "contribution_mm": round(contribution_mm, 4),
                    }
                )
                total_mm += contribution_mm
            else:
                logger.warning(
                    "repeatability_estimate_ak80_joint_missing",
                    work_product_id=work_product_id,
                    expected_joint=AK80_8_JOINT_NAME,
                )

            contributions.append(
                {
                    "joint_name": "joint_3, joint_4, joint_5, joint_6 (combined)",
                    "actuator": "igus RL-SE-105-70 / RL-SE-80-50",
                    "source": (
                        "https://www.igus.com/product/22534 -- igus publishes no "
                        "per-joint encoder resolution for RL-SE strain-wave "
                        "actuators; their own whole-arm repeatability spec "
                        "(+/-1mm, consistent across every ReBeL cobot "
                        "configuration) is used here as a conservative stand-in "
                        "for these joints' combined contribution, not a "
                        "fabricated per-joint decomposition"
                    ),
                    "contribution_mm": IGUS_REBEL_WHOLE_ARM_REPEATABILITY_MM,
                }
            )
            total_mm += IGUS_REBEL_WHOLE_ARM_REPEATABILITY_MM

            warnings = [
                f"{name}: no real actuator is recorded in the BOM for this joint; "
                "it contributes nothing to this estimate (a real gap, not an "
                "assumed-zero error source)"
                for name in _UNMODELLED_JOINTS
                if any(j.get("name") == name for j in joints)
            ]

            passes = total_mm <= requirement_mm
            span.set_attribute("repeatability.worst_case_mm", total_mm)
            span.set_attribute("repeatability.passes", passes)

            logger.info(
                "repeatability_estimate_computed",
                work_product_id=work_product_id,
                target_part=target,
                worst_case_mm=round(total_mm, 4),
                requirement_mm=requirement_mm,
                passes=passes,
            )
            return {
                "work_product_id": work_product_id,
                "target_part": target,
                "requirement_mm": requirement_mm,
                "worst_case_repeatability_mm": round(total_mm, 4),
                "passes": passes,
                "contributions": contributions,
                "warnings": warnings,
            }

    return get
