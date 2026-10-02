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


def _optional_str(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


class RunResponse(BaseModel):
    """Serialized run state."""

    id: str
    status: str
    request: dict[str, Any]
    created_at: float
    updated_at: float
    error: str | None = None
    approval_reason: str | None = None
    #: Wall-clock time after which a held approval is expired as
    #: ``timed_out`` if nobody closed it first (FORGE-466).
    approval_deadline: float | None = None
    #: Who answered the approval, and whether that identity was verified
    #: (FORGE-393). Exposed so a remote approval gate can read the approver
    #: back out of the ledger rather than inventing one (FORGE-406).
    approved_by: str | None = None
    approver_verified: bool = False
    result: dict[str, Any] | None = None
    history: list[str]
    #: Which design-flow engine drives this run, ``temporal`` or
    #: ``in_process`` (FORGE-474). ``None`` for a plain run.
    engine: str | None = None
    #: The flow version a design-flow run is pinned to (FORGE-474): the
    #: stored version id when it was started from one, and the content hash
    #: of what it actually runs, on either engine.
    flow_version_id: str | None = None
    flow_content_hash: str | None = None
    #: Token and cost totals for the run, overall and per phase, role and model
    #: (FORGE-476). ``None`` when no model call has been recorded for it.
    usage: dict[str, Any] | None = None

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
            approval_deadline=run.approval_deadline,
            approved_by=run.approved_by,
            approver_verified=run.approver_verified,
            result=run.result,
            history=[str(s) for s in run.history],
            engine=_optional_str(run.request.get("flow_engine")),
            flow_version_id=_optional_str(run.request.get("flow_version_id")),
            flow_content_hash=_optional_str(run.request.get("flow_content_hash")),
        )


class RunListResponse(BaseModel):
    """Body for ``GET /v1/runs``."""

    runs: list[RunResponse]
