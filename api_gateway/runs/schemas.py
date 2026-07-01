"""Request/response schemas for the Runs API (MET-547, Phase 1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from orchestrator.harness.runs import Run


class CreateRunRequest(BaseModel):
    """Body for ``POST /v1/runs``."""

    request: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque run input (goal, spec, config) handed to the harness.",
    )
    start: bool = Field(
        default=True,
        description="Transition queued -> running immediately after creation.",
    )


class ApprovalRequest(BaseModel):
    """Body for ``POST /v1/runs/{id}/approval``."""

    decision: Literal["approve", "reject"]


class RunResponse(BaseModel):
    """Serialized run state."""

    id: str
    status: str
    request: dict[str, Any]
    created_at: float
    updated_at: float
    error: str | None = None
    approval_reason: str | None = None
    result: dict[str, Any] | None = None
    history: list[str]

    @classmethod
    def from_run(cls, run: Run) -> RunResponse:
        return cls(
            id=run.id,
            status=str(run.status),
            request=run.request,
            created_at=run.created_at,
            updated_at=run.updated_at,
            error=run.error,
            approval_reason=run.approval_reason,
            result=run.result,
            history=[str(s) for s in run.history],
        )


class RunListResponse(BaseModel):
    """Body for ``GET /v1/runs``."""

    runs: list[RunResponse]
