"""Requirement satisfaction claims (FORGE-65, spec section 57, Phase 6 of
epic FORGE-35).

A claim -- "this artefact satisfies this requirement" -- is deliberately
NOT a stored status field (a stored "supported"/"unsupported" flag would
itself need its own staleness tracking the moment the evidence it depended
on went stale, the exact "silently stale boolean" failure mode this whole
epic has avoided since Phase 4/5's gate evaluators). Instead:

- ``claim_recorder.py`` persists only the FACT of the claim -- a real graph
  edge from the artefact (a WorkProduct) to the requirement (a Constraint),
  typed by ``claim_type`` (default ``EdgeType.SATISFIES``, reusing the same
  relation vocabulary FORGE-61/64 already established rather than inventing
  a new edge type), carrying the cited evidence ids in its own metadata.
- ``evaluate_claim`` (this module) computes ``status`` LIVE, every time it's
  asked, from current graph state: SUPPORTED iff at least one cited evidence
  entity currently exists with a CURRENT or REVALIDATED staleness status --
  never a faked or cached answer, same posture as
  ``twin_core.consistency.gates``'s evaluators. "Claims without evidence
  remain UNSUPPORTED" falls out naturally: an empty evidence list never
  finds a CURRENT one.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.api import TwinAPI
from twin_core.consistency.staleness import StalenessEngine, StalenessStatus

CLAIM_EDGE_KIND = "requirement_satisfaction_claim"

_SUPPORTING_STATUSES = frozenset({StalenessStatus.CURRENT, StalenessStatus.REVALIDATED})


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class Claim(BaseModel):
    artefact_id: UUID
    requirement_id: UUID
    claim_type: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    status: ClaimStatus


async def evaluate_claim(twin: TwinAPI, artefact_id: UUID, requirement_id: UUID) -> Claim:
    """Evaluate the claim edge from `artefact_id` to `requirement_id`
    (recorded via `claim_recorder.py`). Raises `ValueError` if no such
    claim was ever recorded -- there is nothing to evaluate, not an
    UNSUPPORTED claim (that status means "recorded, but unevidenced").
    """
    edges = await twin.get_edges(artefact_id, direction="outgoing")
    claim_edges = [
        e
        for e in edges
        if e.target_id == requirement_id and (e.metadata or {}).get("kind") == CLAIM_EDGE_KIND
    ]
    if not claim_edges:
        raise ValueError(
            f"no requirement-satisfaction claim recorded from {artefact_id} to "
            f"{requirement_id} -- call twin.record_claim first"
        )
    edge = claim_edges[0]
    evidence_ids = [UUID(e) for e in (edge.metadata or {}).get("evidence", [])]

    status = ClaimStatus.UNSUPPORTED
    if evidence_ids:
        engine = StalenessEngine(twin)
        for evidence_id in evidence_ids:
            try:
                evidence_status = await engine.get_status("engineering_entity", evidence_id)
            except KeyError:
                continue  # cited evidence was deleted -- doesn't support the claim
            if evidence_status in _SUPPORTING_STATUSES:
                status = ClaimStatus.SUPPORTED
                break

    return Claim(
        artefact_id=artefact_id,
        requirement_id=requirement_id,
        claim_type=edge.edge_type.value,
        evidence_ids=evidence_ids,
        status=status,
    )
