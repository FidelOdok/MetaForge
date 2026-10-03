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

    decision: Literal["approve", "reject", "retry", "rework"]
    #: Why, for a ``retry`` (FORGE-495): given to the phase brain, with the
    #: gate's findings, as the first thing in its prompt. Optional elsewhere.
    reason: str = Field(default="", max_length=2000)
    #: For a ``rework`` (FORGE-500): the id of an earlier phase of this run to
    #: go back to. The phase and every later one re-run and re-open their gates.
    to_phase: str = Field(default="", max_length=200)


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
    #: Which project this run belongs to, promoted out of ``request``.
    #:
    #: It was only ever inside the request blob, so `/runs` and the approvals
    #: queue listed every project's work together and a client had to dig
    #: through an untyped dict to tell them apart. ``None`` is a real answer
    #: and not a gap: a run can genuinely have no project (12 of 17 on the
    #: dev gateway carried one), and those must stay visible rather than
    #: disappear the moment a project is selected.
    project_id: str | None = None

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
            project_id=project_of(run.request),
        )


def project_of(request: dict[str, Any]) -> str | None:
    """The project a run belongs to, wherever the caller put it.

    Two shapes in practice: a design-flow run carries ``project_id`` at the
    top of its request, and a held tool call carries the tool's own
    ``arguments.project_id``. Both are the same fact, so both resolve here
    rather than at each call site.
    """
    direct = request.get("project_id")
    if isinstance(direct, str) and direct:
        return direct
    arguments = request.get("arguments")
    if isinstance(arguments, dict):
        nested = arguments.get("project_id")
        if isinstance(nested, str) and nested:
            return nested
    return None


def filter_by_project(runs: list[Run], project_id: str | None) -> tuple[list[Run], int]:
    """Runs for one project, and how many carry no project at all.

    The count is returned rather than folded in, because the alternative --
    silently dropping unscoped runs once a project is selected -- is the
    failure this codebase keeps producing: something disappears and the
    absence reads as "there are none" rather than "these are hidden".
    """
    if not project_id:
        return runs, 0
    matching = [r for r in runs if project_of(r.request) == project_id]
    unscoped = sum(1 for r in runs if project_of(r.request) is None)
    return matching, unscoped


class RunListResponse(BaseModel):
    """Body for ``GET /v1/runs``."""

    runs: list[RunResponse]
    #: How many runs were left out because they carry no project, when the
    #: list was scoped to one. Zero on an unscoped listing. The dashboard
    #: says so rather than letting them vanish.
    unscoped_count: int = 0
