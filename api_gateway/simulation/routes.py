"""Load-case + simulation-result work-product API (FORGE-278, FORGE-279).

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

FORGE-279: ``GET /v1/simulation/results`` lists ``SIMULATION_RESULT`` work
products the same way -- FORGE-246 already persists these (agent-side, via
``twin.record_document(document_type='simulation_result')`` after a
``calculix.extract_results`` run) with ``max_von_mises_mpa``,
``max_displacement_mm``, ``load_case`` (a free-text name of which load case
produced it) and an optional ``mesh_stats`` object flattened onto
``metadata``; this route is the piece FORGE-246 didn't add, a read path the
dashboard's Sim tab can list and compare from. There is deliberately no
``POST`` here -- creation stays exactly the agent-driven
``twin.record_document`` path (a result without a real FEA run behind it
would be worse than no result at all).

FORGE-532: each listed result also says whether its 3D result field was
stored (``hasField``) and what it holds, which geometry it analysed, its
boundary conditions and any mesh-convergence verdict; ``GET
/v1/simulation/results/{id}/field`` serves the field itself (gzipped
``metaforge.sim_field`` JSON, ``Content-Encoding: gzip`` so a browser
inflates it transparently). A result recorded before FORGE-532 has no
field: the list says ``hasField: false`` and the route answers 404 with
``field not stored``, which the dashboard shows as a note rather than an
error.

FORGE-277: ``POST /v1/simulation/named-faces`` is unrelated to load cases/
results but lives here too -- it backs the load-case dialog's face picker,
bridging to the ``freecad.list_named_faces`` MCP tool.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Response
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


class SimulationResultResponse(BaseModel):
    """One FEA result, in the dashboard's camelCase shape (FORGE-279)."""

    id: str
    name: str
    maxVonMisesMpa: float | None = None  # noqa: N815
    maxDisplacementMm: float | None = None  # noqa: N815
    loadCase: str | None = None  # noqa: N815 — free-text name, not a load-case node id
    meshStats: dict[str, Any] | None = None  # noqa: N815
    projectId: str  # noqa: N815
    createdAt: str  # noqa: N815
    updatedAt: str  # noqa: N815
    # FORGE-532: 3D result field + what was analysed. All optional, so a
    # result recorded before the field existed lists exactly as it did.
    maxTemperatureC: float | None = None  # noqa: N815
    analysisType: str | None = None  # noqa: N815 — static_stress | modal | thermal
    hasField: bool = False  # noqa: N815
    fieldQuantities: list[str] = Field(default_factory=list)  # noqa: N815
    fieldRanges: dict[str, Any] | None = None  # noqa: N815
    fieldSizeBytes: int | None = None  # noqa: N815
    analysedGeometry: dict[str, Any] | None = None  # noqa: N815 — {node_id, revision, ...}
    loadCaseSpec: dict[str, Any] | None = None  # noqa: N815
    fixtures: list[dict[str, Any]] | None = None
    loads: list[dict[str, Any]] | None = None
    # calculix.check_mesh_convergence output recorded on the result:
    # {points: [{element_size_mm, max_von_mises_mpa, element_count?}],
    #  changes, converged, recommendation}.
    meshConvergence: dict[str, Any] | None = None  # noqa: N815


