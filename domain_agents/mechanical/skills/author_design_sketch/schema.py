"""Input/output schemas for the author_design_sketch skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Segment(BaseModel):
    """One linked segment in a kinematic-chain sketch (e.g. thigh, shin,
    foot) -- a chain of these, drawn end-to-end, is what actually makes the
    sketch a *sketch*: a scaled 2D diagram of the part's proportions and
    topology, not just a table of numbers."""

    name: str = Field(..., min_length=1, description="e.g. 'Thigh', 'Shin', 'Bracket arm'")
    length_mm: float = Field(..., gt=0, description="Segment length in mm")
    thickness_mm: float = Field(..., gt=0, description="Segment thickness/width in mm")
    joint_angle_deg: float = Field(
        default=0.0,
        description="Bend from the previous segment's direction, in degrees. 0 continues straight.",
    )


class AuthorDesignSketchInput(BaseModel):
    """Input for the design-sketch authoring skill.

    ``after_segments`` describes the proposed chain -- always required, even
    for a brand-new design with nothing built yet. ``before_segments`` is
    only given for a revision of an already-built design; when empty, the
    sketch draws just the proposed chain (nothing to compare against).
    """

    name: str = Field(..., min_length=1, description="Work-product name/title")
    subject_name: str = Field(..., min_length=1, description="What's being sketched")
    summary: str = Field(default="", description="Short description of what this sketch checks")
    after_segments: list[Segment] = Field(
        ..., min_length=1, description="Proposed chain, in order from the fixed/root end"
    )
    before_segments: list[Segment] = Field(
        default_factory=list,
        description="Existing chain for a revision comparison; empty for a brand-new design",
    )
    change_notes: list[str] = Field(
        default_factory=list, description="Rationale bullets for the proposed change(s)"
    )
    source_node_ids: list[str] = Field(
        default_factory=list,
        description="Existing work-product ids this sketch reviews (revision case)",
    )
    project_id: str | None = Field(default=None)
    domain: str = Field(default="mechanical")


class AuthorDesignSketchOutput(BaseModel):
    """Output from the design-sketch authoring skill."""

    node_id: str = Field(..., description="DESIGN_SKETCH work-product node id")
    segment_count: int = Field(..., ge=1)
    is_revision: bool = Field(..., description="Whether before_segments were supplied")
    approved: bool = Field(..., description="Always False on creation -- awaits human approval")
