"""Exact CAD and STEP export routes (CDE slice 6).

* ``POST /v1/twin/items/{key}/exact-build`` starts the exact build of a CAD
  item from its current head (an explicit commit or an on-demand request,
  never a pointer event). With ``design_ir`` or ``script`` it builds new
  geometry; with neither it validates the head in place.
* ``GET /v1/twin/nodes/{node_id}/exports`` is one revision's geometry state
  and its GLB, native CAD and STEP states (UI-010).
* ``POST /v1/twin/nodes/{node_id}/exports/step`` queues the STEP export of a
  validated revision, or returns the one already made for it.
* ``GET /v1/twin/nodes/{node_id}/exports/step/file`` downloads it.

The work itself is :mod:`api_gateway.twin.exact_cad`; these routes only map
its errors onto HTTP.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api_gateway.twin.exact_cad import (
    ExactCadError,
    ExactCadService,
    StepExportError,
    StepExportRefusedError,
    get_exact_cad_service,
    read_export_file,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.exact_cad_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

#: The job session REST requests run under (jobs are per session for reuse).
REST_SESSION = "gateway-rest"


class ExactBuildRequest(BaseModel):
    project_id: str
    #: The head revision the build starts from; defaults to the current head.
    base_revision: str | None = None
    #: A design IR document to lower and build (CadQuery).
    design_ir: dict[str, Any] | None = None
    #: A CadQuery script to build.
    script: str | None = None


class StepExportRequest(BaseModel):
    #: A gate or release export: the round trip must pass.
    critical: bool = False


class JobReply(BaseModel):
    job_id: str | None = None
    status: str
    poll: str | None = None
    expected_seconds: float | None = None
    tool_id: str | None = None
    reused: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    #: Set when a cached STEP export answered at once.
    step_export: dict[str, Any] | None = None
    url: str | None = None
    cache_hit: bool | None = None


class ExportState(BaseModel):
    #: glb | native | step
    format: str
    #: READY | STALE | QUEUED | FAILED | NONE
    state: str
    detail: str = ""
    schema_: str | None = Field(default=None, alias="schema")
    round_trip: str | None = None
    url: str | None = None
    revision: int | None = None

    model_config = {"populate_by_name": True}


class NodeArtifactsResponse(BaseModel):
    node_id: str
    item_key: str | None = None
    revision: int | None = None
    head_revision: int | None = None
    is_head: bool = False
    geometry_state: str | None = None
    badge: str | None = None
    authority: str | None = None
    validity: dict[str, Any] | None = None
    latest_attempt: dict[str, Any] | None = None
    last_validated: dict[str, Any] | None = None
    can_export_step: bool = False
    exports: list[ExportState]


def _service() -> ExactCadService:
    service = get_exact_cad_service()
    if service is None:
        raise HTTPException(status_code=503, detail="exact CAD is not configured on this gateway")
    return service


@router.post("/items/{key}/exact-build", response_model=JobReply, status_code=202)
async def start_exact_build(key: str, body: ExactBuildRequest) -> Any:
    """Build (or validate) a CAD item's exact geometry as a background job."""
    from twin_core.items import ItemRevisionConflictError

    service = _service()
    with tracer.start_as_current_span("twin.exact_cad.route_build") as span:
        span.set_attribute("item.key", key)
        try:
            reply = await service.start_build(
                project_id=body.project_id,
                item_key=key,
                base_revision=body.base_revision,
                design_ir=body.design_ir,
                script=body.script,
                session_id=REST_SESSION,
            )
        except ItemRevisionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExactCadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return reply


@router.get("/nodes/{node_id}/exports", response_model=NodeArtifactsResponse)
async def get_node_exports(node_id: str) -> Any:
    """A revision's geometry state and its GLB, native CAD and STEP export states."""
    service = _service()
    with tracer.start_as_current_span("twin.exact_cad.route_exports") as span:
        span.set_attribute("node.id", node_id)
        try:
            view = await service.artifact_view(node_id)
        except ExactCadError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return view


@router.post("/nodes/{node_id}/exports/step", response_model=JobReply, status_code=202)
async def request_step_export(node_id: str, body: StepExportRequest | None = None) -> Any:
    """Queue the STEP export of a validated revision; a cached one answers 200 at once."""
    service = _service()
    critical = bool(body.critical) if body is not None else False
    with tracer.start_as_current_span("twin.exact_cad.route_step") as span:
        span.set_attribute("node.id", node_id)
        try:
            reply = await service.start_export(
                node_id=node_id, critical=critical, session_id=REST_SESSION
            )
        except StepExportRefusedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StepExportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ExactCadError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if reply.get("cache_hit"):
            return JSONResponse(status_code=200, content=JobReply(**reply).model_dump())
        return reply


@router.get("/nodes/{node_id}/exports/step/file")
async def download_step_export(node_id: str) -> Response:
    """The STEP file exported from this revision (kept even once it is stale)."""
    from uuid import UUID

    from api_gateway.twin.routes import get_twin
    from twin_core.geometry_state import STEP_EXPORT_KEY

    twin: Any = get_twin()
    try:
        wp = await twin.get_work_product(UUID(node_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="node_id is not a UUID") from exc
    export = ((wp.metadata or {}) if wp is not None else {}).get(STEP_EXPORT_KEY)
    if not isinstance(export, dict) or export.get("state") not in ("ready", "stale"):
        raise HTTPException(status_code=404, detail="no STEP has been exported from this revision")
    content = read_export_file(export)
    if content is None:
        raise HTTPException(status_code=404, detail="the exported STEP file is no longer stored")
    filename = str(export.get("filename") or "export.step")
    logger.info("step_export_downloaded", node_id=node_id, size_bytes=len(content))
    return Response(
        content=content,
        media_type="application/step",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
