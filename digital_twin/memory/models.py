"""Pydantic models for the agent memory layer.

Models here represent a single agent-task *experience* once it has been
extracted from an ``AGENT_TASK_*`` event, scored, and (optionally) embedded
for pgvector storage.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ConfidenceTier(StrEnum):
    """Provenance tier for a memory record.

    Aligned with MET-462 property-extraction tiers so the same vocabulary
    flows from L1 extraction through to L3 reasoning:

    * ``VERBATIM`` — copied directly from a structured event field (1.0)
    * ``LLM_INFERRED`` — produced by an LLM synthesis step (0.6-0.8)
    * ``DERIVED`` — computed from other records (0.4-0.6)
    """

    VERBATIM = "verbatim"
    LLM_INFERRED = "llm_inferred"
    DERIVED = "derived"


class ExperienceMemory(BaseModel):
    """A single agent-task experience, ready for indexing.

    One ``ExperienceMemory`` corresponds to one ``AGENT_TASK_*`` event
    (``STARTED`` + matching ``COMPLETED`` / ``FAILED`` are typically
    folded into a single completed-experience record by the consumer).
    """

    id: UUID = Field(default_factory=uuid4, description="Unique experience ID")
    run_id: str = Field(description="Workflow run that produced this experience")
    step_id: str = Field(description="Step within the workflow run")
    agent_code: str = Field(description="Agent that executed the task")
    task_type: str = Field(
        default="",
        description="Optional sub-type (e.g. 'validate_stress', 'erc_check')",
    )
    success: bool = Field(description="Whether the task completed without error")
    duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="End-to-end duration if known",
    )
    result_summary: str = Field(
        default="",
        description="Short textual summary of the result, used for embedding",
    )
    error: str | None = Field(default=None, description="Error message if failed")
    project_id: UUID | None = Field(
        default=None,
        description="Project the experience belongs to (for tenant isolation)",
    )
    timestamp: datetime = Field(description="Event wall-clock timestamp (UTC)")
    importance: float = Field(
        ge=0.0,
        le=1.0,
        description="Importance score (recency·relevance·criticality)",
    )
    confidence: ConfidenceTier = Field(
        default=ConfidenceTier.VERBATIM,
        description="Provenance tier for this record",
    )
    embedding: list[float] = Field(
        default_factory=list,
        description="Vector embedding of ``result_summary`` (empty until embedded)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form extras (event_id, source, etc.)",
    )

    @property
    def confidence_score(self) -> float:
        """Numeric confidence value associated with the tier."""
        return _CONFIDENCE_SCORES[self.confidence]


_CONFIDENCE_SCORES: dict[ConfidenceTier, float] = {
    ConfidenceTier.VERBATIM: 1.0,
    ConfidenceTier.LLM_INFERRED: 0.7,
    ConfidenceTier.DERIVED: 0.5,
}


class MemorySearchHit(BaseModel):
    """A single hit returned by ``retrieve_similar_experience``."""

    experience: ExperienceMemory
    similarity: float = Field(
        ge=-1.0,
        le=1.0,
        description="Cosine similarity to the query embedding",
    )
    rank: int = Field(ge=0, description="Zero-indexed rank in the result set")
