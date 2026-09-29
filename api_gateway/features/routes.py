"""Parametric feature library API (FORGE-269, gap G-D1; FORGE-270, gap G-D2).

``POST /v1/features/generate`` is a thin REST wrapper over the
``generate_parametric_feature`` skill
(``domain_agents.mechanical.skills.generate_parametric_feature``) -- the
same skill the chat harness already reaches via its own skill-discovery
path (``api_gateway.chat.skill_tools``), exposed here so the dashboard's
feature-library panel doesn't need a chat turn for one create action
(same precedent as ``POST /v1/design-loop/start``, ``POST /v1/promotion/
attempt``).

``GET /v1/features/{work_product_id}/diff`` is FORGE-270's own "editable
parameters on committed parts" -- "editing" a feature is calling
``POST /v1/features/generate`` again with the SAME name and a CHANGED
parameter; ``api_gateway.twin.geometry_recorder`` already links the
resulting node to its predecessor via a real ``SUPERSEDES`` edge (same
same-name matching every other re-commit in this codebase already gets),
and now also threads ``parameters`` into both nodes'
``metadata.geometry_features.parameters`` (this ticket's own addition to
``generate_cad_ir``). This route walks that ONE real edge back and diffs
the two nodes' parameters -- no new versioning system, reusing
``api_gateway.twin.version_schemas.FieldDelta`` for the diff shape rather
than inventing a second one.

The real MCP bridge (``request.app.state.mcp_bridge``) is only populated
once the gateway's startup lifespan finishes -- by the time any HTTP
request is actually served, that has already happened, so this route
reads it directly rather than needing a lazy-binding placeholder object.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from api_gateway.twin.version_schemas import FieldDelta
from domain_agents.mechanical.skills.generate_parametric_feature.handler import (
    GenerateParametricFeatureHandler,
)
from domain_agents.mechanical.skills.generate_parametric_feature.schema import (
    GenerateParametricFeatureInput,
)
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, NodeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.features.routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("features_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/features", tags=["features"])


class GenerateFeatureRequest(BaseModel):
    name: str
    workProductId: str | None = None  # noqa: N815 -- dashboard contract is camelCase
    feature: dict[str, Any]
    adapter: str = "freecad"
    material: str = "aluminum_6061"
    projectId: str | None = None  # noqa: N815
    commit: bool = True


@router.post("/generate")
async def generate_feature(payload: GenerateFeatureRequest, request: Request) -> dict[str, Any]:
    mcp_bridge = getattr(request.app.state, "mcp_bridge", None)
    if mcp_bridge is None:
        raise HTTPException(status_code=503, detail="MCP bridge is not available yet")

    with tracer.start_as_current_span("features.generate") as span:
        feature_type = payload.feature.get("feature_type")
        span.set_attribute("feature.type", str(feature_type))
        try:
            skill_input = GenerateParametricFeatureInput(
                name=payload.name,
                work_product_id=UUID(payload.workProductId) if payload.workProductId else None,
                feature=payload.feature,  # type: ignore[arg-type]
                adapter=payload.adapter,  # type: ignore[arg-type]
                material=payload.material,
                project_id=payload.projectId,
                commit=payload.commit,
            )
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ctx = SkillContext(
            twin=_twin,
            mcp=mcp_bridge,
            logger=logger,
            session_id=uuid4(),
            branch="main",
            domain="mechanical",
        )
        result = await GenerateParametricFeatureHandler(ctx).run(skill_input)
        if not result.success:
            raise HTTPException(status_code=400, detail="; ".join(result.errors))

    logger.info(
        "parametric_feature_generated_via_dashboard",
        feature_type=feature_type,
        name=payload.name,
    )
    assert result.data is not None
    return result.data.model_dump(mode="json")


class FeatureDiffResponse(BaseModel):
    currentWorkProductId: str  # noqa: N815 -- dashboard contract is camelCase
    previousWorkProductId: str  # noqa: N815
    changed: dict[str, FieldDelta]
    added: dict[str, Any]
    removed: dict[str, Any]


@router.get("/{work_product_id}/diff", response_model=FeatureDiffResponse)
async def get_feature_diff(work_product_id: str) -> FeatureDiffResponse:
    try:
        node_id = UUID(work_product_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid work_product_id") from exc

    current = await _twin.graph.get_node(node_id)
    if current is None or current.node_type != NodeType.WORK_PRODUCT:
        raise HTTPException(status_code=404, detail=f"work product {work_product_id} not found")

    edges = await _twin.get_edges(node_id, direction="outgoing", edge_type=EdgeType.SUPERSEDES)
    if not edges:
        raise HTTPException(
            status_code=404,
            detail=f"work product {work_product_id} has no prior version (no SUPERSEDES edge)",
        )
    previous = await _twin.graph.get_node(edges[0].target_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="superseded work product no longer exists")

    params_a = (previous.metadata.get("geometry_features") or {}).get("parameters") or {}
    params_b = (current.metadata.get("geometry_features") or {}).get("parameters") or {}
    keys_a, keys_b = set(params_a), set(params_b)

    changed: dict[str, FieldDelta] = {}
    for key in keys_a & keys_b:
        if params_a[key] != params_b[key]:
            changed[key] = FieldDelta(from_value=params_a[key], to_value=params_b[key])
    added = {k: params_b[k] for k in keys_b - keys_a}
    removed = {k: params_a[k] for k in keys_a - keys_b}

    return FeatureDiffResponse(
        currentWorkProductId=work_product_id,
        previousWorkProductId=str(previous.id),
        changed=changed,
        added=added,
        removed=removed,
    )
