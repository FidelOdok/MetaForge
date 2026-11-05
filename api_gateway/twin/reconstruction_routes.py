"""Parametric reconstruction routes (spec section 4B, 3D-RC-002).

* ``POST /v1/twin/projects/{project_id}/reconstruction`` proposes a feature
  history for each made component of the accepted concept (``409`` when no
  concept is accepted yet).
* ``GET /v1/twin/projects/{project_id}/reconstruction`` returns the latest
  proposal per component, with the plain-language summary Create shows.
* ``POST /v1/twin/projects/{project_id}/reconstruction/{key}/accept`` accepts
  features of one proposal (``FHP-...``), with edits and answers; accepted
  bodies become parametric sketches held for approval.

The work is :mod:`api_gateway.twin.reconstruction`.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.twin.reconstruction import (
    ReconstructionAcceptRequest,
    ReconstructionAcceptResponse,
    ReconstructionError,
    ReconstructionProposeRequest,
    ReconstructionProposeResponse,
    ReconstructionView,
    accept_proposal,
    propose,
    reconstruction_view,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.reconstruction_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    twin = get_twin()
    if twin is None:
        raise HTTPException(status_code=503, detail="the twin is not configured on this gateway")
    return twin


@router.post("/projects/{project_id}/reconstruction")
async def post_reconstruction(
    project_id: str, body: ReconstructionProposeRequest | None = None
) -> ReconstructionProposeResponse:
    """Propose an editable feature history for the accepted concept's components."""
    twin = _twin()
    with tracer.start_as_current_span("twin.reconstruction.route_propose") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await propose(twin, project_id, body or ReconstructionProposeRequest())
        except ReconstructionError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/projects/{project_id}/reconstruction")
async def get_reconstruction(project_id: str) -> ReconstructionView:
    """The latest feature history proposal per component."""
    twin = _twin()
    with tracer.start_as_current_span("twin.reconstruction.route_view") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await reconstruction_view(twin, project_id)
        except ReconstructionError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/projects/{project_id}/reconstruction/{key}/accept")
async def post_accept(
    project_id: str, key: str, body: ReconstructionAcceptRequest
) -> ReconstructionAcceptResponse:
    """Accept (and edit) features of one proposal; bodies become parametric sketches."""
    from twin_core.items import ItemRevisionConflictError

    twin = _twin()
    with tracer.start_as_current_span("twin.reconstruction.route_accept") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("reconstruction.key", key)
        try:
            return await accept_proposal(twin, project_id, key, body)
        except ItemRevisionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReconstructionError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
