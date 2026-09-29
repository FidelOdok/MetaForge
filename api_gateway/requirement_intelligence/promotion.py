"""Maturity-gate promotion attempts (FORGE-319, target lifecycle spec §29
MaturityGate, step 17).

``attempt_promotion`` is the first place in this codebase a gate actually
REFUSES rather than only reporting -- ``twin_core.consistency.gates``'s
G3-G8 design-flow gates are a different, pre-existing concept, and (per
that module's own docstring) never block a transition, only inform a human
reviewer. Scoped narrowly here: this ONE new function checks its own
required claims and either persists a real ``promoted=True`` MaturityGate
or refuses (still persists the attempt, with ``promoted=False`` and
exactly which required claim(s) blocked it) -- no change to ``gates.py``
itself.

Reuses, rather than re-derives:

- **Margin/band decision** -- ``api_gateway.requirement_intelligence.
  matrix.build_requirement_matrix`` (FORGE-318) already computes
  pass/uncertain/fail/no_data/stale per requirement from real Claim +
  Evidence data. A MaturityGate's ``required_claim_ids`` are simply the
  subset of a project's requirement (Constraint) ids that must each
  resolve to ``pass`` (or an approved waiver covering a ``fail``).
- **Waiver approval** -- the existing ``entity_type="waiver"`` /
  ``twin.approve_engineering_entity`` mechanism (FORGE-73), the same
  ``list_engineering_entities(..., entity_type="waiver")`` +
  ``authority in (APPROVED, BASELINED)`` pattern
  ``twin_core.consistency.gates``'s own G8 ``_evaluate_waivers_check``
  already uses -- scoped here to a waiver whose ``parent_refs`` names the
  SPECIFIC blocked requirement (G8's own check is a blanket "any waivers
  outstanding" scan; promotion needs per-requirement specificity, since
  only a mass-specific waiver should unblock mass, not an unrelated one).
- **Human authority** -- a caller-supplied ``decided_by`` string, the same
  agent-asserted-identity trust level every ``created_by``/ECT
  ``approver`` field in this codebase already carries (FORGE-66's own
  ``ect.py``'s ``approve``/``commit``), not a second authentication
  concept.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from api_gateway.requirement_intelligence.matrix import build_requirement_matrix
from observability.tracing import get_tracer
from twin_core.consistency.gates import APPROVED_AUTHORITY
from twin_core.models.maturity_gate import (
    SATISFIED_DECISIONS,
    MaturityGate,
    MaturityLevel,
    RequiredClaimDecision,
    RequiredClaimResult,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.requirement_intelligence.promotion")

_MATRIX_STATUS_TO_DECISION: dict[str, RequiredClaimDecision] = {
    "pass": RequiredClaimDecision.PASS,
    "uncertain": RequiredClaimDecision.UNCERTAIN,
    "stale": RequiredClaimDecision.UNCERTAIN,
    "no_data": RequiredClaimDecision.FAIL,
    "fail": RequiredClaimDecision.FAIL,
}


async def _find_covering_waiver(twin: Any, project_id: UUID, requirement_id: UUID) -> UUID | None:
    """An APPROVED waiver whose parent_refs names this exact requirement --
    same authority check as G8's own _evaluate_waivers_check, scoped to
    one requirement rather than a blanket scan."""
    entities = await twin.list_engineering_entities(project_id=project_id, entity_type="waiver")
    for entity in entities:
        if str(requirement_id) not in entity.parent_refs:
            continue
        if entity.authority in APPROVED_AUTHORITY:
            return entity.id
    return None


async def attempt_promotion(
    twin: Any,
    *,
    project_id: str,
    level: str,
    required_claim_ids: list[str],
    k: float = 1.0,
    decided_by: str | None = None,
    comment: str | None = None,
    reject: bool = False,
) -> dict[str, Any]:
    """Evaluate ``required_claim_ids`` against live evidence and either
    promote or refuse (see module docstring). ``reject=True`` is the
    ticket's own "approve/reject with comment" human-veto path (FORGE-290,
    gap G-G4): a reviewer can refuse a promotion EVEN IF every required
    claim is satisfied -- the evidence-gate logic above is a necessary
    condition for promotion, never a sufficient one that overrides a
    human's own judgement. Requires ``decided_by`` (who rejected it), the
    same honesty precedent as promoting requiring a human identity.
    """
    if reject and not decided_by:
        raise ValueError("attempt_promotion: reject=True requires decided_by (who rejected it)")
    with tracer.start_as_current_span("twin.attempt_promotion") as span:
        pid = UUID(project_id)
        span.set_attribute("promotion.level", level)
        span.set_attribute("promotion.required_claim_count", len(required_claim_ids))
        span.set_attribute("promotion.reject", reject)

        matrix_rows = await build_requirement_matrix(twin, pid)
        rows_by_id = {row.requirementId: row for row in matrix_rows}

        results: list[RequiredClaimResult] = []
        for raw_id in required_claim_ids:
            row = rows_by_id.get(raw_id)
            if row is None:
                results.append(
                    RequiredClaimResult(
                        requirement_id=UUID(raw_id),
                        requirement_name=raw_id,
                        decision=RequiredClaimDecision.FAIL,
                        detail="requirement not found on this project",
                    )
                )
                continue

            decision = _MATRIX_STATUS_TO_DECISION[row.status]
            detail = row.detail
            waiver_id: UUID | None = None

            if decision == RequiredClaimDecision.FAIL and row.status == "fail":
                waiver_id = await _find_covering_waiver(twin, pid, UUID(raw_id))
                if waiver_id is not None:
                    decision = RequiredClaimDecision.WAIVED
                    detail = f"{row.detail} (waived: {waiver_id})"

            results.append(
                RequiredClaimResult(
                    requirement_id=UUID(raw_id),
                    requirement_name=row.requirementName,
                    decision=decision,
                    detail=detail,
                    waiver_id=waiver_id,
                )
            )

        blocking = [r for r in results if r.decision not in SATISFIED_DECISIONS]

        promoted = False
        blocked_reason: str | None = None
        if reject:
            blocked_reason = comment or f"rejected by {decided_by}"
        elif blocking:
            blocked_reason = "; ".join(
                f"{r.requirement_name} ({r.decision.value}): {r.detail}" for r in blocking
            )
        elif not decided_by:
            blocked_reason = (
                "all required claims satisfied, but promotion requires human authority "
                "(decided_by) -- not automatically granted"
            )
        else:
            promoted = True

        gate = MaturityGate(
            level=MaturityLevel(level),
            project_id=pid,
            required_claim_ids=[UUID(c) for c in required_claim_ids],
            results=results,
            promoted=promoted,
            blocked_reason=blocked_reason,
            # Recorded whether promoted or blocked -- "who made this call"
            # matters for a refusal too, not only a grant.
            decided_by=decided_by,
            comment=comment,
            k=k,
        )
        created = await twin.create_maturity_gate(gate)

        logger.info(
            "maturity_gate_attempted",
            gate_id=str(created.id),
            project_id=project_id,
            level=level,
            promoted=promoted,
            blocked_reason=blocked_reason,
            reject=reject,
        )

        return {
            "gate_id": str(created.id),
            "level": level,
            "promoted": promoted,
            "blocked_reason": blocked_reason,
            "decided_by": decided_by,
            "comment": comment,
            "results": [
                {
                    "requirementId": str(r.requirement_id),
                    "requirementName": r.requirement_name,
                    "decision": r.decision.value,
                    "detail": r.detail,
                    "waiverId": str(r.waiver_id) if r.waiver_id else None,
                }
                for r in results
            ],
        }
