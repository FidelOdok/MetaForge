"""Cancel and retry for operations (FORGE-788, DI-003, RT-024, RT-028).

The routes in :mod:`api_gateway.operations.routes` call these. Each refusal
is an ``HTTPException`` carrying a plain reason: 404 for an operation the
project does not hold, 409 for one that cannot be cancelled or retried, 503
when the process that runs it cannot be reached.

What each kind supports:

* **Run, plain** (``run:<id>``): cancel through the run state machine.
* **Run, design flow**: cancel stops the workflow (Temporal) or the
  in-process executor at its next step, then marks the run cancelled. The
  run's open drafts are closed as on any ending; what it already committed to
  the twin stays committed. A failed design flow can be retried: the same
  request starts again as a new run, which names the old one in ``retry_of``.
* **Job** (``job:<id>``): cancel stops the job in this gateway, or relays to
  the MCP sidecar when the sidecar runs it. Retry runs a failed job of a tool
  that registered a retry, with the same arguments, as a new job.
* **Chat turn** (``chat:<id>``): neither; send the message again.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import HTTPException

from api_gateway.operations import sidecar
from api_gateway.operations.publisher import (
    JOB_TERMINAL_SEQUENCE,
    get_operation_publisher,
    terminal_event_id,
)
from api_gateway.operations.schemas import (
    OperationKind,
    OperationSnapshot,
    OperationState,
    is_terminal,
    kind_of,
)
from observability.metrics import MetricsCollector
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.scheduling")

__all__ = ["cancel_operation", "retry_operation"]

CANCEL_REASON = "cancelled from the operations view"

#: Request keys the gateway sets on a run as it starts one; a retry starts
#: afresh and lets ``create_run`` set them again.
_RUNTIME_KEYS = frozenset({"flow_engine", "retry_of"})


def _metrics() -> MetricsCollector:
    from api_gateway.operations import publisher

    return publisher._metrics()  # noqa: SLF001 - the gateway's collector


def _count(kind: str, action: str, outcome: str) -> None:
    try:
        _metrics().record_operation_scheduling(kind, action, outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a request
        pass


def _snapshot(project_id: str, operation_id: str) -> OperationSnapshot:
    snapshot = get_operation_publisher().store.get(project_id, operation_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"operation '{operation_id}' not found")
    return snapshot


# ── cancel ──────────────────────────────────────────────────────────────


async def cancel_operation(project_id: str, operation_id: str) -> OperationState:
    """Cancel an operation that supports it. Returns the state it is now in."""
    kind = str(kind_of(operation_id) or "unknown")
    with tracer.start_as_current_span("operations.cancel") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("operation_id", operation_id)
        try:
            snapshot = _snapshot(project_id, operation_id)
            if is_terminal(snapshot.state):
                raise HTTPException(
                    status_code=409,
                    detail=f"operation '{operation_id}' is already {snapshot.state}",
                )
            native = operation_id.partition(":")[2]
            if snapshot.kind is OperationKind.RUN:
                await _cancel_run(native)
            elif snapshot.kind is OperationKind.JOB:
                await _cancel_job(project_id, snapshot, native)
            else:
                raise HTTPException(
                    status_code=409, detail="a chat turn cannot be cancelled from here"
                )
        except HTTPException as exc:
            span.set_attribute("outcome", exc.status_code)
            _count(kind, "cancel", "refused" if exc.status_code < 500 else "error")
            raise
    _count(kind, "cancel", "ok")
    logger.info("operation_cancelled", project_id=project_id, operation_id=operation_id)
    return OperationState.CANCELLED


async def _cancel_run(run_id: str) -> None:
    from api_gateway.runs import routes as run_routes
    from api_gateway.runs.engine import FlowEngine
    from orchestrator.design_flow.launcher import WorkflowNotFoundError
    from orchestrator.harness.runs import InvalidTransition, RunNotFoundError

    store = run_routes.get_run_store()
    try:
        run = store.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=409, detail="the run is not held by this gateway") from exc
    if run.is_terminal:
        raise HTTPException(status_code=409, detail=f"the run is already {run.status.value}")
    if run_routes.is_design_flow_run(run) and (
        run.request.get("flow_engine") == FlowEngine.TEMPORAL.value
    ):
        # Stop the workflow first: a record marked cancelled while its
        # workflow carries on would be a run that still spends.
        try:
            launcher = await run_routes.get_flow_launcher()
            await launcher.cancel(run_id, reason=CANCEL_REASON)
        except WorkflowNotFoundError:
            logger.info("design_flow_cancel_workflow_missing", run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - the engine is down or the RPC failed
            logger.warning("design_flow_cancel_failed", run_id=run_id, error=str(exc))
            raise HTTPException(
                status_code=503,
                detail=f"the workflow engine could not be reached to stop the run ({exc})",
            ) from exc
    try:
        store.cancel(run_id, reason=CANCEL_REASON)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _cancel_job(project_id: str, snapshot: OperationSnapshot, job_id: str) -> None:
    from tool_registry.jobs import active_job_runner

    runner = active_job_runner()
    if runner is not None and runner.owns(job_id):
        job = runner.cancel(job_id, reason=CANCEL_REASON)
        if job is None or job.status != "cancelled":
            raise HTTPException(status_code=409, detail="the job could not be cancelled")
        return
    relayed = await sidecar.relay_job_action(job_id, "cancel")
    if relayed is None:
        raise HTTPException(
            status_code=409,
            detail="the job is not running in this gateway and no MCP sidecar is configured",
        )
    if not relayed.reached:
        raise HTTPException(status_code=503, detail=str(relayed.body.get("detail")))
    if relayed.status == 404:
        raise HTTPException(
            status_code=409, detail="the job is not running in this gateway or the MCP sidecar"
        )
    if relayed.status >= 400 or relayed.body.get("status") != "cancelled":
        raise HTTPException(
            status_code=409,
            detail=str(relayed.body.get("detail") or "the MCP sidecar could not cancel the job"),
        )
    # The sidecar posts its own `cancelled` event; this one has the same id
    # and sequence, so whichever arrives second is a duplicate.
    get_operation_publisher().publish(
        project_id,
        snapshot.operation_id,
        "cancelled",
        "cancelled",
        event_id=terminal_event_id(snapshot.operation_id, "cancelled"),
        sequence=max(JOB_TERMINAL_SEQUENCE, snapshot.last_sequence + 1),
        title=snapshot.title,
        domain=snapshot.domain.value,
        message="Cancelled",
    )


# ── retry ───────────────────────────────────────────────────────────────


async def retry_operation(project_id: str, operation_id: str) -> str:
    """Run a failed operation again. Returns the new operation's id."""
    kind = str(kind_of(operation_id) or "unknown")
    with tracer.start_as_current_span("operations.retry") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("operation_id", operation_id)
        try:
            snapshot = _snapshot(project_id, operation_id)
            if snapshot.state is not OperationState.FAILED:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"operation '{operation_id}' is {snapshot.state}; only a failed "
                        "operation can be retried"
                    ),
                )
            native = operation_id.partition(":")[2]
            if snapshot.kind is OperationKind.RUN:
                new_id = await _retry_run(project_id, native)
            elif snapshot.kind is OperationKind.JOB:
                new_id = await _retry_job(native)
            else:
                raise HTTPException(
                    status_code=409, detail="a chat turn is retried by sending it again"
                )
        except HTTPException as exc:
            span.set_attribute("outcome", exc.status_code)
            _count(kind, "retry", "refused" if exc.status_code < 500 else "error")
            raise
        span.set_attribute("new_operation_id", new_id)
    _count(kind, "retry", "ok")
    logger.info("operation_retried", project_id=project_id, operation_id=operation_id, retry=new_id)
    return new_id


