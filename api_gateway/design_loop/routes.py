"""Closed design loop API (FORGE-287, gap G-G1).

``POST /v1/design-loop/start`` runs the closed loop (propose -> build ->
simulate -> evaluate against constraints -> revise -> repeat, until pass or
proven infeasible) and persists every candidate as a real, queryable
``DesignLoopIteration`` -- see ``api_gateway.twin.design_loop`` for the full
design rationale (composes the already-shipped FORGE-320 optimizer rather
than reimplementing the search).

``GET /v1/design-loop/{loop_id}`` is the dashboard's "iteration timeline".

``POST /v1/design-loop/{loop_id}/approve`` records a human's approval of the
converged winner -- the gate-approval half of this ticket's own yardstick
line ("a human approving at gates").
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.design_loop.routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin --
    used by the read/approve routes, which need no other dependency."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("design_loop_twin_initialized", twin_type=type(twin).__name__)


# An injected async ``start(...)`` (built in server.py over
# ``api_gateway.twin.design_loop.make_design_loop_starter``, composed with
# the SAME optimizer instance ``twin.optimize_parameter`` uses so Evidence/
# Decision recording isn't duplicated). None until the server lifespan
# wires it in -- same seam as ``twin/routes.py``'s ``_design_sketch_approver``.
_design_loop_starter: Any = None


def init_design_loop_starter(starter: Any) -> None:
    """Wire in the design-loop start callable (server lifespan)."""
    global _design_loop_starter  # noqa: PLW0603
    _design_loop_starter = starter


router = APIRouter(prefix="/v1/design-loop", tags=["design-loop"])


class StartDesignLoopRequest(BaseModel):
    workProductId: str  # noqa: N815 -- dashboard contract is camelCase
    loadN: float  # noqa: N815
    deflectionLimitMm: float  # noqa: N815
    sfLimit: float = 2.0  # noqa: N815
    material: str = "aluminum_6061"
    wallMinMm: float = 0.5  # noqa: N815
    wallMaxMm: float | None = None  # noqa: N815
    projectId: str | None = None  # noqa: N815
    requirementIds: list[str] | None = None  # noqa: N815
    maxIterations: int = 60  # noqa: N815


@router.post("/start")
async def start_design_loop(payload: StartDesignLoopRequest) -> dict[str, Any]:
    if _design_loop_starter is None:
        raise HTTPException(status_code=503, detail="design loop is not available")
    with tracer.start_as_current_span("design_loop.start") as span:
        span.set_attribute("design_loop.work_product_id", payload.workProductId)
        try:
            result = await _design_loop_starter(
                work_product_id=payload.workProductId,
                load_n=payload.loadN,
                deflection_limit_mm=payload.deflectionLimitMm,
                sf_limit=payload.sfLimit,
                material=payload.material,
                wall_min_mm=payload.wallMinMm,
                wall_max_mm=payload.wallMaxMm,
                project_id=payload.projectId,
                requirement_ids=payload.requirementIds,
                max_iterations=payload.maxIterations,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "design_loop_started_via_dashboard",
        work_product_id=payload.workProductId,
        loop_id=result.get("loop_id"),
        status=result.get("status"),
    )
    return result


@router.get("/{loop_id}")
async def get_design_loop(loop_id: str) -> dict[str, Any]:
    from api_gateway.twin.design_loop import make_design_loop_reader

    read = make_design_loop_reader(_twin)
    try:
        return await read(loop_id=loop_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ApproveDesignLoopRequest(BaseModel):
    approvedBy: str  # noqa: N815 -- dashboard contract is camelCase


@router.post("/{loop_id}/approve")
async def approve_design_loop(loop_id: str, payload: ApproveDesignLoopRequest) -> dict[str, Any]:
    return await approve_loop(loop_id, payload.approvedBy)


async def approve_loop(
    loop_id: str, approved_by: str, audit: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Approve a loop's winner. Shared with ``/v1/approvals`` (FORGE-507)."""
    from api_gateway.twin.design_loop import make_design_loop_approver

    approve = make_design_loop_approver(_twin)
    try:
        return await approve(loop_id=loop_id, approved_by=approved_by, audit=audit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
