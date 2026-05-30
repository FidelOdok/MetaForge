"""Pydantic request / response schemas for ``/v1/memory``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from digital_twin.memory.client import MAX_RETRIEVAL_LIMIT
from digital_twin.memory.consolidation.insight import InsightKind, InsightStatus
from digital_twin.memory.consolidation.modes import ConsolidationMode
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.models import ConfidenceTier


class MemoryRetrieveRequest(BaseModel):
    """Request body for ``POST /v1/memory/retrieve``."""

    goal: str = Field(
        min_length=1,
        description="Natural-language description of the task.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=MAX_RETRIEVAL_LIMIT,
        description="Maximum number of experiences to return.",
    )
    project_id: UUID | None = Field(
        default=None,
        alias="projectId",
        description="Optional project scope.",
    )
    agent_code: str | None = Field(
        default=None,
        alias="agentCode",
        description="Optional filter to a specific agent.",
    )
    only_success: bool | None = Field(
        default=None,
        alias="onlySuccess",
        description="True = success-only, False = failures-only, None = no filter.",
    )
    min_similarity: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        alias="minSimilarity",
        description=(
            "Optional retrieval-confidence floor: drop hits whose cosine "
            "similarity is below this value. None = no floor."
        ),
    )

    model_config = {"populate_by_name": True}


class MemoryHitResponse(BaseModel):
    """Wire shape of a single hit in the response payload."""

    experience_id: UUID = Field(alias="experienceId")
    similarity: float = Field(ge=-1.0, le=1.0)
    rank: int = Field(ge=0)
    agent_code: str = Field(alias="agentCode")
    task_type: str = Field(alias="taskType")
    run_id: str = Field(alias="runId")
    step_id: str = Field(alias="stepId")
    success: bool
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")
    result_summary: str = Field(alias="resultSummary")
    error: str | None = None
    importance: float = Field(ge=0.0, le=1.0)
    confidence: ConfidenceTier
    timestamp: datetime
    project_id: UUID | None = Field(default=None, alias="projectId")

    model_config = {"populate_by_name": True}


class MemoryRetrieveResponse(BaseModel):
    """Response body for ``POST /v1/memory/retrieve``."""

    hits: list[MemoryHitResponse]
    query: str
    total_found: int = Field(alias="totalFound")

    model_config = {"populate_by_name": True}


class ConsolidationTriggerRequest(BaseModel):
    """Request body for ``POST /v1/memory/consolidate``."""

    mode: ConsolidationMode = Field(
        default=ConsolidationMode.ON_DEMAND,
        description=(
            "Consolidation mode. Defaults to on_demand since the REST endpoint "
            "is a manual trigger; the Temporal worker handles background."
        ),
    )
    since: datetime | None = None
    until: datetime | None = None
    project_id: UUID | None = Field(default=None, alias="projectId")
    theme: ConsolidationTheme | None = None
    min_importance: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        alias="minImportance",
    )
    fetch_limit: int | None = Field(default=None, ge=1, alias="fetchLimit")

    model_config = {"populate_by_name": True}


class ConsolidationTriggerResponse(BaseModel):
    """Wire shape of a single consolidation pass result."""

    mode: ConsolidationMode
    fetched_count: int = Field(alias="fetchedCount")
    group_count: int = Field(alias="groupCount")
    synthesized_count: int = Field(alias="synthesizedCount")
    accepted_count: int = Field(alias="acceptedCount")
    rejected_count: int = Field(alias="rejectedCount")
    revalidated_count: int = Field(alias="revalidatedCount")
    newly_failed_count: int = Field(alias="newlyFailedCount")
    rejected_reasons: list[str] = Field(default_factory=list, alias="rejectedReasons")

    model_config = {"populate_by_name": True}


class InsightResponse(BaseModel):
    """Wire shape of a single consolidated insight."""

    id: UUID
    theme: ConsolidationTheme
    kind: InsightKind
    narrative: str
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_tier: ConfidenceTier = Field(alias="confidenceTier")
    status: InsightStatus
    supporting_experience_ids: list[UUID] = Field(alias="supportingExperienceIds")
    synthesized_at: datetime = Field(alias="synthesizedAt")

    model_config = {"populate_by_name": True}


class InsightListResponse(BaseModel):
    """Response body for ``GET /v1/memory/insights``."""

    insights: list[InsightResponse]
    total: int
    theme: ConsolidationTheme | None = None
    include_stale: bool = Field(alias="includeStale")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# MET-471 — knowledge-backed convenience endpoints
# ---------------------------------------------------------------------------


class MemorySearchRequest(BaseModel):
    """Request body for ``POST /v1/memory/search`` (design-rationale search)."""

    query: str = Field(
        min_length=1,
        description="Natural-language query against design-decision knowledge.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=MAX_RETRIEVAL_LIMIT,
        description="Maximum number of hits to return.",
    )
    project_id: UUID | None = Field(
        default=None,
        alias="projectId",
        description="Optional project scope.",
    )

    model_config = {"populate_by_name": True}


class KnowledgeHitResponse(BaseModel):
    """Wire shape of a single knowledge-base hit.

    Used by ``POST /v1/memory/search`` (design rationale) and
    ``GET /v1/memory/components/{name}`` (component context). Fields
    mirror ``digital_twin.knowledge.service.SearchHit``.
    """

    content: str
    similarity_score: float = Field(alias="similarityScore")
    source_path: str | None = Field(default=None, alias="sourcePath")
    heading: str | None = None
    chunk_index: int | None = Field(default=None, alias="chunkIndex")
    total_chunks: int | None = Field(default=None, alias="totalChunks")
    knowledge_type: str | None = Field(default=None, alias="knowledgeType")
    source_work_product_id: UUID | None = Field(default=None, alias="sourceWorkProductId")

    model_config = {"populate_by_name": True}


class MemorySearchResponse(BaseModel):
    """Response body for ``POST /v1/memory/search`` and the components GET."""

    hits: list[KnowledgeHitResponse]
    query: str
    total_found: int = Field(alias="totalFound")

    model_config = {"populate_by_name": True}
