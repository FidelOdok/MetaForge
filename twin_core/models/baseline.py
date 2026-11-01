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

FORGE-526 extends the same node to items (FORGE-523): ``items`` pins every
current item of the project as ``KEY@n`` (with the revision node id, so a
pin never depends on the item's head moving), and ``gate_id`` / ``run_id``
say which gate approval and which design-flow run produced it. A baseline
created by a gate approval (``source="gate"``) holds items only; one created
through ``twin.create_baseline`` (``source="manual"``) holds both the
constraint/entity pins it always had and the item pins. Nothing ever edits a
baseline after creation: there is no update path, and each pin is frozen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType
from twin_core.models.patch import ControlledEntityKind


class BaselineMember(BaseModel):
    """One (entity, revision) pin inside a Baseline's ``includes`` list."""

    entity_kind: ControlledEntityKind
    entity_id: UUID
    revision: int


class BaselineItemRef(BaseModel):
    """One immutable ``KEY@n`` pin inside a Baseline's ``items`` list (FORGE-526)."""

    model_config = ConfigDict(frozen=True)

    key: str
    item_type: str
    revision: int
    node_id: UUID
    name: str = ""

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.revision}"


class Baseline(NodeBase):
    """A named, approved snapshot of specific controlled-object revisions."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.BASELINE
    name: str
    includes: list[BaselineMember]
    approved_by: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str = ""
    #: FORGE-526: immutable item@rev pins, one per current item of the project.
    items: list[BaselineItemRef] = Field(default_factory=list)
    #: FORGE-526: the gate whose approval created this baseline, if any.
    gate_id: str | None = None
    #: FORGE-526: the design-flow run that gate belongs to, if any.
    run_id: str | None = None
    #: FORGE-526: ``gate`` (a gate approval) or ``manual`` (``twin.create_baseline``).
    source: Literal["gate", "manual"] = "manual"


class BaselineResult(BaseModel):
    """Outcome of ``twin_core.transactions.baseline.create_baseline``."""

    status: Literal["created", "conflict"]
    baseline: Baseline | None = None
    conflicts: list[str] = Field(default_factory=list)
