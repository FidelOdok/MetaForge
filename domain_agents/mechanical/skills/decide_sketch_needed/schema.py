"""Input/output schemas for the decide_sketch_needed skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class DecideSketchNeededInput(BaseModel):
    """Describes a proposed CAD/build action so the skill can decide whether a
    design_sketch work product (twin.commit_design_sketch) must be authored and
    human-approved before any real CAD/build tool is invoked.

    Not robot-specific -- applies to any CAD work: a single bracket, a
    multi-part enclosure, a kinematic assembly, or a revision to something
    already built.
    """

    source_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Existing Twin work-product IDs (cad_model, robot_description, ...) this "
            "action would modify. Empty means a brand-new design with nothing built yet."
        ),
    )
    part_count: int = Field(
        default=1, ge=1, description="Number of distinct parts/links/bodies involved."
    )
    has_moving_joints: bool = Field(
        default=False,
        description=(
            "Whether the design includes kinematic joints (revolute, prismatic, ...) between parts."
        ),
    )
    topology_is_novel: bool = Field(
        default=False,
        description=(
            "Whether this shape or mechanism type has no prior built reference in "
            "this project -- nothing to compare proportions/topology against."
        ),
    )
    user_requested_review: bool = Field(
        default=False,
        description="Explicit human ask for a sketch/review regardless of the other signals.",
    )


class DecideSketchNeededOutput(BaseModel):
    """The gate decision."""

    sketch_needed: bool = Field(
        ..., description="Whether a design_sketch must be committed and approved first."
    )
    reasons: list[str] = Field(
        ..., min_length=1, description="Human-readable reason(s) the decision was made."
    )
    is_revision: bool = Field(
        ..., description="Whether this action would modify an already-built design."
    )
    recommended_source_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids to pass as source_node_ids on twin.commit_design_sketch, if a "
            "sketch is needed (echoes the input's source_node_ids)."
        ),
    )
