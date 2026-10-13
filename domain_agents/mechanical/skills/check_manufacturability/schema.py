"""Input/output schemas for the check_manufacturability skill (FORGE-611)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

FeatureKind = Literal[
    "wall_thickness",
    "hole_diameter",
    "inside_corner_radius",
    "bend_radius",
    "draft_angle",
    "overhang_angle",
    "feature_size",
    "envelope",
]
Status = Literal["pass", "fail", "unknown"]


class DesignFeature(BaseModel):
    """One feature of the design, and how its value is known.

    ``basis`` is part of the answer: MetaForge cannot yet measure a wall
    thickness or a bend radius off the geometry, so most values are what the
    part was authored with (``declared``) or what someone stated. A
    ``measured`` value came from a tool (``freecad.measure``, the overhang
    metric). The findings carry it so a reviewer sees which is which.
    """

    name: str = Field(..., min_length=1, description="e.g. 'leg wall', 'top bolt holes'")
    kind: FeatureKind
    value: float | None = Field(
        default=None, description="mm, or degrees for draft_angle and overhang_angle"
    )
    dims_mm: list[float] | None = Field(
        default=None, min_length=3, max_length=3, description="for kind envelope: L x W x H"
    )
    basis: Literal["measured", "declared", "stated"] = "declared"


class ProcessLimit(BaseModel):
    """A limit of the chosen process, always with where it came from."""

    kind: FeatureKind
    min: float | None = None
    max: float | None = None
    max_dims_mm: list[float] | None = Field(default=None, min_length=3, max_length=3)
    source_ref: str = Field(..., min_length=1, description="design_value id or citation")


class CheckManufacturabilityInput(BaseModel):
    process: str = Field(..., min_length=1)
    features: list[DesignFeature] = Field(default_factory=list)
    limits: list[ProcessLimit] = Field(default_factory=list)
    work_product_id: UUID | None = Field(default=None, description="The cad_model checked.")


class DfmFinding(BaseModel):
    feature: str
    kind: FeatureKind
    status: Status
    basis: str
    value: float | None = None
    limit: str = ""
    margin: float | None = Field(default=None, description="How far inside (+) or outside (-).")
    limit_source: str = ""
    message: str


class CheckManufacturabilityOutput(BaseModel):
    process: str
    verdict: Literal["manufacturable", "not_manufacturable", "incomplete"]
    findings: list[DfmFinding]
    passed: int
    failed: int
    unknown: int
    unknowns: list[str] = Field(default_factory=list, description="What could not be checked.")
