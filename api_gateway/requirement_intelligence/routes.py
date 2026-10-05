"""Requirement quality API (FORGE-257, gap G-A1).

``GET /v1/requirements/quality`` for the dashboard's Requirements panel:
per-requirement quality flags, conflict pairs, and per-product-type
completeness, from one request (``build_requirement_set_quality_report``).

``POST /v1/requirements/{id}/fix`` wires the panel's "fix with AI" action to
the existing, non-mutating ``RequirementAuthorAgent`` -- it proposes a
rewritten requirement (as a Patch, REFINES-linked to the original), never
applies one directly.

``POST /v1/requirements/constraints`` (FORGE-259, gap G-A3) is the
dashboard's constraint editor -- picks metric/operator/limit/unit/target
node directly, no hand-typed Python expression. Reuses the exact same
``twin.record_constraint_set`` mechanism/validation every MCP caller
already goes through (``api_gateway.twin.constraint_recorder``), just
exposed as a REST route so the dashboard doesn't need an MCP client of
its own for one create action.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api_gateway.requirement_intelligence.linter import RequirementLinter
from api_gateway.requirement_intelligence.matrix import (
    RequirementMatrixRow,
    build_requirement_matrix,
)
from api_gateway.requirement_intelligence.requirement_author import RequirementAuthorAgent
from api_gateway.requirement_intelligence.set_quality import (
    RequirementSetQualityReport,
    build_requirement_set_quality_report,
)
from api_gateway.requirement_intelligence.traceability import (
    TraceabilityAgent,
    TraceabilityCoverage,
)
from api_gateway.twin.constraint_recorder import make_constraint_recorder
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


class RequirementMatrixResponse(BaseModel):
    rows: list[RequirementMatrixRow]
    # FORGE-528: the current constraint set revisions the rows were read
    # from, e.g. ["CS-WIDGET@2"]; empty when no constraint set is itemized.
    revisionRefs: list[str] = []  # noqa: N815


@router.get("/matrix", response_model=RequirementMatrixResponse)
async def get_requirement_matrix(project_id: str) -> RequirementMatrixResponse:
    """FORGE-318: requirements x claims x evidence, one row per real
    requirement -- status (pass/uncertain/fail/no_data/stale) derived live
    from current claim + evidence staleness state, never cached."""
    try:
        pid = UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project_id") from exc
    with tracer.start_as_current_span("requirements.matrix"):
        from api_gateway.twin.requirements_home import current_revisions

        rows = await build_requirement_matrix(_twin, pid)
        refs = [r.ref for r in await current_revisions(_twin, pid, "constraint_set")]
        return RequirementMatrixResponse(rows=rows, revisionRefs=refs)


@router.get("/coverage", response_model=TraceabilityCoverage)
async def get_requirement_coverage(project_id: str) -> TraceabilityCoverage:
    """FORGE-297 (gap G-I1): the 5 traceability coverage percentages
    (needs->requirements, requirements->architecture, requirements->
    verification, verification->evidence, critical_requirements->evidence),
    computed live by ``TraceabilityAgent`` (FORGE-56/73) -- previously real,
    tested code with no gateway route exposing it at all."""
    try:
        pid = UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project_id") from exc
    with tracer.start_as_current_span("requirements.coverage"):
        agent = TraceabilityAgent(_twin)
        return await agent.coverage(str(pid))


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


class CreateConstraintRequest(BaseModel):
    projectId: str  # noqa: N815 -- dashboard contract is camelCase
    name: str
    metric: str
    operator: str = "<="
    limit: float
    unit: str = ""
    targetNodeType: str = ""  # noqa: N815
    message: str = ""
    severity: str = "error"
    # FORGE-258 (gap G-A2): the requirement-verification declaration.
    verificationMethod: str = ""  # noqa: N815
    expectedEvidence: str = ""  # noqa: N815


class CreateConstraintResponse(BaseModel):
    constraintId: str  # noqa: N815
    setWorkProductId: str | None  # noqa: N815


@router.post("/constraints", response_model=CreateConstraintResponse)
async def create_constraint(payload: CreateConstraintRequest) -> CreateConstraintResponse:
    """FORGE-259: create one structured constraint (metric/operator/limit/
    unit/target_node_type) from the dashboard's constraint editor. Live
    pass/fail/no_data status for it then comes from the existing
    GET /v1/requirements/matrix, computed from real Claim/Evidence data --
    this route only ever records the requirement's own declaration."""
    record = make_constraint_recorder(_twin)
    try:
        result = await record(
            title=payload.name,
            constraints=[
                {
                    "name": payload.name,
                    "metric": payload.metric,
                    "operator": payload.operator,
                    "limit": payload.limit,
                    "unit": payload.unit,
                    "target_node_type": payload.targetNodeType,
                    "message": payload.message,
                    "severity": payload.severity,
                    "verification_method": payload.verificationMethod,
                    "expected_evidence": payload.expectedEvidence,
                }
            ],
            project_id=payload.projectId,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "constraint_created_via_dashboard",
        project_id=payload.projectId,
        name=payload.name,
        metric=payload.metric,
    )
    return CreateConstraintResponse(
        constraintId=result["constraint_ids"][0],
        setWorkProductId=result.get("node_id"),
    )
