"""Lightweight headless physics sanity check for an exported URDF robot
description (MET-740 Phase 2) -- the cheapest, first-pass tier of "ready for
simulation" validation: load the robot into a real physics engine, drop it
under gravity alone (no control input) for a short burst, and check nothing
diverges (NaN/Inf, exploding velocities). This is the standard smoke test
the robotics ecosystem runs before ever touching a full target simulator
(Gazebo/Isaac Sim) -- PyBullet specifically, not MetaForge's target
Gazebo/Isaac Sim, because it needs no GPU, no multi-GB simulator install,
and runs in well under a second for a robot this size. It catches the most
common real-world export bugs: degenerate/zero inertia, disconnected link
trees, colliders that self-intersect at rest.

PyBullet import is conditional (mirrors CadQuery's own conditional import
in ``operations.py``) so this module can be imported/tested without the
real package installed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cadquery.physics_check")

try:
    import pybullet as _pb
    import pybullet_data as _pb_data

    HAS_PYBULLET = True
except ImportError:  # pragma: no cover - exercised only when pybullet is absent
    _pb = None
    _pb_data = None
    HAS_PYBULLET = False


class PyBulletNotAvailableError(RuntimeError):
    """Raised when validate_physics_stability is called without pybullet installed."""

    def __init__(self) -> None:
        super().__init__(
            "pybullet is not installed -- physics-stability validation needs it "
            "(pip install pybullet). Run inside the cadquery adapter container."
        )


def validate_physics_stability(
    urdf_path: str,
    *,
    steps: int = 300,
    drop_height_m: float = 0.05,
    ground_plane: bool = True,
    max_stable_linear_velocity_mps: float = 20.0,
    max_stable_angular_velocity_radps: float = 20.0,
) -> dict[str, Any]:
    """Load ``urdf_path`` into a headless PyBullet world and step it under
    gravity alone for ``steps`` ticks, with no control input.

    ``ground_plane`` (default on) loads PyBullet's bundled flat-ground
    primitive so the robot actually settles under contact/collision
    resolution rather than free-falling forever -- this is what makes the
    check diagnostic for broken/inverted/self-intersecting collision
    meshes, not just numerically-exploding inertia. ``drop_height_m`` is a
    small ground clearance (not a real "drop test") -- it exists so a
    robot whose declared origin already sits exactly at z=0 doesn't start
    in an already-interpenetrating, guaranteed-unstable state purely from
    float precision, not because a drop is physically meaningful here.

    Never raises for an unstable/broken robot -- that verdict IS the
    result. Only raises for a setup failure: missing pybullet, a missing
    file, or a URDF pybullet's own parser rejects outright.
    """
    if not HAS_PYBULLET:
        raise PyBulletNotAvailableError

    path = Path(urdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"URDF not found: {urdf_path}")

    with tracer.start_as_current_span("cadquery.validate_physics_stability") as span:
        span.set_attribute("physics_check.urdf_path", str(path))
        span.set_attribute("physics_check.steps", steps)
        span.set_attribute("physics_check.ground_plane", ground_plane)

        client = _pb.connect(_pb.DIRECT)
        try:
            _pb.setGravity(0, 0, -9.81, physicsClientId=client)
            if ground_plane:
                _pb.setAdditionalSearchPath(_pb_data.getDataPath(), physicsClientId=client)
                _pb.loadURDF("plane.urdf", physicsClientId=client)
            # Mesh <mesh filename="leg_fl.stl"> references are bare/relative
            # -- same directory as the URDF itself (see cad_export/routes.py
            # and use-urdf-robot.ts's meshBaseUrl, which resolve the same
            # co-located-files assumption for the browser preview). Added
            # AFTER plane.urdf's own search path so it doesn't shadow it.
            _pb.setAdditionalSearchPath(str(path.parent), physicsClientId=client)
            robot = _pb.loadURDF(
                str(path),
                basePosition=[0, 0, drop_height_m],
                useFixedBase=False,
                physicsClientId=client,
            )

            joint_count = _pb.getNumJoints(robot, physicsClientId=client)
            link_names = ["base"] + [
                _pb.getJointInfo(robot, i, physicsClientId=client)[12].decode("utf-8")
                for i in range(joint_count)
            ]

            max_linear_velocity = 0.0
            max_angular_velocity = 0.0
            diverged_at_step: int | None = None
            steps_run = 0

            for step in range(steps):
                _pb.stepSimulation(physicsClientId=client)
                steps_run = step + 1
                pos, orn = _pb.getBasePositionAndOrientation(robot, physicsClientId=client)
                lin, ang = _pb.getBaseVelocity(robot, physicsClientId=client)
                values = (*pos, *orn, *lin, *ang)
                if any(math.isnan(v) or math.isinf(v) for v in values):
                    diverged_at_step = step
                    break
                max_linear_velocity = max(max_linear_velocity, math.dist((0, 0, 0), lin))
                max_angular_velocity = max(max_angular_velocity, math.dist((0, 0, 0), ang))

            final_pos, _final_orn = _pb.getBasePositionAndOrientation(robot, physicsClientId=client)

            stable = (
                diverged_at_step is None
                and max_linear_velocity <= max_stable_linear_velocity_mps
                and max_angular_velocity <= max_stable_angular_velocity_radps
            )

            result: dict[str, Any] = {
                "stable": stable,
                "diverged_at_step": diverged_at_step,
                "steps_run": steps_run,
                "link_count": joint_count + 1,
                "joint_count": joint_count,
                "link_names": link_names,
                "max_linear_velocity_mps": round(max_linear_velocity, 4),
                "max_angular_velocity_radps": round(max_angular_velocity, 4),
                "final_base_position_m": [round(v, 4) for v in final_pos],
            }
            span.set_attribute("physics_check.stable", stable)
            logger.info(
                "physics_stability_checked",
                urdf_path=str(path),
                stable=stable,
                steps_run=steps_run,
                joint_count=joint_count,
                max_linear_velocity_mps=result["max_linear_velocity_mps"],
                max_angular_velocity_radps=result["max_angular_velocity_radps"],
            )
            return result
        finally:
            _pb.disconnect(physicsClientId=client)
