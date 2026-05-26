"""Pydantic models for synthesized consolidation insights.

An ``Insight`` is the LLM-synthesized lesson learned across one
``ExperienceGroup``. The downstream writer persists it to Neo4j
(structured edges to components / decisions) and pgvector
(embedded ``narrative`` for semantic search).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.models import ConfidenceTier


class InsightKind(StrEnum):
    """What kind of statement the insight is."""

    PATTERN = "pattern"  # "agents repeatedly hit X"
    PRINCIPLE = "principle"  # "do Y to avoid Z"
    FAILURE_MODE = "failure_mode"
    OBSERVATION = "observation"


class InsightStatus(StrEnum):
    """Lifecycle state of a stored insight (MET-455).

    * ``ACTIVE`` — trusted; surfaced to agents normally.
    * ``STALE_WARN`` — confidence has decayed (or re-validation failed);
      flagged for review. Still readable, but consumers should treat it
      with caution / prefer fresher insights.
    """

    ACTIVE = "active"
    STALE_WARN = "stale_warn"


class Insight(BaseModel):
    """A single synthesized lesson covering an ``ExperienceGroup``."""

    id: UUID = Field(default_factory=uuid4)
    theme: ConsolidationTheme
    kind: InsightKind = InsightKind.OBSERVATION
    narrative: str = Field(
        min_length=1,
        description="Short paragraph summarizing what was learned.",
    )
    supporting_experience_ids: list[UUID] = Field(
        default_factory=list,
        description="IDs of the experiences this insight was synthesized from.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="LLM-asserted confidence; the validator gates on >= 0.70.",
    )
    confidence_tier: ConfidenceTier = ConfidenceTier.LLM_INFERRED
    status: InsightStatus = Field(
        default=InsightStatus.ACTIVE,
        description="Lifecycle state — STALE_WARN once decayed below the floor.",
    )
    synthesized_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the LLM synthesized the insight.",
    )
