"""CAD authoring REST endpoints (MET-10).

``POST /v1/cad/assembly`` authors a multi-part FreeCAD assembly deterministically
(no LLM) and commits it to the twin as a loadable ``cad_model`` — the CLI's path
to complex, multi-assembly geometry.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.cad.builder import build_assembly, validate_assembly_spec
from api_gateway.cad.nl_compiler import compile_spec
from api_gateway.cad.schemas import (
    AssemblyResponse,
    CreateAssemblyRequest,
    FromTextRequest,
    FromTextResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/cad", tags=["cad"])


async def _build_spec(spec: CreateAssemblyRequest) -> dict[str, object]:
    """Validate + author + commit a spec; return the recorder result.

    Raises ``HTTPException`` (400 for an infeasible spec, 502 for a build
    failure). Shared by the declarative and natural-language entry points.
    """
    from api_gateway.chat.routes import get_mcp_bridge
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.twin.geometry_recorder import make_geometry_recorder
    from api_gateway.twin.routes import get_twin

    parts = [p.model_dump() for p in spec.parts]
    # Pre-flight: reject geometrically-impossible specs with a clear 400 rather
    # than a mid-build 502 (e.g. a fillet radius >= half the part thickness).
    spec_errors = validate_assembly_spec(parts)
    if spec_errors:
        raise HTTPException(status_code=400, detail="; ".join(spec_errors))

    recorder = make_geometry_recorder(get_twin(), get_project_backend())
    try:
        rec = await build_assembly(
            bridge=get_mcp_bridge(),
            recorder=recorder,
            name=spec.name,
            parts=parts,
            project_id=spec.project_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean 502 with the cause
        logger.warning("cad_assembly_failed", name=spec.name, error=str(exc))
        raise HTTPException(status_code=502, detail=f"assembly authoring failed: {exc}") from exc

    if not rec.get("node_id"):
        raise HTTPException(status_code=502, detail="assembly committed but no node id returned")
    return rec


@router.post("/assembly", response_model=AssemblyResponse, status_code=201)
async def create_assembly(body: CreateAssemblyRequest) -> AssemblyResponse:
    """Author + commit a multi-part assembly from a declarative spec."""
    rec = await _build_spec(body)
    node_id = rec["node_id"]
    return AssemblyResponse(
        node_id=str(node_id),
        model_url=str(rec.get("model_url") or f"/v1/twin/nodes/{node_id}/model"),
        minio_object_key=rec.get("minio_object_key"),  # type: ignore[arg-type]
        content_hash=rec.get("content_hash"),  # type: ignore[arg-type]
        part_count=len(body.parts),
    )


@router.post("/from-text", response_model=FromTextResponse, status_code=201)
async def create_assembly_from_text(body: FromTextRequest) -> FromTextResponse:
    """Compile a plain-English description into a spec (LLM), then build it.

    The LLM only produces the small declarative spec — the geometry itself is
    authored deterministically by the same builder as ``/assembly``, so the
    output is reproducible and the spec is returned for review.
    """
    from api_gateway.chat.harness_backend import run_chat_turn

    async def translate(prompt: str) -> str:
        # No tools: a single translation completion, not an agent tool loop.
        return await run_chat_turn(
            prompt,
            max_steps=2,
            mcp_bridge=None,
            provider=body.provider,
            model=body.model,
            enabled_tools=[],
        )

    try:
        raw_spec = await compile_spec(body.description, translate=translate, name=body.name)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"could not translate description into a spec: {exc}"
        ) from exc

    try:
        spec = CreateAssemblyRequest(
            name=str(raw_spec["name"]),
            parts=raw_spec["parts"],
            project_id=body.project_id,
        )
    except Exception as exc:  # noqa: BLE001 — pydantic validation → clear 422
        raise HTTPException(status_code=422, detail=f"generated spec is invalid: {exc}") from exc

    rec = await _build_spec(spec)
    node_id = rec["node_id"]
    return FromTextResponse(
        node_id=str(node_id),
        model_url=str(rec.get("model_url") or f"/v1/twin/nodes/{node_id}/model"),
        minio_object_key=rec.get("minio_object_key"),  # type: ignore[arg-type]
        content_hash=rec.get("content_hash"),  # type: ignore[arg-type]
        part_count=len(spec.parts),
        spec=spec,
    )
