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


class NamedFace(BaseModel):
    """One geometric face of a generated mesh (FORGE-277).

    Mirrors ``freecad.list_named_faces``'/``generate_mesh``'s ``faces``
    table (FORGE-239) in the dashboard's camelCase shape -- real
    coordinates for a face, not just its opaque gmsh-assigned name.
    """

    name: str
    centroidMm: list[float]  # noqa: N815 — dashboard contract is camelCase
    normal: list[float]
    areaMm2: float  # noqa: N815
    bboxMm: dict[str, list[float]]  # noqa: N815 — {"min": [x,y,z], "max": [x,y,z]}


class NamedFacesResponse(BaseModel):
    meshFile: str  # noqa: N815
    faces: list[NamedFace]


class NamedFacesRequest(BaseModel):
    """Body for ``POST /v1/simulation/named-faces``."""

    meshFile: str = Field(min_length=1)  # noqa: N815


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


@router.post("/named-faces", response_model=NamedFacesResponse)
async def list_named_faces(body: NamedFacesRequest) -> NamedFacesResponse:
    """Named-face geometry for an already-generated mesh (FORGE-277).

    Backs the dashboard's geometric boundary-condition face picker: given a
    mesh file path (from an earlier ``freecad.generate_mesh`` call, e.g.
    surfaced in a forge chat turn), returns each named surface group's real
    centroid/normal/area/bbox so the dashboard can render pickable face
    patches instead of a blind "type the gmsh group name" text field.
    """
    from api_gateway.chat.routes import get_mcp_bridge

    with tracer.start_as_current_span("simulation.list_named_faces") as span:
        span.set_attribute("simulation.mesh_file", body.meshFile)
        bridge = get_mcp_bridge()
        try:
            envelope = await bridge.invoke("freecad.list_named_faces", {"mesh_file": body.meshFile})
        except Exception as exc:  # noqa: BLE001 — surface a clean 502 with the cause
            logger.warning("named_faces_tool_failed", mesh_file=body.meshFile, error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=502, detail=f"freecad.list_named_faces failed: {exc}"
            ) from exc

        if isinstance(envelope, dict) and envelope.get("status") == "error":
            err = envelope.get("error") or envelope
            logger.warning("named_faces_tool_failed", mesh_file=body.meshFile, error=str(err))
            raise HTTPException(status_code=502, detail=f"freecad.list_named_faces failed: {err}")

        data = envelope.get("data", envelope) if isinstance(envelope, dict) else {}
        if not isinstance(data, dict):
            data = {}
        raw_faces = data.get("faces", [])
        faces = [
            NamedFace(
                name=f["name"],
                centroidMm=f["centroid_mm"],
                normal=f["normal"],
                areaMm2=f["area_mm2"],
                bboxMm=f["bbox_mm"],
            )
            for f in raw_faces
        ]
        span.set_attribute("simulation.face_count", len(faces))
        logger.info("named_faces_listed", mesh_file=body.meshFile, count=len(faces))
        return NamedFacesResponse(meshFile=data.get("mesh_file", body.meshFile), faces=faces)
