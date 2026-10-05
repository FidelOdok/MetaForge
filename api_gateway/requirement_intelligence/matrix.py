"""Evidence-backed requirement matrix (FORGE-318, target lifecycle spec
step 16: Requirements x claims x evidence).

One row per real (non-candidate) Constraint on a project, joining:

- **Claims** (FORGE-65, ``twin_core.consistency.claims``) -- which
  artefact(s) claim to satisfy this requirement, resolved via the new
  ``list_claims_for_requirement`` (the one piece genuinely missing before
  this ticket: everything else needed already existed).
- **Evidence** cited by those claims -- read for `method` (``producer.
  tool``), `tier` (``result["tier"]`` -- FORGE-315's tier-0 result already
  carries this; tier-2's raw ``calculix.run_fea`` output didn't until this
  ticket added ``"tier": 2`` to what gets persisted,
  ``api_gateway/twin/metric_evaluator.py``), `value`/`limit`/`margin`
  (either FORGE-315's tier-0/tier-2 shape -- ``value_mm``/``limit_mm``/
  ``margin_mm``/``escalated`` -- or FORGE-317's sensitivity-ranking shape
  -- ``baseline_value``/``limit``/``baseline_margin`` -- both handled, no
  third shape invented), and current **staleness**
  (``twin_core.consistency.staleness.StalenessEngine``).

Status is one of 5 states, derived live every call (same "never a stored,
driftable boolean" discipline FORGE-65's own ``evaluate_claim`` already
established):

- ``no_data``: no claim recorded against this requirement at all.
- ``stale``: a claim's cited evidence includes at least one entity whose
  current staleness is STALE/SUPERSEDED/INVALID -- flagged even when the
  claim itself is still SUPPORTED by some other current evidence, so
  "flags stale evidence after a change" (the ticket's own acceptance
  wording) is never silently hidden by a passing overall claim status.
- ``fail`` / ``uncertain`` / ``pass``: claim SUPPORTED, no stale evidence,
  derived from the most current evidence's own margin -- negative margin
  is ``fail``; a tier-0 result whose own ``escalated`` flag is true is
  ``uncertain`` (the same "too close to call without a higher-fidelity
  check" semantics FORGE-315 already established); otherwise ``pass``.
  A SUPPORTED claim with no extractable margin at all (evidence shape this
  module doesn't recognize) falls back to a bare ``pass`` with a note --
  never guessed at as fail/uncertain.

FORGE-528: the rows are the project's *current* requirements, read from their
one home: the current revision of each constraint set item (plus constraints
recorded outside any item). An older revision's constraints are not listed,
so a requirement revised from 15 g to 12 g shows once, at 12 g. Each row
carries ``revisionRef`` (``CS-KEY@n``), the revision it was read from.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.api import TwinAPI
from twin_core.consistency.claims import Claim, ClaimStatus, list_claims_for_requirement
from twin_core.consistency.staleness import StalenessEngine, StalenessStatus

_STALE_STATUSES = frozenset(
    {StalenessStatus.STALE, StalenessStatus.SUPERSEDED, StalenessStatus.INVALID}
)
_CURRENT_STATUSES = frozenset({StalenessStatus.CURRENT, StalenessStatus.REVALIDATED})


class EvidenceSummary(BaseModel):
    id: str
    method: str
    tier: int | None = None
    value: float | None = None
    limit: float | None = None
    margin: float | None = None
    staleness: str


class RequirementMatrixRow(BaseModel):
    requirementId: str  # noqa: N815 -- dashboard contract is camelCase
    requirementName: str  # noqa: N815
    limitText: str  # noqa: N815 -- the requirement's own recorded text, e.g. "<= 4.5 kg"
    status: str  # "pass" | "uncertain" | "fail" | "no_data" | "stale"
    detail: str
    artefactIds: list[str] = Field(default_factory=list)  # noqa: N815
    evidence: list[EvidenceSummary] = Field(default_factory=list)
    # FORGE-258 (gap G-A2): surfaced so the dashboard can render "declared
    # but unverified" without a second fetch -- "" means genuinely
    # undeclared, distinct from the live status (which is about evidence,
    # not declaration).
    verificationMethod: str = ""  # noqa: N815
    # FORGE-528: the constraint set revision this requirement was read from,
    # e.g. "CS-WIDGET@2"; None for a constraint recorded outside any item.
    revisionRef: str | None = None  # noqa: N815
    expectedEvidence: str = ""  # noqa: N815


def _extract_value_limit_margin(
    result: dict[str, Any],
) -> tuple[float | None, float | None, float | None, bool]:
    """Read (value, limit, margin, escalated) from either evidence result
    shape this codebase produces -- FORGE-315's tier-0/tier-2
    (`value_mm`/`limit_mm`/`margin_mm`/`escalated`) or FORGE-317's
    sensitivity ranking (`baseline_value`/`limit`/`baseline_margin`, no
    `escalated` concept -- a bare ranking has no "too close to call"
    signal of its own, so that comes back False, never guessed)."""
    if "margin_mm" in result:
        return (
            result.get("value_mm"),
            result.get("limit_mm"),
            result.get("margin_mm"),
            bool(result.get("escalated", False)),
        )
    if "baseline_margin" in result:
        return (
            result.get("baseline_value"),
            result.get("limit"),
            result.get("baseline_margin"),
            False,
        )
    return None, None, None, False


async def _evidence_summary(
    twin: TwinAPI, staleness: StalenessEngine, evidence_id: UUID
) -> EvidenceSummary | None:
    entity = await twin.get_engineering_entity(evidence_id)
    if entity is None:
        return None
    try:
        status = await staleness.get_status("engineering_entity", evidence_id)
    except KeyError:
        return None
    result = entity.metadata.get("result") or {}
    producer = entity.metadata.get("producer") or {}
    value, limit, margin, _escalated = _extract_value_limit_margin(result)
    return EvidenceSummary(
        id=str(evidence_id),
        method=str(producer.get("tool", "")),
        tier=result.get("tier"),
        value=value,
        limit=limit,
        margin=margin,
        staleness=status.value,
    )


async def _derive_row(
    twin: TwinAPI, staleness: StalenessEngine, req: Any, claims: list[Claim]
) -> RequirementMatrixRow:
    # FORGE-259: prefer the structured metric/operator/limit/unit binding
    # (real, machine-set data) over the free-text message/name fallback,
    # when a requirement has one -- unchanged for every constraint recorded
    # before this ticket, which has no `metric` set.
    if req.metric and req.limit is not None:
        limit_text = f"{req.metric} {req.operator} {req.limit}{req.unit}"
    else:
        limit_text = req.message or req.name
    verification_method = req.verification_method or str(
        req.metadata.get("verification_method") or ""
    )
    expected_evidence = req.expected_evidence

    if not claims:
        return RequirementMatrixRow(
            requirementId=str(req.id),
            requirementName=req.name,
            limitText=limit_text,
            status="no_data",
            detail="no claim recorded against this requirement",
            verificationMethod=verification_method,
            expectedEvidence=expected_evidence,
        )

    # Multiple artefacts can each claim to satisfy the same requirement --
    # report every one; prefer a SUPPORTED claim for the derived status
    # (an UNSUPPORTED claim from one artefact shouldn't hide a real,
    # currently-supported claim from another).
    claim = next((c for c in claims if c.status == ClaimStatus.SUPPORTED), claims[0])
    artefact_ids = [str(c.artefact_id) for c in claims]

    evidence_summaries: list[EvidenceSummary] = []
    for c in claims:
        for eid in c.evidence_ids:
            summary = await _evidence_summary(twin, staleness, eid)
            if summary is not None:
                evidence_summaries.append(summary)

    any_stale = any(StalenessStatus(e.staleness) in _STALE_STATUSES for e in evidence_summaries)

    if claim.status == ClaimStatus.UNSUPPORTED:
        status = "stale" if any_stale else "no_data"
        detail = (
            "cited evidence is stale -- claim currently unsupported"
            if any_stale
            else "claim recorded but has no current supporting evidence"
        )
        return RequirementMatrixRow(
            requirementId=str(req.id),
            requirementName=req.name,
            limitText=limit_text,
            status=status,
            detail=detail,
            artefactIds=artefact_ids,
            evidence=evidence_summaries,
            verificationMethod=verification_method,
            expectedEvidence=expected_evidence,
        )

    if any_stale:
        return RequirementMatrixRow(
            requirementId=str(req.id),
            requirementName=req.name,
            limitText=limit_text,
            status="stale",
            detail="claim is supported, but at least one cited evidence entity is stale",
            artefactIds=artefact_ids,
            evidence=evidence_summaries,
            verificationMethod=verification_method,
            expectedEvidence=expected_evidence,
        )

    # Most current evidence with an extractable margin wins the derived status.
    current_with_margin = [
        e
        for e in evidence_summaries
        if StalenessStatus(e.staleness) in _CURRENT_STATUSES and e.margin is not None
    ]
    if not current_with_margin:
        return RequirementMatrixRow(
            requirementId=str(req.id),
            requirementName=req.name,
            limitText=limit_text,
            status="pass",
            detail="claim supported by current evidence (no extractable margin to grade further)",
            artefactIds=artefact_ids,
            evidence=evidence_summaries,
            verificationMethod=verification_method,
            expectedEvidence=expected_evidence,
        )

    best = current_with_margin[-1]
    result = (await twin.get_engineering_entity(UUID(best.id))).metadata.get("result") or {}
    _value, _limit, margin, escalated = _extract_value_limit_margin(result)

    if margin is not None and margin < 0:
        status = "fail"
        detail = f"value {best.value} exceeds limit {best.limit} (margin {margin:.4g})"
    elif escalated:
        status = "uncertain"
        detail = (
            f"margin {margin:.4g} is inside its error band -- escalate to a higher-fidelity check"
        )
    else:
        status = "pass"
        detail = f"value {best.value} within limit {best.limit} (margin {margin:.4g})"

    return RequirementMatrixRow(
        requirementId=str(req.id),
        requirementName=req.name,
        limitText=limit_text,
        status=status,
        detail=detail,
        artefactIds=artefact_ids,
        evidence=evidence_summaries,
        verificationMethod=verification_method,
        expectedEvidence=expected_evidence,
    )


async def build_requirement_matrix(twin: TwinAPI, project_id: UUID) -> list[RequirementMatrixRow]:
    from api_gateway.twin.requirements_home import current_requirements

    current = await current_requirements(twin, project_id)
    staleness = StalenessEngine(twin)
    rows: list[RequirementMatrixRow] = []
    for req in current.all():
        row = await _derive_row(
            twin, staleness, req, await list_claims_for_requirement(twin, req.id)
        )
        row.revisionRef = current.ref_for(req.id)
        rows.append(row)
    return rows
