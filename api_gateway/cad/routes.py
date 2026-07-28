"""CAD authoring REST endpoints (MET-10).

``POST /v1/cad/assembly`` authors a multi-part FreeCAD assembly deterministically
(no LLM) and commits it to the twin as a loadable ``cad_model`` — the CLI's path
to complex, multi-assembly geometry.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.cad.builder import build_assembly
from api_gateway.cad.schemas import AssemblyResponse, CreateAssemblyRequest

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/cad", tags=["cad"])


@router.post("/assembly", response_model=AssemblyResponse, status_code=201)
async def create_assembly(body: CreateAssemblyRequest) -> AssemblyResponse:
    """Author + commit a multi-part assembly from a declarative spec."""
    from api_gateway.chat.routes import get_mcp_bridge
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.twin.geometry_recorder import make_geometry_recorder
    from api_gateway.twin.routes import get_twin

    bridge = get_mcp_bridge()
    recorder = make_geometry_recorder(get_twin(), get_project_backend())
    parts = [p.model_dump() for p in body.parts]
    try:
        rec = await build_assembly(
            bridge=bridge,
            recorder=recorder,
            name=body.name,
            parts=parts,
            project_id=body.project_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean 502 with the cause
        logger.warning("cad_assembly_failed", name=body.name, error=str(exc))
        raise HTTPException(status_code=502, detail=f"assembly authoring failed: {exc}") from exc

    node_id = rec.get("node_id")
    if not node_id:
        raise HTTPException(status_code=502, detail="assembly committed but no node id returned")
    return AssemblyResponse(
        node_id=str(node_id),
        model_url=rec.get("model_url") or f"/v1/twin/nodes/{node_id}/model",
        minio_object_key=rec.get("minio_object_key"),
        content_hash=rec.get("content_hash"),
        part_count=len(parts),
    )
