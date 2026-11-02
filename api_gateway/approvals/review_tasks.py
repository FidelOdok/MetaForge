"""Review tasks: a gate review done by a connected client's reviewer (FORGE-730).

When a project's approval policy says ``reviewer: client`` (or
``client_then_server``), the review step does not run the reviewer itself. It
opens a **review task** carrying the review brief and the model the policy asks
for, and waits. A client's reviewer subagent (``metaforge-gate-reviewer`` in
the plugin) lists open tasks, claims one, reviews from the brief with the
read-only tools, and submits its review, which is recorded on the gate exactly
like the server reviewer's (``source: client``).

Independence: the server cannot tell a subagent from the session that ran it,
so a review submitted by the same client that did the phase (a client-mode
phase task claimed under the same client name) is marked ``same_client`` and is
advice only; the policy never acts on it.

Tasks live in memory: the review itself is stored on the run, and a task lost
to a gateway restart simply times out at the worker, which then falls back as
the policy says.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.approvals.review_tasks")

router = APIRouter(prefix="/v1/review-tasks", tags=["approvals"])

Status = Literal["open", "claimed", "submitted", "cancelled"]


def task_id_for(run_id: str, phase: str, attempt: int | None) -> str:
    return f"review:{run_id}:{phase}:{attempt or 1}"


class ReviewTask(BaseModel):
    id: str
    run_id: str
    project_id: str | None = None
    phase: str
    attempt: int | None = None
    gate: str = ""
    status: Status = "open"
    brief: dict[str, Any] = Field(default_factory=dict)
    #: The model the policy asks the client reviewer to use.
    requested_model: str = ""
    created_at: str
    claimed_by: str | None = None
    claimed_at: str | None = None
    submitted_at: str | None = None
    cancel_reason: str | None = None


class ReviewTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, ReviewTask] = {}

    def open(self, task: ReviewTask) -> ReviewTask:
        existing = self._tasks.get(task.id)
        if existing is not None and existing.status in ("open", "claimed"):
            return existing
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> ReviewTask:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"review task '{task_id}' not found"
            ) from None

    def list(self, project_id: str | None = None, status: str | None = None) -> list[ReviewTask]:
        return [
            t
            for t in sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
            if (project_id is None or t.project_id == project_id)
            and (status is None or t.status == status)
        ]


_store: ReviewTaskStore | None = None


def get_review_task_store() -> ReviewTaskStore:
    global _store  # noqa: PLW0603
    if _store is None:
        _store = ReviewTaskStore()
    return _store


def reset_review_task_store() -> None:
    global _store  # noqa: PLW0603
    _store = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


class OpenReviewTask(BaseModel):
    run_id: str = Field(min_length=1)
    project_id: str | None = None
    phase: str = Field(min_length=1)
    attempt: int | None = None
    gate: str = ""
    brief: dict[str, Any] = Field(default_factory=dict)
    requested_model: str = ""


class ClaimReviewTask(BaseModel):
    client: str = Field(default="client", max_length=200)


class SubmitReview(BaseModel):
    client: str = Field(default="client", max_length=200)
    #: The model that actually reviewed (the subagent says which it ran on).
    model: str = Field(default="", max_length=200)
    recommendation: Literal["approve", "retry", "rework", "reject"]
    to_phase: str = ""
    reasons: str = Field(default="", max_length=4000)
    concerns: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CancelReviewTask(BaseModel):
    reason: str = ""


@router.post("", response_model=ReviewTask, status_code=201)
async def open_review_task(body: OpenReviewTask) -> ReviewTask:
    """Open a review task for a gate (the design-flow worker's review step)."""
    task = ReviewTask(
        id=task_id_for(body.run_id, body.phase, body.attempt),
        created_at=_now(),
        **body.model_dump(),
    )
    task = get_review_task_store().open(task)
    logger.info("review_task_opened", task_id=task.id, run_id=body.run_id, phase=body.phase)
    return task


@router.get("", response_model=list[ReviewTask])
async def list_review_tasks(
    project_id: str | None = None, status: Status | None = "open"
) -> list[ReviewTask]:
    """Review tasks, open ones by default, optionally for one project."""
    return get_review_task_store().list(project_id, status)


@router.get("/{task_id}", response_model=ReviewTask)
async def get_review_task(task_id: str) -> ReviewTask:
    return get_review_task_store().get(task_id)


@router.post("/{task_id}/claim", response_model=ReviewTask)
async def claim_review_task(task_id: str, body: ClaimReviewTask) -> ReviewTask:
    """Take a review task. Returns it with its brief."""
    task = get_review_task_store().get(task_id)
    if task.status == "claimed" and task.claimed_by == body.client:
        return task
    if task.status != "open":
        raise HTTPException(status_code=409, detail=f"review task '{task_id}' is {task.status}")
    task.status, task.claimed_by, task.claimed_at = "claimed", body.client, _now()
    logger.info("review_task_claimed", task_id=task_id, client=body.client)
    return task


def _did_the_phase(task: ReviewTask, client: str) -> bool:
    """Whether ``client`` claimed this gate's phase as a client-mode phase task."""
    try:
        from api_gateway.client_tasks.routes import get_client_task_store
        from orchestrator.design_flow.client_tasks import task_id_for as phase_task_id

        phase_task = get_client_task_store().get(
            phase_task_id(task.run_id, task.phase, task.attempt or 1)
        )
    except Exception:  # noqa: BLE001 - no phase task: the server ran the phase
        return False
    who = {getattr(phase_task, "claimed_by", None), getattr(phase_task, "submitted_by", None)}
    return client in who


@router.post("/{task_id}/submit")
async def submit_review(task_id: str, body: SubmitReview, request: Request) -> dict[str, Any]:
    """Record the client reviewer's review on the gate, then close the task."""
    from api_gateway.approvals import service
    from api_gateway.approvals.schemas import GateReview

    store = get_review_task_store()
    task = store.get(task_id)
    if task.status not in ("open", "claimed"):
        raise HTTPException(status_code=409, detail=f"review task '{task_id}' is {task.status}")
    if task.status == "claimed" and task.claimed_by not in (None, body.client):
        raise HTTPException(status_code=409, detail="another client claimed this review task")
    model = body.model.strip() or task.requested_model or "unknown"
    review = GateReview(
        phase=task.phase,
        attempt=task.attempt,
        gate=task.gate,
        recommendation=body.recommendation,
        to_phase=body.to_phase,
        reasons=body.reasons,
        concerns=[str(c)[:500] for c in body.concerns][:20],
        confidence=body.confidence,
        source="client",
        model=model,
        reviewer=f"reviewer:client:{model}",
        same_client=_did_the_phase(task, body.client),
    )
    with tracer.start_as_current_span("review_task.submit") as span:
        span.set_attribute("review_task.id", task_id)
        stored = await service.record_review(request.app, task.run_id, review)
    task.status, task.submitted_at = "submitted", _now()
    logger.info(
        "review_task_submitted",
        task_id=task_id,
        client=body.client,
        recommendation=body.recommendation,
        same_client=review.same_client,
        applied=stored.applied,
    )
    return {"task": task.model_dump(), "review": stored.model_dump()}


@router.post("/{task_id}/cancel", response_model=ReviewTask)
async def cancel_review_task(task_id: str, body: CancelReviewTask) -> ReviewTask:
    task = get_review_task_store().get(task_id)
    if task.status in ("open", "claimed"):
        task.status, task.cancel_reason = "cancelled", body.reason or "cancelled"
        logger.info("review_task_cancelled", task_id=task_id, reason=task.cancel_reason)
    return task
