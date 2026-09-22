"""TraceabilityAgent (FORGE-56, spec sections 26.9 Traceability Agent, 33
Traceability Coverage, 64 Audit Log).

Unlike FORGE-54/55's agents, this one reads the live Twin graph -- it's a
continuous check over existing nodes/edges, not a text-analysis or
LLM-generation pass. Every check below reuses a real, already-established
edge/field convention from this epic's earlier work rather than inventing
a new one:

- "parent"/traceability edges: the FORGE-43 relation vocabulary
  (IMPLEMENTS/DERIVES_FROM/SATISFIES/MOTIVATES/REFINES/VERIFIED_BY) --
  whatever relation a recorder used, any of these counts as "linked".
- "rationale"/"verification_method": the exact metadata keys
  RequirementAuthorAgent (FORGE-55) already writes.
- "owner": Constraint has no dedicated owner field -- `source` (who/what
  produced it: "requirement_author_agent", "user", ...) is the closest
  real field this codebase has, reused rather than inventing a new one.
- artefact <-> requirement justification: the existing CONSTRAINED_BY edge
  ConstraintEngine.add_constraint already creates between a Constraint and
  the WorkProduct(s) it binds.

Two deliberate scope limits, stated plainly:

- Coverage/link checks that involve "evidence" (Verification -> Evidence,
  Critical Requirements -> Evidence) only check a DIRECT edge from the
  verification_case/requirement to an evidence entity -- they do not chase
  a multi-hop path (e.g. requirement -> verification_case -> evidence).
  Multi-hop impact traversal is Phase 4/7 (Engineering Consistency /
  Advanced Change Management) territory, not built here.
- The "audit questions" the spec lists (who approved a change, what
  evidence supported an old state, why is a gate stale) need a real,
  persisted audit/change log this codebase doesn't have yet -- FORGE-51's
  RevisionSnapshot answers "what did this look like before" but nothing
  records WHO approved a transition or WHY a gate went stale. This agent
  answers what it honestly can from existing fields (created_by/source/
  rationale/metadata on the CURRENT and past-revision state); a full audit
  log is a separate, larger piece of work, not guessed into this pass.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from api_gateway.requirement_intelligence.models import AgentResult
from twin_core.api import TwinAPI
from twin_core.models.enums import ConstraintSeverity, EdgeType, WorkProductType

# Any of these counts as "this entity traces to something" -- the exact
# relation a recorder chose doesn't matter for a presence check.
_TRACE_EDGE_TYPES = {
    EdgeType.IMPLEMENTS,
    EdgeType.DERIVES_FROM,
    EdgeType.SATISFIES,
    EdgeType.MOTIVATES,
    EdgeType.REFINES,
    EdgeType.VERIFIED_BY,
}


class TraceabilityCategory(StrEnum):
    REQUIREMENT_WITHOUT_PARENT = "requirement_without_parent"
    REQUIREMENT_WITHOUT_RATIONALE = "requirement_without_rationale"
    REQUIREMENT_WITHOUT_OWNER = "requirement_without_owner"
    REQUIREMENT_WITHOUT_VERIFICATION = "requirement_without_verification"
    NEED_WITHOUT_REQUIREMENT = "need_without_requirement"
    VERIFICATION_WITHOUT_REQUIREMENT = "verification_without_requirement"
    ARTEFACT_WITHOUT_REQUIREMENT = "artefact_without_requirement_justification"
    EVIDENCE_WITHOUT_PROVENANCE = "evidence_without_provenance"


class TraceabilityFinding(BaseModel):
    category: TraceabilityCategory
    subject_id: UUID
    subject_label: str


class TraceabilityCoverage(BaseModel):
    """Percentages (0-100), spec section 33. `None` when the denominator is
    zero -- an empty set has no meaningful coverage ratio, not 0% or 100%."""

    needs_to_requirements: float | None = None
    requirements_to_architecture: float | None = None
    requirements_to_verification: float | None = None
    verification_to_evidence: float | None = None
    critical_requirements_to_evidence: float | None = None


def _pct(covered: int, total: int) -> float | None:
    return None if total == 0 else round(100.0 * covered / total, 1)


class TraceabilityAgent:
    """Continuous graph checks (spec section 26.9) -- reads the Twin, never
    writes it (there is nothing to propose here, `proposed_patch` is
    always None)."""

    def __init__(self, twin: TwinAPI) -> None:
        self._twin = twin

    async def coverage(self, project_id: str) -> TraceabilityCoverage:
        """The reusable accessor FORGE-73 adds: the same structured
        ``TraceabilityCoverage`` ``check()`` already computed internally,
        exposed directly instead of only stringified inside
        ``AgentResult.evidence``. This is what ``twin_core.consistency.
        gates``'s G6 "requirement coverage" and G8 "required verification
        complete" checks needed -- see gates.py's module docstring for the
        exact field each one reads.
        """
        _, coverage = await self._compute(project_id)
        return coverage

    async def check(self, project_id: str) -> AgentResult:
        findings, coverage = await self._compute(project_id)
        conclusions = [f"{f.category.value}: {f.subject_label}" for f in findings]
        if not findings:
            conclusions.append("no traceability gaps found")

        return AgentResult(
            conclusions=conclusions,
            assumptions=[],
            evidence=[f"traceability_coverage={coverage.model_dump()}"],
            proposed_patch=None,
            unresolved=[],
            confidence=1.0,
        )

    async def _compute(
        self, project_id: str
    ) -> tuple[list[TraceabilityFinding], TraceabilityCoverage]:
        """The full pass over the graph -- shared by ``check()`` and
        ``coverage()`` so neither duplicates the per-requirement/need/
        verification/artefact/evidence edge-walking below."""
        pid = UUID(project_id)
        constraints = await self._twin.list_constraints(project_id=pid)
        entities = await self._twin.list_engineering_entities(project_id=pid)
        work_products = await self._twin.list_work_products(project_id=pid)

        # "candidate" constraints (FORGE-54/55's not-yet-reviewed proposals)
        # aren't real requirements yet -- checking them for missing
        # parent/rationale/owner/verification would just flag every
        # not-yet-reviewed proposal, which isn't a traceability gap.
        requirements = [c for c in constraints if not c.metadata.get("candidate")]
        needs = [e for e in entities if e.entity_type == "stakeholder_need"]
        verifications = [e for e in entities if e.entity_type == "verification_case"]
        evidence_entities = [e for e in entities if e.entity_type == "evidence"]

        findings: list[TraceabilityFinding] = []
        req_has_parent = 0
        req_has_architecture = 0
        req_has_verification = 0
        critical_reqs = 0
        critical_has_evidence = 0

        for req in requirements:
            out_edges = await self._twin.get_edges(req.id, direction="outgoing")
            out_types = {e.edge_type for e in out_edges}

            has_parent = bool(out_types & _TRACE_EDGE_TYPES)
            if has_parent:
                req_has_parent += 1
            else:
                findings.append(
                    _finding(TraceabilityCategory.REQUIREMENT_WITHOUT_PARENT, req.id, req.name)
                )

            if not req.metadata.get("rationale"):
                findings.append(
                    _finding(TraceabilityCategory.REQUIREMENT_WITHOUT_RATIONALE, req.id, req.name)
                )

            if not req.source:
                findings.append(
                    _finding(TraceabilityCategory.REQUIREMENT_WITHOUT_OWNER, req.id, req.name)
                )

            has_verification = bool(req.metadata.get("verification_method"))
            if has_verification:
                req_has_verification += 1
            else:
                findings.append(
                    _finding(
                        TraceabilityCategory.REQUIREMENT_WITHOUT_VERIFICATION, req.id, req.name
                    )
                )

            if await self._has_architecture_binding(req.id):
                req_has_architecture += 1

            if req.severity == ConstraintSeverity.ERROR:
                critical_reqs += 1
                if await self._has_direct_evidence_edge(req.id):
                    critical_has_evidence += 1

        for need in needs:
            in_edges = await self._twin.get_edges(need.id, direction="incoming")
            in_types = {e.edge_type for e in in_edges}
            if not in_types & _TRACE_EDGE_TYPES:
                findings.append(
                    _finding(
                        TraceabilityCategory.NEED_WITHOUT_REQUIREMENT,
                        need.id,
                        need.title or (need.statement or "")[:60],
                    )
                )

        verification_has_evidence = 0
        for v in verifications:
            all_edges = (await self._twin.get_edges(v.id, direction="outgoing")) + (
                await self._twin.get_edges(v.id, direction="incoming")
            )
            if not all_edges:
                findings.append(
                    _finding(
                        TraceabilityCategory.VERIFICATION_WITHOUT_REQUIREMENT,
                        v.id,
                        v.title or (v.statement or "")[:60],
                    )
                )
            if await self._has_direct_evidence_edge(v.id):
                verification_has_evidence += 1

        for wp in work_products:
            incoming = await self._twin.get_edges(
                wp.id, direction="incoming", edge_type=EdgeType.CONSTRAINED_BY
            )
            if not incoming:
                findings.append(
                    _finding(TraceabilityCategory.ARTEFACT_WITHOUT_REQUIREMENT, wp.id, wp.name)
                )

        for ev in evidence_entities:
            if not ev.source_refs:
                findings.append(
                    _finding(
                        TraceabilityCategory.EVIDENCE_WITHOUT_PROVENANCE,
                        ev.id,
                        ev.title or (ev.statement or "")[:60],
                    )
                )

        coverage = TraceabilityCoverage(
            needs_to_requirements=_pct(
                len(needs)
                - sum(
                    1
                    for f in findings
                    if f.category == TraceabilityCategory.NEED_WITHOUT_REQUIREMENT
                ),
                len(needs),
            ),
            requirements_to_architecture=_pct(req_has_architecture, len(requirements)),
            requirements_to_verification=_pct(req_has_verification, len(requirements)),
            verification_to_evidence=_pct(verification_has_evidence, len(verifications)),
            critical_requirements_to_evidence=_pct(critical_has_evidence, critical_reqs),
        )

        return findings, coverage

    async def _has_architecture_binding(self, requirement_id: UUID) -> bool:
        edges = await self._twin.get_edges(
            requirement_id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        for edge in edges:
            wp = await self._twin.get_work_product(edge.target_id)
            if wp is not None and wp.type == WorkProductType.SYSTEM_ARCHITECTURE:
                return True
        return False

    async def _has_direct_evidence_edge(self, node_id: UUID) -> bool:
        for direction in ("outgoing", "incoming"):
            edges = await self._twin.get_edges(node_id, direction=direction)
            for edge in edges:
                other_id = edge.target_id if direction == "outgoing" else edge.source_id
                other = await self._twin.get_engineering_entity(other_id)
                if other is not None and other.entity_type == "evidence":
                    return True
        return False


def _finding(category: TraceabilityCategory, subject_id: UUID, label: str) -> TraceabilityFinding:
    return TraceabilityFinding(category=category, subject_id=subject_id, subject_label=label)
