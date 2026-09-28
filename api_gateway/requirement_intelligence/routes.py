"""Requirement quality API (FORGE-257, gap G-A1).

``GET /v1/requirements/quality`` for the dashboard's Requirements panel:
per-requirement quality flags, conflict pairs, and per-product-type
completeness, from one request (``build_requirement_set_quality_report``).

``POST /v1/requirements/{id}/fix`` wires the panel's "fix with AI" action to
the existing, non-mutating ``RequirementAuthorAgent`` -- it proposes a
rewritten requirement (as a Patch, REFINES-linked to the original), never
applies one directly.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api_gateway.requirement_intelligence.linter import RequirementLinter
from api_gateway.requirement_intelligence.requirement_author import RequirementAuthorAgent
from api_gateway.requirement_intelligence.set_quality import (
    RequirementSetQualityReport,
    build_requirement_set_quality_report,
)
from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.requirement_intelligence.routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("requirement_routes_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/requirements", tags=["requirements"])


@router.get("/quality", response_model=RequirementSetQualityReport)
async def get_requirement_quality(
    project_id: str, product_type: str = "generic"
) -> RequirementSetQualityReport:
    try:
        pid = UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project_id") from exc
    with tracer.start_as_current_span("requirements.quality") as span:
        span.set_attribute("requirements.product_type", product_type)
        return await build_requirement_set_quality_report(_twin, pid, product_type)


class FixRequirementResponse(BaseModel):
    proposedText: str | None  # noqa: N815 -- dashboard contract is camelCase
    rationale: str
    conclusions: list[str]


@router.post("/{requirement_id}/fix", response_model=FixRequirementResponse)
async def propose_requirement_fix(requirement_id: str) -> FixRequirementResponse:
    """Generate a proposed rewrite for one flawed requirement -- diagnoses
    with the same deterministic linter the quality report already used,
    then asks the Requirement Author agent for a corrected version linked
    back to the original via REFINES. Never writes anything itself; the
    caller applies the resulting patch through the normal review path."""
    try:
        rid = UUID(requirement_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid requirement_id") from exc

    constraint = await _twin.get_constraint(rid)
    if constraint is None:
        raise HTTPException(status_code=404, detail=f"Requirement {requirement_id} not found")

    text = constraint.message or constraint.name
    findings = RequirementLinter().lint(text)
    rationale = (
        "; ".join(f"{f.category.value}: {f.detail}" for f in findings)
        if findings
        else "no lint issues found -- ask for a general clarity pass"
    )

    with tracer.start_as_current_span("requirements.propose_fix") as span:
        span.set_attribute("requirements.requirement_id", requirement_id)
        agent = RequirementAuthorAgent()
        result = await agent.author(
            text,
            context=f"Rewrite this requirement to fix: {rationale}",
            project_id=str(constraint.project_id) if constraint.project_id else None,
            parent_ref=str(rid),
            relation="refines",
        )

    logger.info("requirement_fix_proposed", requirement_id=requirement_id, rationale=rationale)
    return FixRequirementResponse(
        proposedText=result.conclusions[0] if result.conclusions else None,
        rationale=rationale,
        conclusions=result.conclusions,
    )
