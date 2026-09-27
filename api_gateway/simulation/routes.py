"""Load-case work-product API (FORGE-278).

A load case (material, supports, loads, source of loads) is persisted as a
first-class ``LOAD_CASE`` work product -- the same generic document-recorder
path ``twin.record_document`` uses (see ``api_gateway/twin/document_recorder.py``)
-- so a case authored from the dashboard's Sim tab and one authored by an
agent via the MCP tool are indistinguishable on the twin, and both show up in
the other's list.

Serves ``GET /v1/simulation/load-cases`` (list, optionally scoped to a
project) and ``POST /v1/simulation/load-cases`` (create) for the dashboard's
Sim tab. Read-only fields the dashboard's list needs (material, node sets,
force vector) live in ``metadata`` -- mirrored from ``content`` so the list
renders without fetching each work product's blob.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.simulation.routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("simulation_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/simulation", tags=["simulation"])


class LoadCaseResponse(BaseModel):
    """One load case, in the dashboard's camelCase shape."""

    id: str
    name: str
    material: dict[str, Any] | None = None
    fixedNodeSet: str | None = None  # noqa: N815 — dashboard contract is camelCase
    loadNodeSet: str | None = None  # noqa: N815
    loadForceN: list[float] | None = None  # noqa: N815
    sourceOfLoads: str | None = None  # noqa: N815
    projectId: str  # noqa: N815
    createdAt: str  # noqa: N815
    updatedAt: str  # noqa: N815


class LoadCaseListResponse(BaseModel):
    loadCases: list[LoadCaseResponse]  # noqa: N815
    total: int


class CreateLoadCaseRequest(BaseModel):
    """Body for ``POST /v1/simulation/load-cases``."""

    name: str = Field(min_length=1, max_length=200)
    projectId: str = Field(min_length=1)  # noqa: N815
    material: dict[str, Any] = Field(min_length=1)
    fixedNodeSet: str = Field(min_length=1)  # noqa: N815
    loadNodeSet: str = Field(min_length=1)  # noqa: N815
    loadForceN: list[float] = Field(min_length=3, max_length=3)  # noqa: N815
    sourceOfLoads: str | None = None  # noqa: N815
    sourcePartNodeIds: list[str] | None = None  # noqa: N815


def _wp_to_load_case(wp: WorkProduct) -> LoadCaseResponse:
    md = wp.metadata or {}
    return LoadCaseResponse(
        id=str(wp.id),
        name=wp.name,
        material=md.get("material") if isinstance(md.get("material"), dict) else None,
        fixedNodeSet=md.get("fixed_node_set"),
        loadNodeSet=md.get("load_node_set"),
        loadForceN=md.get("load_force_n"),
        sourceOfLoads=md.get("source_of_loads"),
        projectId=str(wp.project_id) if wp.project_id else "",
        createdAt=wp.created_at.isoformat(),
        updatedAt=wp.updated_at.isoformat(),
    )


@router.get("/load-cases", response_model=LoadCaseListResponse)
async def list_load_cases(project_id: str | None = None) -> LoadCaseListResponse:
    """List load cases, optionally scoped to a project.

    Empty (not a 404) when the project has none yet, matching ``/v1/bom``.
    """
    with tracer.start_as_current_span("simulation.list_load_cases") as span:
        scoped: UUID | None = None
        if project_id:
            try:
                scoped = UUID(project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
            span.set_attribute("simulation.project_id", project_id)
        wps = await _twin.list_work_products(
            work_product_type=WorkProductType.LOAD_CASE, project_id=scoped
        )
        cases = [_wp_to_load_case(wp) for wp in wps]
        span.set_attribute("simulation.count", len(cases))
        logger.info("load_cases_listed", count=len(cases), project_id=project_id)
        return LoadCaseListResponse(loadCases=cases, total=len(cases))


@router.post("/load-cases", response_model=LoadCaseResponse, status_code=201)
async def create_load_case(body: CreateLoadCaseRequest) -> LoadCaseResponse:
    """Create a load case, via the same document-recorder path
    ``twin.record_document(document_type='load_case')`` uses (see module
    docstring) so a dashboard-authored and agent-authored case are
    indistinguishable on the twin.
    """
    # MET-575: fetch the accessor at call time, never bind an alias at
    # import time -- an early alias keeps pointing at the empty in-memory
    # store after server startup swaps in the real (Postgres) backend.
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.twin.document_recorder import make_document_recorder

    with tracer.start_as_current_span("simulation.create_load_case") as span:
        metadata = {
            "material": body.material,
            "fixed_node_set": body.fixedNodeSet,
            "load_node_set": body.loadNodeSet,
            "load_force_n": body.loadForceN,
            **({"source_of_loads": body.sourceOfLoads} if body.sourceOfLoads else {}),
        }
        content = json.dumps(metadata)
        record = make_document_recorder(_twin, get_project_backend())
        result = await record(
            content=content,
            name=body.name,
            wp_type=WorkProductType.LOAD_CASE,
            domain="mechanical",
            fmt="json",
            link_type="load_case",
            source_tool="api_gateway.simulation.routes",
            project_id=body.projectId,
            extra_metadata=metadata,
            source_part_node_ids=body.sourcePartNodeIds,
        )
        span.set_attribute("simulation.node_id", result["node_id"])
        wp = await _twin.get_work_product(UUID(result["node_id"]))
        if wp is None:  # pragma: no cover — the record we just created
            raise HTTPException(status_code=500, detail="Load case created but not readable back")
        logger.info("load_case_created", node_id=result["node_id"], project_id=body.projectId)
        return _wp_to_load_case(wp)
