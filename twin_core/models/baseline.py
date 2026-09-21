"""Baseline — an agreed, approved reference configuration: immutable
references to specific revisions of controlled objects (FORGE-51, spec
section 11).

Not permanently frozen in the sense of blocking further edits -- a
baselined entity can still be revised (FORGE-50 already increments its
revision on every write); what a Baseline actually pins down is which
*revision* of each included entity was the agreed-upon one at approval
time, forever queryable via ``TwinAPI.get_constraint_revision``/
``get_engineering_entity_revision`` even after the entity moves on to a
later revision.

Built via ``twin_core.transactions.baseline.create_baseline`` -- not
constructed and persisted directly -- so that including an entity in a
Baseline and bumping its ``authority`` to ``AuthorityState.BASELINED``
happen as one atomic operation (reusing ``TransactionEngine.commit``,
FORGE-50) rather than two separate writes that could drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType
from twin_core.models.patch import ControlledEntityKind


class BaselineMember(BaseModel):
    """One (entity, revision) pin inside a Baseline's ``includes`` list."""

    entity_kind: ControlledEntityKind
    entity_id: UUID
    revision: int


class Baseline(NodeBase):
    """A named, approved snapshot of specific controlled-object revisions."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.BASELINE
    name: str
    includes: list[BaselineMember]
    approved_by: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str = ""


class BaselineResult(BaseModel):
    """Outcome of ``twin_core.transactions.baseline.create_baseline``."""

    status: Literal["created", "conflict"]
    baseline: Baseline | None = None
    conflicts: list[str] = Field(default_factory=list)
