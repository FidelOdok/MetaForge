"""Parametric feature library API (FORGE-269, gap G-D1).

``POST /v1/features/generate`` is a thin REST wrapper over the
``generate_parametric_feature`` skill
(``domain_agents.mechanical.skills.generate_parametric_feature``) -- the
same skill the chat harness already reaches via its own skill-discovery
path (``api_gateway.chat.skill_tools``), exposed here so the dashboard's
feature-library panel doesn't need a chat turn for one create action
(same precedent as ``POST /v1/design-loop/start``, ``POST /v1/promotion/
attempt``).

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

from domain_agents.mechanical.skills.generate_parametric_feature.handler import (
    GenerateParametricFeatureHandler,
)
from domain_agents.mechanical.skills.generate_parametric_feature.schema import (
    GenerateParametricFeatureInput,
)
from observability.tracing import get_tracer
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI

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
