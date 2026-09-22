"""EngineeringEntity node — the Engineering Intent & Requirements Harness's
generic entity type (FORGE-44, epic FORGE-35).

One node type implements the spec's "common base" (see
``docs/architecture/engineering-intent-requirements-harness.md`` section 46
in the MetaForge-Planner repo) for every entity that isn't already a real,
first-class node elsewhere in this codebase — not eight bespoke model
classes. ``Requirement``/``Constraint`` keep using the existing
:class:`~twin_core.models.constraint.Constraint` node (already real,
evaluable, gate-integrated); ``Decision`` keeps using
``WorkProductType.DESIGN_DECISION`` via ``twin.record_decision``.
Type-specific fields the spec calls out per subtype (``parameter``/
``operator``/``value``/``unit`` for a quantified need, ``metric``/
``direction``/``target`` for an objective, ``evidence_type``/``producer``/
``result`` for evidence, ...) live in ``metadata`` — the same pattern
``WorkProduct.metadata`` already uses everywhere in this codebase.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.confidence import Confidence
from twin_core.models.enums import AuthorityState, NodeType

EngineeringEntityType = Literal[
    "intent",
    "stakeholder_need",
    "objective",
    "assumption",
    "question",
    "risk",
    "verification_case",
    "evidence",
    # FORGE-73 (budget/invariant persistence): metric/unit/system_total/
    # allocations (budget) and metric/unit/limit/comparison/
    # source_constraint_id (invariant) live in metadata, same convention as
    # "objective"'s metric/direction/target -- see
    # twin_core.consistency.budgets.budget_from_entity /
    # twin_core.consistency.invariants.invariant_from_entity.
    "budget",
    "invariant",
]


class EngineeringEntity(NodeBase):
    """A generic Engineering Intent & Requirements Harness entity.

    ``entity_type`` discriminates which of the spec's entity kinds this node
    represents. ``parent_refs`` holds the *names* (or ids, resolved at record
    time) of the entities this one derives from/satisfies/etc. — the actual
    graph edge (``EdgeType.DERIVES_FROM``/``SATISFIES``/... per ``relation``)
    is what a recorder (FORGE-45) creates; this field is a denormalized,
    non-traversal-cost mirror of that edge, not the source of truth for it.
    """

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.ENGINEERING_ENTITY
    entity_type: EngineeringEntityType
    title: str | None = None
    statement: str | None = None
    # Lifecycle state (spec section 10: DISCOVERED -> ... -> VALIDATED, plus
    # side states REJECTED/SUPERSEDED/DEFERRED/WAIVED/OBSOLETE) is kept as a
    # plain string for now -- driving it needs the requirement-intelligence
    # agents (Phase 3), not this sub-task.
    status: str = "proposed"
    # FORGE-51: authority lifecycle (spec section 25), formalized into a real
    # StrEnum now that Baseline (FORGE-51) gives it real transition logic --
    # only twin_core.transactions.baseline.create_baseline ever advances this
    # to BASELINED. Never derived from `confidence`.
    authority: AuthorityState = AuthorityState.PROPOSED
    confidence: Confidence | None = None
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_refs: list[str] = Field(default_factory=list)
    parent_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    # FORGE-50 (Phase 2): optimistic-concurrency revision counter. Starts at
    # 1 on creation; TwinAPI.update_engineering_entity increments it on every
    # applied write and rejects a stale expected_revision (PatchConflictError).
    revision: int = 1
