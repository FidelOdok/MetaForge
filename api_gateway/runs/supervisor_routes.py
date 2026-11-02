"""``/v1/runs/{run_id}/supervise`` and ``/supervision`` (FORGE-735, FORGE-739).

Starting supervision is not an approval: the supervisor never decides a gate
itself (the project's approval policy and the reviewer do, or a person), so a
client may start it. It can restart runs, which is why it carries budgets and
stops for a person on anything it cannot fix.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.runs import supervisor
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.supervisor_routes")

router = APIRouter(prefix="/v1/runs", tags=["runs"])


class SuperviseRequest(BaseModel):
    """Budgets; any left out take the defaults."""

    max_actions: int | None = Field(default=None, ge=1, le=200)
    max_restarts: int | None = Field(default=None, ge=0, le=20)
    max_model_calls: int | None = Field(default=None, ge=1, le=20000)
    max_hours: float | None = Field(default=None, gt=0, le=168)


class StopSupervisionRequest(BaseModel):
    reason: str = "stopped by a person"


def _get(run_id: str) -> Any:
    from orchestrator.harness.runs import RunNotFoundError

    try:
        return supervisor._store().get(run_id)  # noqa: SLF001
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc


@router.post("/{run_id}/supervise")
async def supervise_run(run_id: str, body: SuperviseRequest, request: Request) -> dict[str, Any]:
    """Start supervising a run until it is verified, a budget is used or a person is needed."""
    from api_gateway.approvals.routes import surface_from_request

    run = _get(run_id)
    if run.is_terminal and run.status.value != "failed":
        raise HTTPException(
            status_code=409, detail=f"run '{run_id}' already ended as {run.status.value}"
        )
    surface, on_behalf = surface_from_request(request)
    with tracer.start_as_current_span("run_supervisor.start") as span:
        span.set_attribute("run.id", run_id)
        sup = supervisor.start_supervision(
            run_id,
            budgets=body.model_dump(exclude_none=True),
            started_by=on_behalf or surface,
        )
    # Act once now, so a run that already failed or completed is handled at once.
    await supervisor.supervise_tick(run_id)
    return supervisor.supervision_of(supervisor._store().get(run_id)) or sup  # noqa: SLF001


@router.get("/{run_id}/supervision")
async def get_supervision(run_id: str) -> dict[str, Any]:
    """The supervision record: status, budgets, counters, chain of runs and its log."""
    sup = supervisor.supervision_of(_get(run_id))
    if sup is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' is not supervised")
    return sup


@router.post("/{run_id}/supervise/stop")
async def stop_supervision(run_id: str, body: StopSupervisionRequest) -> dict[str, Any]:
    _get(run_id)
    sup = supervisor.stop_supervision(run_id, body.reason)
    if sup is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' is not supervised")
    return sup
