"""Input/output schemas for the author_design_sketch skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProposedChange(BaseModel):
    """A single before/after row in the sketch's comparison table."""

    feature: str = Field(..., min_length=1, description="What's changing, e.g. 'Thigh length'")
    before: str = Field(..., min_length=1, description="Current value, e.g. '50 mm'")
    after: str = Field(..., min_length=1, description="Proposed value, e.g. '60 mm'")
    rationale: str = Field(default="", description="Why this change is proposed")


class AuthorDesignSketchInput(BaseModel):
    """Input for the design-sketch authoring skill."""

    name: str = Field(..., min_length=1, description="Work-product name/title")
    subject_name: str = Field(..., min_length=1, description="What's being sketched")
    summary: str = Field(default="", description="Short description of what this sketch checks")
    proposed_changes: list[ProposedChange] = Field(..., min_length=1)
    source_node_ids: list[str] = Field(
        default_factory=list,
        description="Existing work-product ids this sketch reviews (revision case)",
    )
    project_id: str | None = Field(default=None)
    domain: str = Field(default="mechanical")


class AuthorDesignSketchOutput(BaseModel):
    """Output from the design-sketch authoring skill."""

    node_id: str = Field(..., description="DESIGN_SKETCH work-product node id")
    change_count: int = Field(..., ge=1)
    approved: bool = Field(..., description="Always False on creation -- awaits human approval")
