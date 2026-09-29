"""Constraint node — a rule that must be satisfied across work_products."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from twin_core.models.base import NodeBase
from twin_core.models.confidence import Confidence
from twin_core.models.enums import (
    EVIDENCE_TYPES,
    AuthorityState,
    ConstraintSeverity,
    ConstraintStatus,
    NodeType,
)
from twin_core.models.quantity import is_valid_unit


class Constraint(NodeBase):
    """A constraint evaluated by the Constraint Engine against the graph state."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.CONSTRAINT
    name: str
    expression: str
    severity: ConstraintSeverity
    status: ConstraintStatus = ConstraintStatus.UNEVALUATED
    domain: str
    cross_domain: bool = False
    source: str
    message: str = ""
    # FORGE-312 (lifecycle step 2, spec section 29 Requirement): real typed
    # fields, not metadata dict keys -- `constraint_recorder.py` keeps them,
    # `twin_core.consistency.gates`'s G7 checks read them (falling back to
    # `metadata["verification_method"]` for data RequirementAuthorAgent
    # (FORGE-55) already wrote there before this field existed).
    acceptance_criteria: str = ""
    verification_method: str = ""
    # FORGE-258 (gap G-A2, milestone M1): the KIND of evidence that would
    # actually verify this requirement -- e.g. a "safety_factor" constraint
    # naming expected_evidence="simulation" says a claim citing only a
    # "datasheet" evidence entity doesn't count, even if a claim technically
    # exists. Validated against twin_core.models.enums.EVIDENCE_TYPES, the
    # SAME set twin.record_evidence's own evidence_type accepts -- a
    # requirement can never declare it expects evidence this codebase has
    # no way to actually record. Unlike verification_method (a free string,
    # FORGE-312) or target_node_type (free string, FORGE-259), this one is
    # validated: the whole point of the field is to be checked against real
    # Evidence, so a typo here should fail loud, not silently never match.
    expected_evidence: str = ""
    # FORGE-259 (gap G-A3, milestone M1): a structured measured-key binding
    # -- what property this constraint is about, compared how, against what
    # limit and unit, on what kind of node -- rather than only the opaque
    # `expression` string. All five are optional/blank-default so every
    # existing `expression`-only Constraint stays valid unchanged (same
    # additive discipline FORGE-312 already used for acceptance_criteria/
    # verification_method). Deliberately NOT wired into `expression`
    # evaluation or into FORGE-315/317/320's evaluator/sensitivity/
    # optimizer tools automatically -- those already take their own
    # metric/limit kwargs directly and reading a Constraint's structured
    # fields into them is real, separable follow-up work, not this
    # ticket's own minimal scope (the ticket asks for the declaration to
    # exist and be editable, not for every existing tool to auto-consume
    # it). `unit`, when set, must be one of `twin_core.models.quantity`'s
    # recognized units -- the same validator `InterfaceQuantity.unit`
    # (FORGE-313) already uses, so a constraint and an interface quantity
    # can never silently disagree about what units even mean.
    metric: str = ""
    operator: str = "<="
    limit: float | None = None
    unit: str = ""
    target_node_type: str = ""
    last_evaluated: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    # FORGE-50 (Phase 2, epic FORGE-35): optimistic-concurrency revision
    # counter. Starts at 1 on creation; TwinAPI.update_constraint increments
    # it on every applied write and rejects a caller-supplied
    # expected_revision that no longer matches it (PatchConflictError).
    revision: int = 1
    # FORGE-51: authority lifecycle (spec section 25). Only
    # twin_core.transactions.baseline.create_baseline ever advances this to
    # BASELINED -- never derived from `confidence`.
    authority: AuthorityState = AuthorityState.PROPOSED
    confidence: Confidence | None = None

    @field_validator("unit")
    @classmethod
    def _validate_unit(cls, v: str) -> str:
        if v and not is_valid_unit(v):
            raise ValueError(f"{v!r} is not a recognized unit")
        return v

    @field_validator("expected_evidence")
    @classmethod
    def _validate_expected_evidence(cls, v: str) -> str:
        if v and v not in EVIDENCE_TYPES:
            raise ValueError(
                f"{v!r} is not a recognized evidence type (one of {sorted(EVIDENCE_TYPES)})"
            )
        return v
