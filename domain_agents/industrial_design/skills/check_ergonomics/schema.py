"""Input/output schemas for the check_ergonomics skill (FORGE-633)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ErgoKind = Literal[
    "grip_diameter",
    "grip_width",
    "handle_length",
    "finger_clearance",
    "button_size",
    "actuation_force",
    "operating_height",
    "reach_distance",
    "product_mass",
]
Status = Literal["pass", "fail", "unknown"]

#: One unit per kind; a limit or feature in another unit is not accepted.
UNITS: dict[str, str] = {
    "grip_diameter": "mm",
    "grip_width": "mm",
    "handle_length": "mm",
    "finger_clearance": "mm",
    "button_size": "mm",
    "actuation_force": "N",
    "operating_height": "mm",
    "reach_distance": "mm",
    "product_mass": "kg",
}


class ErgoFeature(BaseModel):
    """A feature a person touches, holds, reaches or lifts, and how it is known."""

    name: str = Field(..., min_length=1, description="e.g. 'handle', 'power button'")
    kind: ErgoKind
    value: float | None = None
    basis: Literal["measured", "declared", "stated"] = "declared"


class ErgoLimit(BaseModel):
    """A limit for one kind, for a population, with where it came from.

    ``source_ref`` names the dataset or guidance and the figure used, for
    example "ISO 7250-2 table, hand breadth, 5th percentile female" or a
    published ergonomics handbook's grip-diameter range.
    """

    kind: ErgoKind
    min: float | None = None
    max: float | None = None
    population: str = Field(..., min_length=1, description="Who it is for, e.g. 'UK adults 5-95%'")
    source_ref: str = Field(..., min_length=1)


class CheckErgonomicsInput(BaseModel):
    product: str = Field(..., min_length=1)
    user_population: str = Field(
        ..., min_length=1, description="The intended users, as the requirement states them."
    )
    features: list[ErgoFeature] = Field(default_factory=list)
    limits: list[ErgoLimit] = Field(default_factory=list)
    work_product_id: UUID | None = None


class ErgoFinding(BaseModel):
    feature: str
    kind: ErgoKind
    status: Status
    basis: str
    value: float | None = None
    unit: str
    limit: str = ""
    margin: float | None = None
    population: str = ""
    limit_source: str = ""
    message: str


class CheckErgonomicsOutput(BaseModel):
    product: str
    user_population: str
    verdict: Literal["fits", "does_not_fit", "incomplete"]
    findings: list[ErgoFinding]
    passed: int
    failed: int
    unknown: int
    unknowns: list[str] = Field(default_factory=list)
    population_mismatches: list[str] = Field(
        default_factory=list,
        description="Limits whose population differs from the intended users.",
    )
