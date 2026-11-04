"""Sketch compiler routes: store an interpretation, promote it, approve the result.

- ``POST /v1/twin/projects/{project_id}/sketches/{item_key}/interpretations``
  stores the browser's interpretation of a saved sketch as a proposal.
- ``POST /v1/twin/projects/{project_id}/interpretations/{key}/promote``
  makes a ``parametric_sketch`` from its accepted constraints, held for
  approval.
- ``POST /v1/twin/nodes/{node_id}/approve-parametric-sketch`` signs it off
  (also reachable as ``parametric_sketch:<id>`` through ``/v1/approvals``).

The logic lives in :mod:`api_gateway.twin.sketch_compiler_recorder`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.twin.sketch_compiler_recorder import (
    ApproveParametricRequest,
    ApproveParametricResponse,
    InterpretationSaveRequest,
    InterpretationSaveResponse,
    PromoteRequest,
    PromoteResponse,
    SketchCompileError,
    approve_parametric_sketch,
    promote_interpretation,
    save_interpretation,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_compiler_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _http(exc: SketchCompileError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _count_failure(stage: str) -> None:
    from api_gateway.twin.sketch_compiler_recorder import _count

    _count(stage, "failed")


@router.post(
    "/projects/{project_id}/sketches/{item_key}/interpretations",
    response_model=InterpretationSaveResponse,
)
async def store_interpretation(
    project_id: str, item_key: str, body: InterpretationSaveRequest
) -> InterpretationSaveResponse:
    """Store a proposal: primitives fitted to a saved sketch and proposed constraints.

    The browser computes it; the gateway validates its shape, re-checks its
    basic geometry and records it as the next revision of the sketch's
    ``SKI-*`` item, depending on the sketch revision it was made from. Every
    value in it is estimated. A retried ``interpretation_id`` returns the
    revision it already made. 409 when the sketch has moved on since.
    """
    with tracer.start_as_current_span("twin.sketch_compiler_routes.interpret") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("sketch.item_key", item_key)
        try:
            return await save_interpretation(_twin(), project_id, item_key, body)
        except SketchCompileError as exc:
            span.record_exception(exc)
            raise _http(exc) from exc
        except Exception as exc:
            span.record_exception(exc)
            _count_failure("interpretation")
            logger.warning("sketch_interpretation_failed", project_id=project_id, error=str(exc))
            raise HTTPException(
                status_code=502, detail="The interpretation could not be stored"
            ) from exc


@router.post(
    "/projects/{project_id}/interpretations/{interpretation_key}/promote",
    response_model=PromoteResponse,
)
async def promote(
    project_id: str, interpretation_key: str, body: PromoteRequest
) -> PromoteResponse:
    """Promote an interpretation (``SKI-...`` for its head, ``SKI-...@n``) to a parametric sketch.

    Only its accepted constraints are kept. The gateway re-checks each of
    them on the geometry and refuses with 409 if one does not hold, if none
    is accepted, or if the sketch has changed since. Depth, angle and
    material are never invented: the feature plan marks them
    ``required_input``. The result waits for approval. A retried
    ``promote_id`` returns what it already made.
    """
    with tracer.start_as_current_span("twin.sketch_compiler_routes.promote") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("sketch.interpretation_ref", interpretation_key)
        try:
            return await promote_interpretation(_twin(), project_id, interpretation_key, body)
        except SketchCompileError as exc:
            span.record_exception(exc)
            raise _http(exc) from exc
        except Exception as exc:
            span.record_exception(exc)
            _count_failure("promotion")
            logger.warning("sketch_promotion_failed", project_id=project_id, error=str(exc))
            raise HTTPException(status_code=502, detail="The sketch could not be promoted") from exc


async def approve_parametric_node(
    node_id: UUID, approved_by: str | None, audit: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Approve a parametric sketch. Shared with ``/v1/approvals``."""
    try:
        return await approve_parametric_sketch(_twin(), node_id, approved_by, audit)
    except SketchCompileError as exc:
        raise _http(exc) from exc


@router.post("/nodes/{node_id}/approve-parametric-sketch", response_model=ApproveParametricResponse)
async def approve_parametric(
    node_id: UUID, body: ApproveParametricRequest
) -> ApproveParametricResponse:
    """Human sign-off on a parametric sketch, through the design-sketch approver."""
    with tracer.start_as_current_span("twin.sketch_compiler_routes.approve") as span:
        span.set_attribute("sketch.node_id", str(node_id))
        result = await approve_parametric_node(node_id, body.approved_by)
        return ApproveParametricResponse(
            node_id=result["node_id"],
            approved=result["approved"],
            approved_at=result.get("approved_at"),
        )
