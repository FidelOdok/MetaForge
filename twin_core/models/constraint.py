"""Constraint node — a rule that must be satisfied across work_products."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.confidence import Confidence
from twin_core.models.enums import AuthorityState, ConstraintSeverity, ConstraintStatus, NodeType


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
