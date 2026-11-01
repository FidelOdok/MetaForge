"""Requirement-set quality report (FORGE-257, gap G-A1).

Combines the per-requirement diagnostics ``RequirementCriticAgent`` already
runs (spec sections 31/32) with the two set-level checks that agent
explicitly doesn't attempt on its own: real conflict detection
(``RequirementConflictDetector``, FORGE-257's own fill of the linter's
documented ``contradictory_requirement`` gap) and per-product-type
completeness (``completeness.py``). One pass over a project's real
(non-candidate) requirements builds all three together, so the dashboard's
Requirements panel can render them from a single request.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from api_gateway.requirement_intelligence.completeness import (
    CompletenessResult,
    check_completeness,
    load_checklist,
)
from api_gateway.requirement_intelligence.conflict_detector import RequirementConflictDetector
from api_gateway.requirement_intelligence.linter import RequirementLinter
from api_gateway.requirement_intelligence.quality import build_quality_record
from twin_core.api import TwinAPI
from twin_core.models.enums import EdgeType

# The same trace-edge vocabulary traceability.py already established for
# "this requirement points at something" -- reused here rather than
# reinvented so the traceability quality axis agrees with the Traceability
# Agent's own REQUIREMENT_WITHOUT_PARENT check.
_TRACE_EDGE_TYPES = {
    EdgeType.IMPLEMENTS,
    EdgeType.DERIVES_FROM,
    EdgeType.SATISFIES,
    EdgeType.MOTIVATES,
    EdgeType.REFINES,
    EdgeType.VERIFIED_BY,
}


class RequirementRecordView(BaseModel):
    """One requirement's identity + quality diagnostics, dashboard-facing
    camelCase over the reused, already-shipped ``RequirementQualityRecord``
    (left untouched -- its own field names stay snake_case internally)."""

    id: str
    name: str
    text: str
    severity: str
    clarity: str
    atomicity: str
    quantified: str
    traceability: str | None
    verificationReady: str  # noqa: N815 -- dashboard contract is camelCase
    conflicts: list[str] = Field(default_factory=list)


class ConflictPairView(BaseModel):
    aId: str  # noqa: N815 -- dashboard contract is camelCase
    aName: str  # noqa: N815
    bId: str  # noqa: N815
    bName: str  # noqa: N815
    detail: str


class CompletenessView(BaseModel):
    """Dashboard-facing camelCase over the reused ``CompletenessResult``
    (left untouched -- its own field names stay snake_case internally)."""

    productType: str  # noqa: N815 -- dashboard contract is camelCase
    covered: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: CompletenessResult) -> CompletenessView:
        return cls(productType=result.product_type, covered=result.covered, missing=result.missing)


class RequirementSetQualityReport(BaseModel):
    requirements: list[RequirementRecordView] = Field(default_factory=list)
    conflicts: list[ConflictPairView] = Field(default_factory=list)
    completeness: CompletenessView


async def build_requirement_set_quality_report(
    twin: TwinAPI, project_id: UUID, product_type: str
) -> RequirementSetQualityReport:
    linter = RequirementLinter()
    detector = RequirementConflictDetector()

    constraints = await twin.list_constraints(project_id=project_id)
    # "candidate" constraints (FORGE-54/55's not-yet-reviewed proposals)
    # aren't real requirements yet -- same exclusion traceability.py already
    # applies, so a generated-but-unreviewed proposal doesn't get flagged
    # for quality/conflicts/completeness before a human has looked at it.
    requirements = [c for c in constraints if not c.metadata.get("candidate")]

    texts_by_id = {c.id: (c.message or c.name) for c in requirements}
    names_by_id = {c.id: c.name for c in requirements}

    conflict_findings = detector.detect(list(texts_by_id.items()))
    conflicts_by_req: dict[UUID, list[str]] = {}
    for finding in conflict_findings:
        a_id, b_id = UUID(finding.requirement_a_id), UUID(finding.requirement_b_id)
        conflicts_by_req.setdefault(a_id, []).append(str(b_id))
        conflicts_by_req.setdefault(b_id, []).append(str(a_id))
        await _persist_conflict_edge(twin, a_id, b_id, finding.detail)

    records: list[RequirementRecordView] = []
    for constraint in requirements:
        text = texts_by_id[constraint.id]
        findings = linter.lint(text)
        out_edges = await twin.get_edges(constraint.id, direction="outgoing")
        has_parent = bool({e.edge_type for e in out_edges} & _TRACE_EDGE_TYPES)
        quality = build_quality_record(
            findings,
            has_parent=has_parent,
            conflicts=conflicts_by_req.get(constraint.id, []),
        )
        records.append(
            RequirementRecordView(
                id=str(constraint.id),
                name=constraint.name,
                text=text,
                severity=constraint.severity.value,
                clarity=quality.clarity,
                atomicity=quality.atomicity,
                quantified=quality.quantified,
                traceability=quality.traceability,
                verificationReady=quality.verification_ready,
                conflicts=quality.conflicts,
            )
        )

    conflict_pairs = [
        ConflictPairView(
            aId=finding.requirement_a_id,
            aName=names_by_id.get(UUID(finding.requirement_a_id), finding.requirement_a_id),
            bId=finding.requirement_b_id,
            bName=names_by_id.get(UUID(finding.requirement_b_id), finding.requirement_b_id),
            detail=finding.detail,
        )
        for finding in conflict_findings
    ]

    checklist = load_checklist(product_type)
    completeness = check_completeness(list(texts_by_id.values()), checklist)

    return RequirementSetQualityReport(
        requirements=records,
        conflicts=conflict_pairs,
        completeness=CompletenessView.from_result(completeness),
    )


async def _persist_conflict_edge(twin: TwinAPI, a_id: UUID, b_id: UUID, detail: str) -> None:
    """Record a detected conflict as a real ``CONFLICTS_WITH`` edge -- the
    existing relation vocabulary (FORGE-43), not a new one -- so it's
    visible to any other graph consumer, not just this report. Idempotent:
    skips creating a duplicate on repeat calls."""
    existing = await twin.get_edges(a_id, direction="outgoing", edge_type=EdgeType.CONFLICTS_WITH)
    if any(e.target_id == b_id for e in existing):
        return
    await twin.add_edge(a_id, b_id, EdgeType.CONFLICTS_WITH, metadata={"detail": detail})
