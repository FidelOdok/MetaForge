"""Quasi-static joint-load statics for a posed serial robot-arm chain (FORGE-283).

Given a posed serial kinematic chain -- link centers of mass and joint
positions already resolved to world-frame millimetres for a chosen pose --
and an optional end-effector payload, computes the reaction force and
moment each joint must react to hold the chain in static equilibrium under
gravity alone.

This is deliberately **quasi-static**: no velocity or acceleration terms
(the inertial/Coriolis/centrifugal loads a real trajectory would add), and
no friction, actuator torque-speed curve, or contact/collision loads --
those need a real multibody dynamics engine. Neither of this repo's two
simulation adapters can supply that today: `tool_registry/tools/gazebo/
result_parser.py`'s own docstring says "Extracting contact forces or full
trajectories is deferred", and Isaac Sim's `run_physics` output is just
success/exit_code/stdout/stderr/duration_seconds -- no force or torque data
at all. A full dynamic worst-case-over-a-motion search is future work
(tracked against FORGE-284, which owns that dynamics engine).

What this DOES capture honestly: the "worst static pose" case (e.g. an arm
fully extended holding a payload) that dominates structural sizing for most
robot-arm designs -- exactly the load case this ticket's `calculix.run_fea`
hookup targets.

Units: millimetres for position (matching this repo's CalculiX mm+N+MPa
consistent unit system -- see deck_builder.py's own module docstring),
kilograms for mass, Newtons and Newton-millimetres for the reaction
outputs (mm, not m, so a reaction force/moment can be fed directly into
build_static_stress_deck's load_force_n without a unit conversion).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec = tuple[float, float, float]

# Standard gravity (CODATA), m/s^2.
_STANDARD_GRAVITY_M_S2 = 9.80665


@dataclass(frozen=True)
class ChainLink:
    """One link of the posed serial chain, world-frame millimetres."""

    name: str
    com_world_mm: Vec
    mass_kg: float


@dataclass(frozen=True)
class ChainJoint:
    """One joint of the posed serial chain, world-frame millimetres.

    ``links`` and ``joints`` passed to `compute_joint_loads` must be the
    same length and given base-to-tip in matching order: ``links[i]`` is
    the link driven by (immediately outboard of) ``joints[i]``. A joint's
    "outboard sub-chain" -- the mass it must support -- is therefore
    ``links[i:]`` plus the payload, if any.
    """

    name: str
    position_world_mm: Vec


@dataclass(frozen=True)
class JointLoad:
    """The static reaction load a single joint must react."""

    joint_name: str
    supported_mass_kg: float
    reaction_force_n: Vec
    reaction_moment_n_mm: Vec


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _norm(a: Vec) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def compute_joint_loads(
    *,
    links: list[ChainLink],
    joints: list[ChainJoint],
    payload_mass_kg: float = 0.0,
    payload_position_world_mm: Vec | None = None,
    gravity_m_s2: float = _STANDARD_GRAVITY_M_S2,
) -> list[JointLoad]:
    """Static reaction force/moment at every joint of a posed serial chain.

    Gravity acts in -z. For each joint, the outboard sub-chain (that joint's
    own link plus every link further from the base, plus the payload if
    present) is summed: the reaction force is the total weight the joint
    (and everything inboard of it) must support, and the reaction moment is
    the moment that same structure must supply to hold the sub-chain
    static -- equal and opposite to the moment gravity exerts on that
    sub-chain about the joint's own position, i.e. the internal
    bending/torsional load the joint and its adjoining structure carry.

    Raises ``ValueError`` if ``links`` and ``joints`` aren't the same
    length (the base-to-tip pairing this function relies on is otherwise
    ambiguous), or if a payload mass is given without a position.
    """
    if len(links) != len(joints):
        raise ValueError(
            f"links and joints must be the same length (got {len(links)} links, "
            f"{len(joints)} joints) -- joints[i] must pair with links[i], its "
            "immediately outboard link"
        )
    if not links:
        raise ValueError("links/joints must be non-empty")
    if payload_mass_kg > 0 and payload_position_world_mm is None:
        raise ValueError("payload_position_world_mm is required when payload_mass_kg > 0")

    weight_vectors_n: list[Vec] = [(0.0, 0.0, -link.mass_kg * gravity_m_s2) for link in links]
    masses = [link.mass_kg for link in links]
    coms = [link.com_world_mm for link in links]

    if payload_mass_kg > 0 and payload_position_world_mm is not None:
        weight_vectors_n.append((0.0, 0.0, -payload_mass_kg * gravity_m_s2))
        masses.append(payload_mass_kg)
        coms.append(payload_position_world_mm)

    results: list[JointLoad] = []
    for i, joint in enumerate(joints):
        outboard = range(i, len(masses))
        supported_mass_kg = sum(masses[k] for k in outboard)
        reaction_force_n = (0.0, 0.0, supported_mass_kg * gravity_m_s2)
        gravity_moment = (0.0, 0.0, 0.0)
        for k in outboard:
            r = _sub(coms[k], joint.position_world_mm)
            gravity_moment = _add(gravity_moment, _cross(r, weight_vectors_n[k]))
        # The joint reacts the equal-and-opposite moment to hold the
        # outboard sub-chain static (sum of moments = 0).
        moment = (-gravity_moment[0], -gravity_moment[1], -gravity_moment[2])
        results.append(
            JointLoad(
                joint_name=joint.name,
                supported_mass_kg=supported_mass_kg,
                reaction_force_n=reaction_force_n,
                reaction_moment_n_mm=moment,
            )
        )
    return results


def worst_joint_load(loads: list[JointLoad]) -> JointLoad:
    """The joint with the largest reaction-moment magnitude -- the one most
    likely to govern structural sizing for a static hold pose."""
    if not loads:
        raise ValueError("loads must be non-empty")
    return max(loads, key=lambda load: _norm(load.reaction_moment_n_mm))
