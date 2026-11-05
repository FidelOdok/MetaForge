"""Representation levels and component maturity over REST (C3D slice 2).

* ``POST /v1/twin/projects/{project_id}/structured-mesh`` draws the concept's
  block layout as a structured 3D mesh (L1): one named node per component,
  sizes estimated. Fast and free, so it answers inline. An unchanged layout
  returns the mesh already made (``reused``) instead of a new revision.
* ``GET /v1/twin/projects/{project_id}/maturity`` answers, per component,
  Illustrative, Structured, Parametric, Exact, Verified or Released with the
  evidence for each claim, and the product's readiness, which is never above
  its least mature required component.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_gateway.twin.structured_mesh import (
    NoLayoutError,
    StructuredMeshError,
    make_structured_mesh_recorder,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.representation_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

_recorder: Any = None
_metrics: Any = None


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _project_backend() -> Any:
    try:
        from api_gateway.projects.routes import get_project_backend

        return get_project_backend()
    except Exception as exc:  # noqa: BLE001 - the link is best-effort
        logger.info("structured_mesh_project_backend_unavailable", error=str(exc))
        return None


def set_recorder(recorder: Any) -> None:
    """Install the mesh recorder (tests); ``None`` builds the default per call."""
    global _recorder  # noqa: PLW0603
    _recorder = recorder


def _record_query(product: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_maturity_query(product)
    except Exception:  # noqa: BLE001
        pass


def _project(project_id: str) -> UUID:
    try:
        return UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project id") from exc


class StructuredMeshRequest(BaseModel):
    #: A sketch made by ``twin.render_concept_sketch``; default the newest.
    layout_node_id: str | None = None
    operation_id: str | None = None


class StructuredMeshResponse(BaseModel):
    node_id: str
    name: str
    format: str = "glb"
    level: str = "L1"
    authority: str = "structured"
    dimensions: str = "estimated"
    label: str
    source_revision: str | None = None
    #: ``{part name: component id}``.
    component_ids: dict[str, str] = Field(default_factory=dict)
    #: ``[{component_id, name, node}]``: the GLB's node per component.
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    triangle_count: int = 0
    #: True when the same mesh already existed and nothing was written.
    reused: bool = False


class MaturityEvidence(BaseModel):
    maturity: str
    kind: str
    node_id: str | None = None
    ref: str | None = None
    detail: str = ""


class ComponentMaturityResponse(BaseModel):
    component_id: str
    name: str
    required: bool
    source: str
    #: Illustrative | Structured | Parametric | Exact | Verified | Released
    maturity: str
    evidence: list[MaturityEvidence] = Field(default_factory=list)
    next_step: str | None = None


class ProductMaturityResponse(BaseModel):
    #: The least mature required component's maturity; None with no components.
    maturity: str | None = None
    by_maturity: dict[str, list[str]] = Field(default_factory=dict)
    required_count: int = 0
    component_count: int = 0
    #: The components holding the product at its maturity.
    limited_by: list[str] = Field(default_factory=list)
    #: Whether every required component is at least Exact, Verified, Released.
    claims: dict[str, bool] = Field(default_factory=dict)
    summary: str = ""


class MaturityResponse(BaseModel):
    project_id: str
    components: list[ComponentMaturityResponse]
    product: ProductMaturityResponse
    has_assembly: bool = False


@router.post(
    "/projects/{project_id}/structured-mesh",
    response_model=StructuredMeshResponse,
)
async def make_structured_mesh(
    project_id: str, body: StructuredMeshRequest | None = None
) -> StructuredMeshResponse:
    """Show the concept as simple 3D parts (L1): one node per component, sizes estimated."""
    _project(project_id)
    req = body or StructuredMeshRequest()
    with tracer.start_as_current_span("twin.structured_mesh.route") as span:
        span.set_attribute("project.id", project_id)
        recorder = _recorder or make_structured_mesh_recorder(_twin(), _project_backend())
        try:
            result = await recorder(
                project_id=project_id,
                layout_node_id=req.layout_node_id,
                trigger="create",
                operation_id=req.operation_id,
            )
        except NoLayoutError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StructuredMeshError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return StructuredMeshResponse(**result)


@router.get("/projects/{project_id}/maturity", response_model=MaturityResponse)
async def project_maturity(project_id: str) -> MaturityResponse:
    """Per-component maturity and the product's readiness, with evidence for each claim."""
    from twin_core.maturity import compute_maturity

    pid = _project(project_id)
    with tracer.start_as_current_span("twin.maturity.route") as span:
        span.set_attribute("project.id", project_id)
        try:
            result = await compute_maturity(_twin(), pid)
        except Exception as exc:
            span.record_exception(exc)
            _record_query("error")
            logger.error("maturity_query_failed", project_id=project_id, error=str(exc))
            raise HTTPException(status_code=500, detail="maturity could not be computed") from exc
        _record_query(str(result["product"]["maturity"] or "none"))
        return MaturityResponse(**result)


__all__ = ["router", "set_recorder"]
