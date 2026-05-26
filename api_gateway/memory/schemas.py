"""Pydantic request / response schemas for ``/v1/memory``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from digital_twin.memory.client import MAX_RETRIEVAL_LIMIT
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
