"""Compliance API routes.

Provides REST endpoints for compliance checklist generation,
evidence linking, and coverage reporting.

Endpoints live under ``/api/v1/compliance``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from domain_agents.compliance.agent import ComplianceAgent
from domain_agents.compliance.models import (
    ComplianceRegime,
    EvidenceType,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.compliance")

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

# Module-level agent instance (stateful in-memory for now)
_agent = ComplianceAgent()


def _get_agent() -> ComplianceAgent:
    """Return the module-level compliance agent."""
    return _agent


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class LinkEvidenceRequest(BaseModel):
    """Request body for linking evidence to a checklist item."""

    checklist_item_id: str = Field(..., description="Checklist item ID to link to")
    evidence_type: EvidenceType = Field(..., description="Type of evidence")
    title: str = Field(..., description="Evidence title")
    description: str = Field(default="", description="Evidence description")
    work_product_id: UUID | None = Field(default=None, description="WorkProduct UUID")


class ChecklistResponse(BaseModel):
    """Response for checklist generation."""

    project_id: str
    target_markets: list[str]
    total_items: int
    evidenced_items: int
    coverage_percent: float
    items: list[dict[str, Any]]


class EvidenceResponse(BaseModel):
    """Response for evidence operations."""

    id: str
    checklist_item_id: str
    evidence_type: str
    status: str
    title: str
    description: str
    uploaded_at: str


class CoverageResponse(BaseModel):
    """Response for coverage queries."""

    project_id: str
    total_items: int
    evidenced_items: int
    coverage_percent: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{project_id}/checklist", response_model=ChecklistResponse)
async def get_checklist(
    project_id: str,
    markets: str = Query(default="UKCA,CE", description="Comma-separated regime codes"),
) -> ChecklistResponse:
    """Generate a compliance checklist for the given project and markets.

    Markets are provided as a comma-separated query parameter, e.g.
    ``?markets=UKCA,CE,FCC``.
    """
    with tracer.start_as_current_span("api.compliance.get_checklist") as span:
        span.set_attribute("project_id", project_id)

        market_list: list[str] = [m.strip() for m in markets.split(",") if m.strip()]
        try:
            regimes = [ComplianceRegime(m) for m in market_list]
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid market regime: {exc}. "
                f"Valid: {', '.join(r.value for r in ComplianceRegime)}",
            )

        agent = _get_agent()
        from domain_agents.compliance.agent import ComplianceTaskRequest

        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id=project_id,
                parameters={"markets": [r.value for r in regimes]},
            )
        )

        if not result.success:
            raise HTTPException(status_code=500, detail="; ".join(result.errors))

        checklist_data = result.data.get("checklist", {})

        return ChecklistResponse(
            project_id=project_id,
            target_markets=market_list,
            total_items=result.total_requirements,
            evidenced_items=result.evidenced_count,
            coverage_percent=result.coverage_percent,
            items=checklist_data.get("items", []),
        )


@router.post("/{project_id}/evidence", response_model=EvidenceResponse)
async def link_evidence(
    project_id: str,
    body: LinkEvidenceRequest,
) -> EvidenceResponse:
    """Link a piece of evidence to a compliance checklist item."""
    with tracer.start_as_current_span("api.compliance.link_evidence") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("checklist_item_id", body.checklist_item_id)

        agent = _get_agent()
        from domain_agents.compliance.agent import ComplianceTaskRequest

        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="link_evidence",
                project_id=project_id,
                parameters={
                    "checklist_item_id": body.checklist_item_id,
                    "evidence_type": body.evidence_type.value,
                    "title": body.title,
                    "description": body.description,
                },
            )
        )

        if not result.success:
            raise HTTPException(status_code=400, detail="; ".join(result.errors))

        ev = result.data.get("evidence", {})

        return EvidenceResponse(
            id=str(ev.get("id", "")),
            checklist_item_id=ev.get("checklist_item_id", ""),
            evidence_type=ev.get("evidence_type", ""),
            status=ev.get("status", ""),
            title=ev.get("title", ""),
            description=ev.get("description", ""),
            uploaded_at=str(ev.get("uploaded_at", "")),
        )


@router.get("/{project_id}/evidence/{item_id}", response_model=list[EvidenceResponse])
async def get_evidence(
    project_id: str,
    item_id: str,
) -> list[EvidenceResponse]:
    """Retrieve all evidence records for a checklist item."""
    with tracer.start_as_current_span("api.compliance.get_evidence") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("item_id", item_id)

        agent = _get_agent()
        records = agent.tracker.get_evidence_for_item(item_id)

        return [
            EvidenceResponse(
                id=str(r.id),
                checklist_item_id=r.checklist_item_id,
                evidence_type=r.evidence_type.value,
                status=r.status.value,
                title=r.title,
                description=r.description,
                uploaded_at=r.uploaded_at.isoformat(),
            )
            for r in records
        ]


@router.get("/{project_id}/coverage", response_model=CoverageResponse)
async def get_coverage(project_id: str) -> CoverageResponse:
    """Get evidence coverage statistics for a project."""
    with tracer.start_as_current_span("api.compliance.get_coverage") as span:
        span.set_attribute("project_id", project_id)

        agent = _get_agent()
        from domain_agents.compliance.agent import ComplianceTaskRequest

        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="get_coverage",
                project_id=project_id,
            )
        )

        if not result.success:
            raise HTTPException(status_code=404, detail="; ".join(result.errors))

        return CoverageResponse(
            project_id=project_id,
            total_items=result.total_requirements,
            evidenced_items=result.evidenced_count,
            coverage_percent=result.coverage_percent,
        )
