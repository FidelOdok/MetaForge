"""L2 preview definition and parameter commit routes (C3D slice 5).

* ``GET /v1/twin/projects/{project_id}/parametric-sketches/{sketch_key}/preview-definition``
  is the shared feature definition the browser draws the L2 preview from,
  with the exact CAD status it swaps to (and, given ``job_id``, the state of
  the exact build the browser scheduled).
* ``POST /v1/twin/projects/{project_id}/parametric-sketches/{sketch_key}/parameters``
  commits a parameter edit (a drag handle release or the numeric field) as
  the sketch's next revision and schedules one exact build. ``409`` when
  ``base_revision`` is no longer the head (or a rule no longer holds);
  ``422`` for a value or dimension that cannot be set.

The work is :mod:`api_gateway.twin.parametric_preview`.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.twin.change_classification import ChangeGuardRefusedError
from api_gateway.twin.parametric_preview import (
    ParameterCommitRequest,
    ParametricPreviewError,
    commit_parameters,
    preview_definition,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.parametric_preview_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


def _deps() -> tuple[Any, Any]:
    from api_gateway.twin.exact_cad import get_exact_cad_service
    from api_gateway.twin.routes import get_twin

    exact = get_exact_cad_service()
    twin = get_twin()
    if exact is None or twin is None:
        raise HTTPException(status_code=503, detail="exact CAD is not configured on this gateway")
    return twin, exact


@router.get("/projects/{project_id}/parametric-sketches/{sketch_key}/preview-definition")
async def get_preview_definition(
    project_id: str,
    sketch_key: str,
    feature_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """The L2 preview definition of a parametric sketch's feature, and its exact status."""
    twin, exact = _deps()
    with tracer.start_as_current_span("twin.parametric_preview.route_definition") as span:
        span.set_attribute("sketch.key", sketch_key)
        try:
            return await preview_definition(
                twin,
                exact,
                project_id=project_id,
                sketch_key=sketch_key,
                feature_id=feature_id,
                job_id=job_id,
            )
        except ParametricPreviewError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/projects/{project_id}/parametric-sketches/{sketch_key}/parameters", status_code=202)
async def post_parameters(
    project_id: str, sketch_key: str, body: ParameterCommitRequest
) -> dict[str, Any]:
    """Commit a parameter edit as a new parametric revision and schedule the exact build."""
    from twin_core.items import ItemRevisionConflictError

    twin, exact = _deps()
    with tracer.start_as_current_span("twin.parametric_preview.route_commit") as span:
        span.set_attribute("sketch.key", sketch_key)
        try:
            return await commit_parameters(
                twin, exact, project_id=project_id, sketch_key=sketch_key, request=body
            )
        except ItemRevisionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ParametricPreviewError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ChangeGuardRefusedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
