"""The derived prd (FORGE-528).

``GET /v1/twin/projects/{project_id}/prd`` renders the project's prd from its
one home for requirements: the current prd prose, intent, needs and
objectives, and the current constraint set revision, each labelled with its
``KEY@n``. ``GET /v1/twin/items/{key}/prd`` renders one prd prose revision
(``PRD-...@n``) with the same current requirements. Read-only: the prd prose
is written by ``twin.record_document`` and the requirements by
``twin.record_constraint_set``; neither is written here.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.prd_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])


class PrdSource(BaseModel):
    ref: str
    item_type: str
    node_id: str
    name: str | None = None
    requirement_count: int | None = None


class DerivedPrdResponse(BaseModel):
    project_id: str | None = None
    title: str
    #: The rendered prd: prose plus the live requirement table.
    markdown: str
    #: The prd prose revision rendered, e.g. ``PRD-WIDGET@2``.
    prose_ref: str | None = None
    #: The constraint set revisions the requirement table was read from.
    requirement_refs: list[str] = Field(default_factory=list)
    requirement_count: int = 0
    #: Every ``KEY@n`` the prd was rendered from.
    refs: list[str] = Field(default_factory=list)
    sources: list[PrdSource] = Field(default_factory=list)


def _twin() -> object:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _parse_project(project_id: str | None) -> UUID | None:
    if not project_id:
        return None
    try:
        return UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project_id format") from None


async def _render(project_id: UUID | None, prd_ref: str | None, run_id: str | None) -> dict:
    from api_gateway.twin.requirements_home import render_prd
    from twin_core.items import AmbiguousItemKeyError, ItemError, UnknownItemError

    try:
        return await render_prd(_twin(), project_id, prd_ref=prd_ref, run_id=run_id)
    except UnknownItemError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AmbiguousItemKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ItemError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_RUN_ID = Query(default=None, description="Read as this design-flow run: include its open drafts")


@router.get("/projects/{project_id}/prd", response_model=DerivedPrdResponse)
async def get_project_prd(project_id: str, run_id: str | None = _RUN_ID) -> DerivedPrdResponse:
    """The project's prd, rendered from its current prose and requirements."""
    with tracer.start_as_current_span("twin.project_prd") as span:
        span.set_attribute("twin.filter.project_id", project_id)
        data = await _render(_parse_project(project_id), None, run_id)
        span.set_attribute("prd.requirement_count", data["requirement_count"])
        return DerivedPrdResponse(**data)


@router.get("/items/{key}/prd", response_model=DerivedPrdResponse)
async def get_item_prd(
    key: str, project_id: str | None = None, run_id: str | None = _RUN_ID
) -> DerivedPrdResponse:
    """One prd prose revision (``KEY`` or ``KEY@n``) with the current requirements."""
    with tracer.start_as_current_span("twin.item_prd") as span:
        span.set_attribute("twin.item_key", key)
        data = await _render(_parse_project(project_id), key, run_id)
        span.set_attribute("prd.requirement_count", data["requirement_count"])
        return DerivedPrdResponse(**data)
