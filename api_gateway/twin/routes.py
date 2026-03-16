"""Digital Twin viewer REST endpoints for the MetaForge Gateway.

Exposes the Twin's work-product graph to the dashboard frontend.
Endpoints live under ``/v1/twin``.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query

from api_gateway.convert.service import ConversionService
from api_gateway.twin.schemas import TwinNodeListResponse, TwinNodeResponse
from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin")

# ---------------------------------------------------------------------------
# Twin integration (initialised by server lifespan)
# ---------------------------------------------------------------------------

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default InMemoryTwinAPI with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("twin_viewer_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/twin", tags=["twin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wp_to_response(wp: WorkProduct) -> TwinNodeResponse:
    """Map a WorkProduct to a TwinNodeResponse."""
    properties: dict[str, str | int | float | bool] = {
        "wp_type": wp.type.value,
        "format": wp.format,
        "file_path": wp.file_path,
        "created_by": wp.created_by,
    }
    # Merge metadata, keeping only JSON-primitive values
    for key, value in wp.metadata.items():
        if isinstance(value, (str, int, float, bool)):
            properties[key] = value

    return TwinNodeResponse(
        id=str(wp.id),
        name=wp.name,
        type="work_product",
        domain=wp.domain,
        status="valid",
        properties=properties,
        updatedAt=wp.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/nodes", response_model=TwinNodeListResponse)
async def list_twin_nodes(
    domain: str | None = None,
) -> TwinNodeListResponse:
    """List all work-product nodes in the Digital Twin."""
    with tracer.start_as_current_span("twin.list_nodes") as span:
        if domain is not None:
            span.set_attribute("twin.filter.domain", domain)
        work_products = await _twin.list_work_products(domain=domain)
        nodes = [_wp_to_response(wp) for wp in work_products]
        logger.info("twin_nodes_listed", count=len(nodes), domain=domain)
        return TwinNodeListResponse(nodes=nodes, total=len(nodes))


@router.get("/nodes/{node_id}", response_model=TwinNodeResponse)
async def get_twin_node(node_id: str) -> TwinNodeResponse:
    """Get a single work-product node by ID."""
    with tracer.start_as_current_span("twin.get_node") as span:
        span.set_attribute("twin.node_id", node_id)
        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")
        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Node not found")
        logger.info("twin_node_retrieved", node_id=node_id)
        return _wp_to_response(wp)


_WORKSPACE_DIR = Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))


@router.get("/nodes/{node_id}/model")
async def get_node_model(
    node_id: str,
    quality: str = Query("standard", pattern="^(preview|standard|fine)$"),
) -> dict:
    """Convert a CAD work-product's STEP file to GLB and return the URL.

    Reads the STEP file from the shared adapter workspace, converts it
    via the OCCT converter, and returns the GLB URL + metadata.
    """
    with tracer.start_as_current_span("twin.get_node_model") as span:
        span.set_attribute("twin.node_id", node_id)

        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")

        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Node not found")

        # Find the STEP file in the workspace
        file_path = wp.file_path
        if not file_path:
            # Try to find a STEP file matching the WP name pattern
            candidates = list(_WORKSPACE_DIR.glob("*.step"))
            if not candidates:
                raise HTTPException(
                    status_code=404,
                    detail="No STEP file found for this work product",
                )
            # Use the most recently modified STEP file
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            file_path = str(candidates[0])

        step_path = Path(file_path)
        if not step_path.is_absolute():
            step_path = _WORKSPACE_DIR / step_path

        if not step_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"STEP file not found: {step_path.name}",
            )

        span.set_attribute("step.file", str(step_path))
        file_bytes = step_path.read_bytes()

        service = ConversionService()
        result = service.convert(file_bytes, step_path.name, quality)

        logger.info(
            "node_model_converted",
            node_id=node_id,
            step_file=step_path.name,
            cached=result.get("cached", False),
        )
        return result
