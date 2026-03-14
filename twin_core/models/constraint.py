"""Constraint node — a rule that must be satisfied across work_products."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import ConstraintSeverity, ConstraintStatus, NodeType


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
