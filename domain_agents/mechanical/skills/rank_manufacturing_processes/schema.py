"""Input/output schemas for the rank_manufacturing_processes skill (FORGE-611)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["pass", "fail", "unknown"]


class CandidateProcess(BaseModel):
    """One process that could make the part, with what it can do.

    Every capability is optional because none is ever defaulted: a value the
    caller could not source stays ``None`` and the criterion it feeds reads
    ``unknown``. ``source_refs`` are the design_value ids or citations the
    values came from.
    """

    process: str = Field(
        ..., min_length=1, description="e.g. 'laser cut + MIG weld', 'cnc_milling'"
    )
    route: Literal["in_house", "vendor"] | None = Field(
        default=None, description="Who would make it with this process."
    )
    available: bool | None = Field(
        default=None,
        description="Whether the machine (in-house) or a supplier (vendor) is actually at hand.",
    )
    max_envelope_mm: list[float] | None = Field(
        default=None, min_length=3, max_length=3, description="Largest part it can make, L x W x H."
    )
    min_feature_mm: float | None = Field(default=None, gt=0)
    achievable_tolerance_mm: float | None = Field(default=None, gt=0)
    materials: list[str] = Field(
        default_factory=list, description="Material classes it handles; empty means not stated."
    )
    setup_cost: float | None = Field(default=None, ge=0, description="One-off tooling or setup.")
    unit_cost: float | None = Field(default=None, ge=0, description="Cost per part.")
    lead_time_days: float | None = Field(default=None, ge=0)
    source_refs: list[str] = Field(default_factory=list)


class RankManufacturingProcessesInput(BaseModel):
    """The part's needs and the candidate processes."""

    quantity: int | None = Field(default=None, gt=0, description="Parts to make.")
    material_class: str | None = Field(default=None, description="e.g. 'steel', 'plywood'.")
    part_envelope_mm: list[float] | None = Field(default=None, min_length=3, max_length=3)
    min_feature_mm: float | None = Field(default=None, gt=0)
    tightest_tolerance_mm: float | None = Field(default=None, gt=0)
    currency: str = Field(default="", description="Unit of the cost values, e.g. 'GBP'.")
    candidates: list[CandidateProcess] = Field(..., min_length=1)


class CriterionResult(BaseModel):
    name: str
    status: Status
    detail: str


class RankedProcess(BaseModel):
    process: str
    route: str | None
    verdict: Literal["fits", "does_not_fit", "incomplete"]
    criteria: list[CriterionResult]
    cost_at_quantity: float | None = Field(
        default=None, description="setup_cost + unit_cost x quantity, when all three are known."
    )
    cost_per_part: float | None = None
    reasons: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class RankManufacturingProcessesOutput(BaseModel):
    ranked: list[RankedProcess]
    recommended: str | None = Field(
        default=None,
        description=(
            "The best candidate that fits; the best incomplete one when none fully fits; "
            "None when every candidate fails. A recommendation, not a decision: the "
            "person picks at the route gate."
        ),
    )
    unknown_inputs: list[str] = Field(
        default_factory=list, description="Part needs that were not given, so not checked."
    )
