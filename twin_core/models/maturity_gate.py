"""MaturityGate — a real promotion gate with its own persistent state
(FORGE-319, target lifecycle spec §29 MaturityGate, step 17).

Unlike ``twin_core.consistency.gates``'s G3-G8 design-flow gates (a
different, pre-existing concept -- see that module's own docstring: *"there
is still no ``enforce_consistency_gate`` flag, so a FAILED status only
informs the human reviewer, never auto-fails the transition"*), a
MaturityGate promotion attempt actually REFUSES when its required claims
aren't satisfied -- the first place in this codebase a gate genuinely
blocks rather than just reports. That refusal lives in
``api_gateway/requirement_intelligence/promotion.py``'s ``attempt_promotion``
(this module has no twin/MCP dependency, mirroring every other twin_core/
api_gateway split in this epic); this module is just the persisted record
of a promotion attempt and its outcome.

"required_claims" (the ticket's own Scope wording) and a requirement row's
own live-derived ``status`` (``api_gateway/requirement_intelligence/
matrix.py``, FORGE-318 -- "PASS if margin > k*band, UNCERTAIN if within,
FAIL if below") are the SAME concept: a MaturityGate's ``required_claim_ids``
are just the subset of a project's requirement (Constraint) ids that must
each be ``pass`` (or have an approved waiver covering a ``fail``) before
promotion is allowed. No second margin/band computation is invented here.

A ``MaturityGate`` is its own ``NodeType`` (not a generic
``EngineeringEntity`` tag, unlike waiver/release_approval): it has real,
structured multi-field state (``required_claim_ids``, per-claim
``results``, an overall ``state``) that wants typed fields, the same
reasoning ``EngineeringChangeTransaction`` (FORGE-66) already used for
itself rather than being a metadata-dict-only EngineeringEntity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class MaturityLevel(StrEnum):
    """Spec §29's own promotion ladder."""

    CONCEPT = "concept"
    SIM_VALIDATED = "sim_validated"
    PHYSICALLY_VALIDATED = "physically_validated"
    RELEASED = "released"


class RequiredClaimDecision(StrEnum):
    """Per-required-claim outcome -- ``pass``/``uncertain``/``fail`` mirror
    ``RequirementMatrixRow.status`` (FORGE-318) exactly (a `no_data`/`stale`
    matrix row is treated the same as `fail` here: neither is a satisfied
    claim). ``waived`` is FAIL-but-covered-by-an-approved-waiver -- still
    counts as satisfied for promotion, but the outcome is recorded honestly
    rather than silently relabelled as ``pass``.
    """

    PASS = "pass"
    UNCERTAIN = "uncertain"
    FAIL = "fail"
    WAIVED = "waived"


#: A required claim counts as satisfied for promotion when it's PASS or a
#: FAIL covered by an approved waiver (WAIVED) -- reused by
#: ``api_gateway.requirement_intelligence.promotion.attempt_promotion`` so
#: the "what blocks a promotion" rule is defined in exactly one place.
SATISFIED_DECISIONS = frozenset({RequiredClaimDecision.PASS, RequiredClaimDecision.WAIVED})


class RequiredClaimResult(BaseModel):
    requirement_id: UUID
    requirement_name: str
    decision: RequiredClaimDecision
    detail: str
    waiver_id: UUID | None = None


class MaturityGate(NodeBase):
    """One promotion attempt's record -- persisted whether it succeeded or
    was refused, so "why wasn't this promoted" has a real, queryable
    answer rather than only a transient tool-call response.
    """

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.MATURITY_GATE
    level: MaturityLevel
    required_claim_ids: list[UUID] = Field(default_factory=list)
    results: list[RequiredClaimResult] = Field(default_factory=list)
    # True only when every required claim was PASS/WAIVED AND a human
    # decided_by was supplied -- the "gates can block" outcome: a refused
    # attempt is still persisted (results show exactly what blocked it),
    # just with promoted=False.
    promoted: bool = False
    blocked_reason: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    k: float = 1.0
    # FORGE-290 (gap G-G4): a human reviewer's own rationale -- distinct
    # from `blocked_reason`, which is system-derived from claim decisions.
    # Set on either outcome: a reviewer can leave context on an approval
    # too, not only a rejection.
    comment: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
