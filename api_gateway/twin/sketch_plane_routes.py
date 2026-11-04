"""Sketch plane routes (FORGE-790).

Under ``/v1/twin/projects/{project_id}/sketches/{sketch_key}/plane``:

- ``GET``: the sketch's current plane (``UNASSIGNED`` when none was set).
- ``POST .../propose``: a candidate plane in inference order. Writes nothing.
- ``POST .../confirm``: the person's explicit choice; a new plane revision.
- ``POST .../reassign/preview``: where the strokes would land. Writes nothing.
- ``POST .../reassign``: commit a reassign (a new plane revision, and a new
  sketch revision when the mode moves the strokes).
- ``POST .../undo``: restore the state before the last change, as a new revision.
- ``POST .../archive``: retire the plane, keeping its history.

The logic lives in :mod:`api_gateway.twin.sketch_planes`. Nothing here takes
the camera: the plane changes only through these explicit calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.twin.sketch_planes import (
    PlaneChangeResult,
    PlaneConfirmRequest,
    PlaneError,
    PlaneProposal,
    PlaneProposalRequest,
    PlaneReassignRequest,
    PlaneSimpleRequest,
    ReassignPreview,
    SketchPlaneView,
    archive_plane,
    confirm_plane,
    get_plane,
    preview_reassign,
    propose_plane,
    reassign_plane,
    undo_plane,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_plane_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

_BASE = "/projects/{project_id}/sketches/{sketch_key}/plane"

T = TypeVar("T")


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _check_project_id(project_id: str) -> None:
    from uuid import UUID

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None


async def _call(name: str, project_id: str, sketch_key: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run one plane call, mapping refusals to 404/409 and anything else to 502."""
    from twin_core.items import ItemError, ItemRevisionConflictError

    _check_project_id(project_id)
    with tracer.start_as_current_span(f"twin.sketch_plane_routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("sketch.key", sketch_key)
        try:
            return await fn()
        except PlaneError as exc:
            span.record_exception(exc)
            status = 404 if str(exc).startswith("no saved sketch") else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except ItemRevisionConflictError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ItemError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning(
                "sketch_plane_route_failed",
                route=name,
                project_id=project_id,
                sketch_key=sketch_key,
                error=str(exc),
            )
            raise HTTPException(
                status_code=502, detail="The sketch plane could not be read or changed"
            ) from exc


@router.get(_BASE, response_model=SketchPlaneView)
async def get_sketch_plane(project_id: str, sketch_key: str) -> SketchPlaneView:
    """The sketch's current plane. An ``UNASSIGNED`` plane when none was ever set."""
    return await _call(
        "get", project_id, sketch_key, lambda: get_plane(_twin(), project_id, sketch_key)
    )


@router.post(f"{_BASE}/propose", response_model=PlaneProposal)
async def propose_sketch_plane(
    project_id: str, sketch_key: str, body: PlaneProposalRequest
) -> PlaneProposal:
    """A candidate plane for the sketch. Writes nothing.

    Sources in order: the explicit selection, the plane of another sketch on
    the same component, the client's orthographic view hint, the component
    frame, then a heuristic (keywords, the drawing's shape). Each candidate
    says who proposed it and how confident it is; ``ambiguous`` is set when
    the top two disagree and are close, or the best guess is weak. A plane
    already confirmed for this sketch is returned as the candidate.
    """
    return await _call(
        "propose",
        project_id,
        sketch_key,
        lambda: propose_plane(_twin(), project_id, sketch_key, body),
    )


@router.post(f"{_BASE}/confirm", response_model=PlaneChangeResult)
async def confirm_sketch_plane(
    project_id: str, sketch_key: str, body: PlaneConfirmRequest
) -> PlaneChangeResult:
    """Place the sketch on the plane the person chose, as the plane item's next revision.

    The choice is applied as given; no inference is consulted. A retried
    ``op_id`` returns the revision it already made; choosing the plane the
    sketch is already on changes nothing.
    """
    return await _call(
        "confirm",
        project_id,
        sketch_key,
        lambda: confirm_plane(_twin(), project_id, sketch_key, body),
    )


@router.post(f"{_BASE}/reassign/preview", response_model=ReassignPreview)
async def preview_sketch_plane_reassign(
    project_id: str, sketch_key: str, body: PlaneReassignRequest
) -> ReassignPreview:
    """Where the strokes would land on the new plane in ``mode``. Writes nothing."""
    return await _call(
        "reassign_preview",
        project_id,
        sketch_key,
        lambda: preview_reassign(_twin(), project_id, sketch_key, body),
    )


@router.post(f"{_BASE}/reassign", response_model=PlaneChangeResult)
async def reassign_sketch_plane(
    project_id: str, sketch_key: str, body: PlaneReassignRequest
) -> PlaneChangeResult:
    """Move the sketch to another plane in ``mode``.

    ``preserve_local`` keeps the stroke coordinates (the sketch moves with
    its plane). ``preserve_world`` keeps every point where it is and is
    refused when the sketch does not lie in the new plane.
    ``project_to_target`` projects the points onto the new plane. A mode that
    moves the strokes saves them as the sketch's next revision.
    """
    return await _call(
        "reassign",
        project_id,
        sketch_key,
        lambda: reassign_plane(_twin(), project_id, sketch_key, body),
    )


@router.post(f"{_BASE}/undo", response_model=PlaneChangeResult)
async def undo_sketch_plane(
    project_id: str, sketch_key: str, body: PlaneSimpleRequest
) -> PlaneChangeResult:
    """Restore the plane (and the strokes, if the change moved them) from before the
    last change, as a new revision. History is never rewritten."""
    return await _call(
        "undo", project_id, sketch_key, lambda: undo_plane(_twin(), project_id, sketch_key, body)
    )


@router.post(f"{_BASE}/archive", response_model=PlaneChangeResult)
async def archive_sketch_plane(
    project_id: str, sketch_key: str, body: PlaneSimpleRequest
) -> PlaneChangeResult:
    """Archive the sketch's plane as a new revision. Undo brings it back."""
    return await _call(
        "archive",
        project_id,
        sketch_key,
        lambda: archive_plane(_twin(), project_id, sketch_key, body),
    )
