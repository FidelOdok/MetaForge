"""Gate engine models for EVT/DVT/PVT progression.

Defines the data structures for gate stages, criteria, scoring, and
transition lifecycle used by the Gate Engine to evaluate readiness
for hardware development milestones.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class GateStage(StrEnum):
    """Hardware development gate stages."""

    EVT = "EVT"  # Engineering Validation Test
    DVT = "DVT"  # Design Validation Test
    PVT = "PVT"  # Production Validation Test


class GateCriterionType(StrEnum):
    """Types of gate readiness criteria."""

    REQUIREMENT_COVERAGE = "requirement_coverage"
    BOM_RISK = "bom_risk"
    CONSTRAINT_COMPLIANCE = "constraint_compliance"
    TEST_EVIDENCE = "test_evidence"
    DESIGN_REVIEW = "design_review"


class GateCriterion(BaseModel):
    """A single gate readiness criterion with weight and threshold."""

    type: GateCriterionType
    name: str
    description: str
    weight: float = Field(ge=0.0, le=1.0, description="Weight for scoring (0-1)")
    threshold: float = Field(
        ge=0.0, le=100.0, description="Minimum score to pass (0-100)"
    )
    required: bool = Field(
        default=True, description="If True, failing this criterion blocks the gate"
    )


class CriterionResult(BaseModel):
    """Result of evaluating a single gate criterion."""

    criterion: GateCriterion
    score: float = Field(ge=0.0, le=100.0, description="Score (0-100)")
    passed: bool
    details: str = ""
    blockers: list[str] = Field(default_factory=list)


class ReadinessScore(BaseModel):
    """Aggregate readiness score for a gate stage."""

    stage: GateStage
    overall_score: float = Field(ge=0.0, le=100.0)
    criteria_results: list[CriterionResult] = Field(default_factory=list)
    ready: bool
    blockers: list[str] = Field(default_factory=list)
    evaluated_at: datetime


class GateTransitionStatus(StrEnum):
    """Status of a gate transition request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GateTransition(BaseModel):
    """A gate transition request and its lifecycle."""

    id: UUID = Field(default_factory=uuid4)
    from_stage: GateStage | None = None
    to_stage: GateStage
    readiness_score: ReadinessScore
    approved_by: str | None = None
    approved_at: datetime | None = None
    comment: str = ""
    status: GateTransitionStatus = GateTransitionStatus.PENDING


class GateDefinition(BaseModel):
    """Definition of criteria required for a gate stage."""

    stage: GateStage
    criteria: list[GateCriterion] = Field(default_factory=list)
    min_overall_score: float = Field(
        ge=0.0, le=100.0, description="Minimum weighted overall score to pass"
    )
