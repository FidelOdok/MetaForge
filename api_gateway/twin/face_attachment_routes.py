"""Face-attached sketch plane routes (CDE slice 9).

Under ``/v1/twin/projects/{project_id}``:

- ``GET .../attachable-geometry``: the CAD items whose head is validated
  exact geometry, so a sketch can be put on one of their faces. When there
  is none, ``reason`` says why in plain words.
- ``GET .../items/{item_key}/faces``: the candidate (planar) faces of one
  validated revision (``?revision=n``, the head by default), each with its
  normal, offset, area, centroid, outer-loop edges, neighbours, semantic tag
  and a signature summary, plus the GLB triangle range the viewer picks and
  highlights (``mesh_map``). Reads only.
- ``POST .../sketches/{sketch_key}/plane/attach-face``: put the sketch on a
  face (``item_key``, ``revision``, ``face_id``, ``op_id``); a new plane
  revision, ``ATTACHED``.
- ``POST .../sketches/{sketch_key}/plane/resolve-reattachment``: answer
  ``REATTACHMENT_REQUIRED``: ``pick_face`` on the current revision,
  ``detach_principal`` (onto Front, Side or Top) or ``detach_unassigned``.

Each write is idempotent per ``op_id``. The logic lives in
:mod:`api_gateway.twin.face_attachment`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog
from fastapi import APIRouter, HTTPException, Query

from api_gateway.twin.face_attachment import (
    AttachableResponse,
    AttachFaceRequest,
    FaceAttachmentError,
    FaceCatalogUnavailableError,
    FaceListResponse,
    ResolveReattachmentRequest,
    attach_to_face,
    attachable_revisions,
    list_faces,
    resolve_reattachment,
)
from api_gateway.twin.sketch_planes import PlaneChangeResult, PlaneError
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.face_attachment_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

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


async def _call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run one call, mapping refusals to 404/409, unavailability to 503, else 502."""
    from twin_core.items import ItemError

    _check_project_id(project_id)
    with tracer.start_as_current_span(f"twin.face_attachment_routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await fn()
        except FaceCatalogUnavailableError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (FaceAttachmentError, PlaneError) as exc:
            span.record_exception(exc)
            text = str(exc)
            status = 404 if text.startswith(("no saved sketch", "no CAD item")) else 409
            raise HTTPException(status_code=status, detail=text) from exc
        except (ItemError, ValueError) as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning(
                "face_attachment_route_failed", route=name, project_id=project_id, error=str(exc)
            )
            raise HTTPException(
                status_code=502, detail="The model surface could not be read or attached"
            ) from exc


@router.get("/projects/{project_id}/attachable-geometry", response_model=AttachableResponse)
async def get_attachable_geometry(project_id: str) -> AttachableResponse:
    """CAD items whose head is validated exact geometry (a face can be picked on them)."""
    return await _call("attachable", project_id, lambda: attachable_revisions(_twin(), project_id))


@router.get("/projects/{project_id}/items/{item_key}/faces", response_model=FaceListResponse)
async def get_candidate_faces(
    project_id: str,
    item_key: str,
    revision: int | None = Query(default=None, ge=1),
) -> FaceListResponse:
    """The planar faces of a validated CAD revision, with the GLB mesh map. Reads only."""
    return await _call(
        "faces", project_id, lambda: list_faces(_twin(), project_id, item_key, revision)
    )


@router.post(
    "/projects/{project_id}/sketches/{sketch_key}/plane/attach-face",
    response_model=PlaneChangeResult,
)
async def attach_sketch_to_face(
    project_id: str, sketch_key: str, body: AttachFaceRequest
) -> PlaneChangeResult:
    """Put the sketch on a planar face of a validated CAD revision.

    The plane frame comes from the face: origin at the face centroid on its
    plane, U along the longest straight edge of its outer loop, the normal
    outward, right-handed. The face's geometry signature is stored so the
    face can be found again when the CAD item changes. A retried ``op_id``
    returns the revision it already made.
    """
    return await _call(
        "attach", project_id, lambda: attach_to_face(_twin(), project_id, sketch_key, body)
    )


@router.post(
    "/projects/{project_id}/sketches/{sketch_key}/plane/resolve-reattachment",
    response_model=PlaneChangeResult,
)
async def resolve_sketch_reattachment(
    project_id: str, sketch_key: str, body: ResolveReattachmentRequest
) -> PlaneChangeResult:
    """Answer ``REATTACHMENT_REQUIRED``: pick a face, or detach the sketch.

    ``pick_face`` attaches to ``face_id`` on the attached item's current
    revision; ``detach_principal`` moves the sketch onto ``principal``
    keeping its local coordinates; ``detach_unassigned`` keeps it as a
    concept with no plane. Refused (409) when the plane does not need it.
    """
    return await _call(
        "resolve",
        project_id,
        lambda: resolve_reattachment(_twin(), project_id, sketch_key, body),
    )