def run_retryable(run: Any) -> bool:
    """A run can be re-run when it is a design flow that failed.

    Re-running it is safe: the new run drafts into a change set of its own,
    and the failed run's drafts were closed when it failed. A plain run (a
    chat harness turn or a held tool call) has no request that could start it
    again safely, so it is never offered.
    """
    from api_gateway.runs.routes import is_design_flow_run
    from orchestrator.harness.runs import RunStatus

    return bool(run.status is RunStatus.FAILED and is_design_flow_run(run))


async def _retry_run(project_id: str, run_id: str) -> str:
    from api_gateway.runs import routes as run_routes
    from api_gateway.runs.schemas import CreateRunRequest
    from orchestrator.harness.runs import RunNotFoundError

    try:
        run = run_routes.get_run_store().get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=409, detail="the run is not held by this gateway") from exc
    if not run_retryable(run):
        raise HTTPException(
            status_code=409, detail="only a failed design-flow run can be retried from here"
        )
    request = {k: v for k, v in run.request.items() if k not in _RUNTIME_KEYS}
    request["project_id"] = request.get("project_id") or project_id
    request["retry_of"] = run_id
    created = await run_routes.create_run(CreateRunRequest(request=request, start=True))
    return f"run:{created.id}"


async def _retry_job(job_id: str) -> str:
    from tool_registry.jobs import JobNotRetryableError, active_job_runner

    runner = active_job_runner()
    if runner is not None and runner.get(job_id) is not None:
        try:
            job = await runner.retry(job_id)
        except JobNotRetryableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return f"job:{job.job_id}"
    relayed = await sidecar.relay_job_action(job_id, "retry")
    if relayed is None:
        raise HTTPException(
            status_code=409,
            detail="the job did not run in this gateway and no MCP sidecar is configured",
        )
    if not relayed.reached:
        raise HTTPException(status_code=503, detail=str(relayed.body.get("detail")))
    new_id = relayed.body.get("job_id")
    if relayed.status >= 400 or not isinstance(new_id, str) or not new_id:
        raise HTTPException(
            status_code=409,
            detail=str(relayed.body.get("detail") or "the MCP sidecar could not retry the job"),
        )
    return f"job:{new_id}"
