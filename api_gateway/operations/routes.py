"""Routes for project operations (FORGE-785, CDE slice 1).

* ``GET  /v1/projects/{project_id}/operations``: the snapshot and its cursor;
* ``GET  /v1/projects/{project_id}/operations/stream``: SSE, resumable from
  ``Last-Event-ID`` or ``?after=`` (a ``project_seq``);
* ``POST /v1/projects/{project_id}/operations/{operation_id}/cancel``;
* ``POST /v1/projects/{project_id}/operations/events``: ingest from a process
  with no stream of its own (the MCP sidecar, the design-flow worker).

Authentication is the gateway's ``AuthMiddleware`` like every other route;
per-project access control is FORGE-787.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from api_gateway.operations.hub import SSE_HEADERS
from api_gateway.operations.publisher import get_operation_publisher
from api_gateway.operations.schemas import (
    OperationCancelResponse,
    OperationEventIn,
    OperationIngestResponse,
    OperationKind,
    OperationListResponse,
    OperationState,
    is_terminal,
    kind_of,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.routes")

router = APIRouter(prefix="/v1/projects", tags=["operations"])

__all__ = ["publish_local", "router"]


async def publish_local(project_id: str, event: dict[str, Any]) -> None:
    """The operation event publisher for code inside the gateway process."""
    get_operation_publisher().publish_payload(project_id, event)


@router.get("/{project_id}/operations", response_model=OperationListResponse)
async def list_operations(
    project_id: str,
    active: bool = Query(default=False, description="Only operations not yet terminal"),
    limit: int = Query(default=50, ge=1, le=200),
) -> OperationListResponse:
    """The project's operations, most recently updated first, and the cursor.

    Open the stream with ``?after=<cursor>`` to receive every event after
    this snapshot.
    """
    store = get_operation_publisher().store
    with tracer.start_as_current_span("operations.list") as span:
        span.set_attribute("project_id", project_id)
        # The cursor is read first: an event stored between the two reads is
        # in the snapshot and replayed, never in neither.
        cursor = store.cursor(project_id)
        operations = store.list_operations(project_id, active=active, limit=limit)
        span.set_attribute("operations", len(operations))
    return OperationListResponse(project_id=project_id, cursor=cursor, operations=operations)


def _resume_point(last_event_id: str | None, after: int | None) -> int | None:
    if last_event_id is not None and last_event_id.strip().isdigit():
        return int(last_event_id.strip())
    return after


@router.get("/{project_id}/operations/stream")
async def stream_operations(
    project_id: str,
    after: int | None = Query(default=None, ge=0, description="Resume after this project_seq"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE of the project's operation events.

    Frames: ``operation`` (``id:`` is the ``project_seq``), ``heartbeat``
    every 15 s with the cursor, and ``resync`` when the resume point is
    unknown or older than the log keeps (refetch the snapshot).
    """
    resume = _resume_point(last_event_id, after)
    logger.info("operation_stream_opened", project_id=project_id, after=resume)
    return StreamingResponse(
        get_operation_publisher().hub.stream(project_id, resume),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post(
    "/{project_id}/operations/events",
    response_model=OperationIngestResponse,
    status_code=202,
    responses={200: {"model": OperationIngestResponse}, 409: {"description": "Refused"}},
)
async def ingest_operation_event(project_id: str, body: OperationEventIn) -> Any:
    """Accept an operation event from a process that has no stream of its own.

    202 with the stored ``project_seq``. A duplicate ``event_id`` is 200 with
    the seq stored the first time. An event out of order, or a non-terminal
    one after the operation ended, is 409.
    """
    if body.project_id is not None and body.project_id != project_id:
        raise HTTPException(status_code=422, detail="project_id does not match the path")
    result = get_operation_publisher().ingest(project_id, body)
    if result.status == "refused" or result.event is None:
        raise HTTPException(status_code=409, detail=result.reason or "event refused")
    out = OperationIngestResponse(
        event_id=result.event.event_id,
        project_seq=result.event.project_seq,
        sequence=result.event.sequence,
        duplicate=result.status == "duplicate",
    )
    if result.status == "duplicate":
        return JSONResponse(status_code=200, content=out.model_dump())
    return out


@router.post(
    "/{project_id}/operations/{operation_id}/cancel",
    response_model=OperationCancelResponse,
    status_code=202,
    responses={404: {"description": "Unknown"}, 409: {"description": "Not cancellable"}},
)
async def cancel_operation(project_id: str, operation_id: str) -> OperationCancelResponse:
    """Cancel an operation that supports it: 202, 409 when not cancellable, 404 when unknown."""
    store = get_operation_publisher().store
    snapshot = store.get(project_id, operation_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"operation '{operation_id}' not found")
    if is_terminal(snapshot.state):
        raise HTTPException(
            status_code=409, detail=f"operation '{operation_id}' is already {snapshot.state}"
        )
    native = operation_id.partition(":")[2]
    kind = kind_of(operation_id)
    with tracer.start_as_current_span("operations.cancel") as span:
        span.set_attribute("operation_id", operation_id)
        if kind is OperationKind.RUN:
            _cancel_run(native)
        elif kind is OperationKind.JOB:
            _cancel_job(native)
        else:
            raise HTTPException(status_code=409, detail="a chat turn cannot be cancelled from here")
    logger.info("operation_cancelled", project_id=project_id, operation_id=operation_id)
    return OperationCancelResponse(operation_id=operation_id, state=OperationState.CANCELLED)


def _cancel_run(run_id: str) -> None:
    from api_gateway.runs.routes import get_run_store, is_design_flow_run
    from orchestrator.harness.runs import InvalidTransition, RunNotFoundError

    store = get_run_store()
    try:
        run = store.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=409, detail="the run is not held by this gateway") from exc
    if is_design_flow_run(run):
        # Stopping a design flow means stopping its workflow, which has no
        # supported path yet. Its gates can reject it.
        raise HTTPException(
            status_code=409,
            detail="a design-flow run cannot be cancelled yet; reject it at its next gate",
        )
    try:
        store.cancel(run_id, reason="cancelled from the operations view")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _cancel_job(job_id: str) -> None:
    from tool_registry.jobs import active_job_runner

    runner = active_job_runner()
    if runner is None or not runner.owns(job_id):
        raise HTTPException(
            status_code=409,
            detail="the job is not running in this gateway (it may run in the MCP sidecar)",
        )
    job = runner.cancel(job_id)
    if job is None or job.status != "cancelled":
        raise HTTPException(status_code=409, detail="the job could not be cancelled")
