"""Pydantic request/response models for the joint-loads route (FORGE-283)."""

from __future__ import annotations

from pydantic import BaseModel

Vec3 = tuple[float, float, float]


class LinkLoadInput(BaseModel):
    name: str
    com_world_mm: Vec3
    mass_kg: float


class JointPositionInput(BaseModel):
    name: str
    position_world_mm: Vec3


class JointLoadRequest(BaseModel):
    """A posed serial chain, base-to-tip: ``links[i]`` is the link driven
    by (immediately outboard of) ``joints[i]``."""

    links: list[LinkLoadInput]
    joints: list[JointPositionInput]
    payload_mass_kg: float = 0.0
    payload_position_world_mm: Vec3 | None = None


class JointLoadResult(BaseModel):
    joint_name: str
    supported_mass_kg: float
    reaction_force_n: Vec3
    reaction_moment_n_mm: Vec3


class JointLoadResponse(BaseModel):
    loads: list[JointLoadResult]
    worst_joint: JointLoadResult
