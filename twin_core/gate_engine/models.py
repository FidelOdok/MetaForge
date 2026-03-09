"""Models for the gate engine — EVT/DVT/PVT readiness gates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class GatePhase(StrEnum):
    """Hardware development gate phases."""

    EVT = "evt"  # Engineering Validation Test
    DVT = "dvt"  # Design Validation Test
    PVT = "pvt"  # Production Validation Test


class GateCriterion(BaseModel):
    """A single criterion that contributes to gate readiness.

    Each criterion has a weight (importance) and a score (0.0-1.0)
    indicating how well it has been satisfied.
    """

    name: str
    description: str = ""
    weight: float = 1.0
    score: float = 0.0  # 0.0 = not met, 1.0 = fully met
    required: bool = False  # If True, score must be 1.0 to pass
    category: str = "general"
    evidence: list[str] = Field(default_factory=list)


class GateDefinition(BaseModel):
    """Definition of a hardware gate with its criteria and threshold."""

    id: UUID = Field(default_factory=uuid4)
    phase: GatePhase
    name: str
    description: str = ""
    threshold: float = 0.8  # Weighted score must be >= this to pass
    criteria: list[GateCriterion] = Field(default_factory=list)


class ReadinessScore(BaseModel):
    """Computed readiness score for a gate."""

    gate_id: UUID
    phase: GatePhase
    weighted_score: float  # 0.0 - 1.0
    threshold: float
    passed: bool
    blockers: list[str] = Field(default_factory=list)
    criteria_scores: dict[str, float] = Field(default_factory=dict)
    computed_at: datetime


class GateTransitionResult(BaseModel):
    """Result of attempting a gate transition."""

    allowed: bool
    from_phase: GatePhase | None = None
    to_phase: GatePhase
    readiness: ReadinessScore
    message: str


class GateSnapshot(BaseModel):
    """Historical snapshot of a gate evaluation."""

    id: UUID = Field(default_factory=uuid4)
    gate_id: UUID
    phase: GatePhase
    score: ReadinessScore
    recorded_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
