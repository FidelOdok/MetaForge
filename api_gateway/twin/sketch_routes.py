"""Freehand sketch routes for the Create stage (FORGE-789).

``POST /v1/twin/projects/{project_id}/sketches`` saves the whole sketch as
the next revision of the project's ``sketch_strokes`` item and reports it as
a ``sketch`` operation. ``GET .../sketches/current`` returns the head revision
with its strokes, so another device or the Engineering view sees the same
sketch. The logic lives in :mod:`api_gateway.twin.sketch_strokes_recorder`.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from api_gateway.twin.sketch_strokes_recorder import (
    SketchRevisionResponse,
    SketchSaveRequest,
    SketchSaveResponse,
    current_sketch,
    make_sketch_strokes_recorder,
    save_sketch_with_operation,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _project_backend() -> Any:
    try:
        from api_gateway.projects.routes import get_project_backend

        return get_project_backend()
    except Exception as exc:  # noqa: BLE001 - the link is best-effort
        logger.info("sketch_project_backend_unavailable", error=str(exc))
        return None


def _check_project_id(project_id: str) -> None:
    from uuid import UUID

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None


@router.post("/projects/{project_id}/sketches", response_model=SketchSaveResponse)
async def save_sketch(project_id: str, body: SketchSaveRequest) -> SketchSaveResponse:
    """Save a person's freehand sketch as the next revision of the project's sketch.

    The strokes are stored as JSON with a generated SVG. The revision is a
    human sketch, not CAD: its scale is unknown and its plane is unassigned.
    A retried ``save_id`` returns the revision it already made, with
    ``duplicate`` set, and adds nothing. The save is reported on the project's
    operation stream as a ``sketch`` operation.
    """
    _check_project_id(project_id)
    with tracer.start_as_current_span("twin.sketch_routes.save") as span:
        span.set_attribute("project_id", project_id)
        save = make_sketch_strokes_recorder(_twin(), _project_backend())
        try:
            return await save_sketch_with_operation(save, project_id, body)
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/sketches/current", response_model=SketchRevisionResponse)
async def get_current_sketch(
    project_id: str,
    item_key: str | None = Query(None, description="The sketch item; the newest when left out"),
) -> SketchRevisionResponse:
    """The current revision of the project's sketch, with its strokes. 404 when there is none."""
    _check_project_id(project_id)
    with tracer.start_as_current_span("twin.sketch_routes.current") as span:
        span.set_attribute("project_id", project_id)
        try:
            found = await current_sketch(_twin(), project_id, item_key)
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("sketch_current_failed", project_id=project_id, error=str(exc))
            raise HTTPException(status_code=502, detail="The sketch could not be read") from exc
        if found is None:
            raise HTTPException(status_code=404, detail="This project has no saved sketch yet")
        return found