class SimulationResultListResponse(BaseModel):
    results: list[SimulationResultResponse]
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


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _dict_list_or_none(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    return [v for v in value if isinstance(v, dict)]


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _wp_to_simulation_result(wp: WorkProduct) -> SimulationResultResponse:
    md = wp.metadata or {}
    quantities = md.get("field_quantities")
    load_case = md.get("load_case")
    analysis_type = md.get("field_analysis_type") or md.get("analysis_type")
    return SimulationResultResponse(
        id=str(wp.id),
        name=wp.name,
        maxVonMisesMpa=_float_or_none(md.get("max_von_mises_mpa")),
        maxDisplacementMm=_float_or_none(md.get("max_displacement_mm")),
        loadCase=load_case if isinstance(load_case, str) else None,
        meshStats=_dict_or_none(md.get("mesh_stats")),
        projectId=str(wp.project_id) if wp.project_id else "",
        createdAt=wp.created_at.isoformat(),
        updatedAt=wp.updated_at.isoformat(),
        maxTemperatureC=_float_or_none(md.get("max_temperature_c")),
        analysisType=analysis_type if isinstance(analysis_type, str) else None,
        hasField=bool(md.get("field_stored") and md.get("field_object_key")),
        fieldQuantities=[q for q in quantities if isinstance(q, str)]
        if isinstance(quantities, list)
        else [],
        fieldRanges=_dict_or_none(md.get("field_ranges")),
        fieldSizeBytes=md.get("field_size_bytes")
        if isinstance(md.get("field_size_bytes"), int)
        else None,
        analysedGeometry=_dict_or_none(md.get("analysed_geometry")),
        loadCaseSpec=_dict_or_none(md.get("load_case_spec")),
        fixtures=_dict_list_or_none(md.get("fixtures")),
        loads=_dict_list_or_none(md.get("loads")),
        meshConvergence=_dict_or_none(md.get("mesh_convergence")),
    )


@router.get("/results", response_model=SimulationResultListResponse)
async def list_simulation_results(project_id: str | None = None) -> SimulationResultListResponse:
    """List FEA results, optionally scoped to a project (FORGE-279).

    Empty (not a 404) when the project has none yet, matching
    ``list_load_cases``. Creation is agent-driven only (see module
    docstring) — there is no corresponding POST.
    """
    with tracer.start_as_current_span("simulation.list_simulation_results") as span:
        scoped: UUID | None = None
        if project_id:
            try:
                scoped = UUID(project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
            span.set_attribute("simulation.project_id", project_id)
        wps = await _twin.list_work_products(
            work_product_type=WorkProductType.SIMULATION_RESULT, project_id=scoped
        )
        results = [_wp_to_simulation_result(wp) for wp in wps]
        # Newest first — a version-compare view wants the latest runs up top.
        results.sort(key=lambda r: r.createdAt, reverse=True)
        span.set_attribute("simulation.count", len(results))
        logger.info("simulation_results_listed", count=len(results), project_id=project_id)
        return SimulationResultListResponse(results=results, total=len(results))


#: Served media type of a result field (the inflated body is JSON).
SIM_FIELD_MEDIA_TYPE = "application/vnd.metaforge.sim-field+json"


@router.get(
    "/results/{result_id}/field",
    response_class=Response,
    responses={
        200: {
            "description": (
                "The result's gzipped metaforge.sim_field JSON payload (Content-Encoding: gzip)."
            ),
            "content": {SIM_FIELD_MEDIA_TYPE: {}},
        },
        404: {"description": "No such result, or the result has no stored field."},
    },
)
async def get_simulation_result_field(result_id: str) -> Response:
    """Serve a simulation_result's 3D result field (FORGE-532).

    404 ``field not stored`` for a result recorded without one (every
    result before FORGE-532, or one whose MinIO write failed); the
    dashboard treats that as "show the numbers only".
    """
    with tracer.start_as_current_span("simulation.get_result_field") as span:
        span.set_attribute("simulation.result_id", result_id)
        try:
            uid = UUID(result_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid result id format")
        wp = await _twin.get_work_product(uid)
        if wp is None or wp.type != WorkProductType.SIMULATION_RESULT:
            raise HTTPException(status_code=404, detail="Simulation result not found")
        md = wp.metadata or {}
        key = md.get("field_object_key")
        if not (md.get("field_stored") and isinstance(key, str) and key):
            span.set_attribute("simulation.field_stored", False)
            raise HTTPException(status_code=404, detail="field not stored")

        from api_gateway.twin.blob_store import fetch_work_product_blob

        try:
            content = fetch_work_product_blob(key)
        except Exception as exc:  # noqa: BLE001 — storage misconfigured / object gone
            span.record_exception(exc)
            logger.warning("simulation_field_fetch_failed", result_id=result_id, error=str(exc))
            raise HTTPException(
                status_code=502, detail="Result field could not be read from storage"
            ) from exc
        span.set_attribute("simulation.field_size", len(content))
        logger.info("simulation_field_served", result_id=result_id, size=len(content))
        headers = {"Content-Encoding": "gzip", "Cache-Control": "private, max-age=3600"}
        etag = md.get("field_content_hash")
        if isinstance(etag, str) and etag:
            headers["ETag"] = f'"{etag}"'
        return Response(content=content, media_type=SIM_FIELD_MEDIA_TYPE, headers=headers)


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
