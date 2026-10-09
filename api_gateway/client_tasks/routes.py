"""``/v1/client-tasks``: phases a connected client does itself (FORGE-581).

The design-flow worker posts a task here for each phase of a client-mode run
and waits for it; the client claims it (getting the full brief), does the
work through MCP tools, and submits a summary. The store is the gateway's, so
the worker, the MCP sidecar and the dashboard all see the same queue.

A task can only be opened for a run that exists and was started in client
mode. Anything else would be a task no workflow is waiting on, and a client
would do work nobody reads.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from observability.metrics import MetricsCollector, collector_for
from observability.tracing import get_tracer
from orchestrator.design_flow.client_tasks import (
    ClientTaskStore,
    Intelligence,
    PhaseTask,
    SqliteClientTaskStore,
    TaskNotFoundError,
    TaskStateError,
    task_id_for,
)
from orchestrator.harness.runs import RunNotFoundError

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.client_tasks.routes")

router = APIRouter(prefix="/v1/client-tasks", tags=["client-tasks"])

AGENT_HEADER = "X-MetaForge-Agent"

_store: ClientTaskStore | None = None
_metrics: MetricsCollector | None = None


def get_client_task_store() -> ClientTaskStore:
    """The process's store; an in-memory one until the lifespan installs the file."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = SqliteClientTaskStore()
    return _store


def init_client_task_store(store: ClientTaskStore | None) -> None:
    """Install ``store`` (the lifespan), or ``None`` to start over (tests)."""
    global _store  # noqa: PLW0603
    _store = store


def _collector() -> MetricsCollector:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        _metrics = collector_for("metaforge-gateway")
    return _metrics


def set_metrics(metrics: MetricsCollector | None) -> None:
    global _metrics  # noqa: PLW0603
    _metrics = metrics


class OpenTaskRequest(BaseModel):
    run_id: str = Field(alias="runId")
    phase_id: str = Field(alias="phaseId")
    attempt: int = 1
    project_id: str | None = Field(default=None, alias="projectId")
    brief: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class ClaimRequest(BaseModel):
    client: str = ""


class SubmitRequest(BaseModel):
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    client: str = ""


class CancelRequest(BaseModel):
    reason: str = ""


def _client_of(request: Request, named: str) -> str:
    return (named or request.headers.get(AGENT_HEADER) or "").strip()[:100] or "unknown"


def _not_found(task_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"client task '{task_id}' not found")


def _summary_view(task: PhaseTask) -> dict[str, Any]:
    """A task without its brief, for listings."""
    return {
        "id": task.id,
        "run_id": task.run_id,
        "phase_id": task.phase_id,
        "attempt": task.attempt,
        "project_id": task.project_id,
        "status": task.status,
        "title": task.brief.get("title", ""),
        "claimed_by": task.claimed_by,
        "submitted_by": task.submitted_by,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _require_client_mode_run(run_id: str) -> None:
    from api_gateway.runs import routes as run_routes

    try:
        run = run_routes.get_run_store().get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    if run_routes.run_intelligence(run) is not Intelligence.CLIENT:
        raise HTTPException(
            status_code=409,
            detail=f"run '{run_id}' runs its phases on the server; it takes no client tasks",
        )


@router.post("", status_code=201)
async def open_task(body: OpenTaskRequest) -> dict[str, Any]:
    """Post a phase for the client. Idempotent on ``run:phase:attempt``."""
    _require_client_mode_run(body.run_id)
    with tracer.start_as_current_span("client_tasks.open") as span:
        span.set_attribute("run.id", body.run_id)
        span.set_attribute("phase.id", body.phase_id)
        task = get_client_task_store().open_task(
            PhaseTask(
                id=task_id_for(body.run_id, body.phase_id, body.attempt),
                run_id=body.run_id,
                phase_id=body.phase_id,
                attempt=body.attempt,
                project_id=body.project_id,
                brief=body.brief,
            )
        )
    _collector().record_client_task("opened")
    return task.as_dict()


@router.get("")
async def list_tasks(
    status: str | None = "open",
    project_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Tasks, open ones by default. ``status=all`` lists every state."""
    wanted = None if status in (None, "", "all") else status
    tasks = get_client_task_store().list_tasks(status=wanted, project_id=project_id, run_id=run_id)
    return {"tasks": [_summary_view(t) for t in tasks]}


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    try:
        return get_client_task_store().get(task_id).as_dict()
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc


@router.post("/{task_id}/claim")
async def claim_task(task_id: str, body: ClaimRequest, request: Request) -> dict[str, Any]:
    """Take a task and get its full brief."""
    client = _client_of(request, body.client)
    try:
        task = get_client_task_store().claim(task_id, client)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _collector().record_client_task("claimed")
    logger.info("client_task_claimed", task_id=task_id, client=client)
    return task.as_dict()


@router.post("/{task_id}/submit")
async def submit_task(task_id: str, body: SubmitRequest, request: Request) -> dict[str, Any]:
    """Hand the phase back. The run's gate then checks the twin as usual."""
    client = _client_of(request, body.client)
    try:
        task = get_client_task_store().submit(task_id, client, body.summary, body.artifacts)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _collector().record_client_task("submitted")
    logger.info("client_task_submitted", task_id=task_id, client=client)
    return task.as_dict()


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, body: CancelRequest) -> dict[str, Any]:
    """Withdraw a task nobody will wait on any more (the worker's activity ended)."""
    try:
        task = get_client_task_store().cancel(task_id, body.reason)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    _collector().record_client_task("cancelled")
    return task.as_dict()
