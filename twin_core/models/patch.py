"""Patch/transaction model (FORGE-50, Phase 2 of epic FORGE-35).

A ``Patch`` is a batch of typed graph operations committed atomically by
``twin_core.transactions.engine.TransactionEngine``: either every operation
applies, or none do. Optimistic concurrency comes from each operation that
touches an existing node carrying the ``expected_revision`` the caller last
read -- if any node's current revision has since moved on, the whole patch
is rejected as a conflict before a single write happens (mirrors the
"resolve before construct" / zero-partial-writes discipline the FORGE-45/46
recorders already established for parent_refs resolution).

Scope for this sub-task, from the spec's full operation list (``add``,
``revise``, ``link``, ``unlink``, ``supersede``, ``deprecate``,
``invalidate``, ``allocate``, ``baseline``, ``waive``): this module
implements the first seven. ``allocate`` (budget propagation) is Phase 4
scope, ``baseline`` is FORGE-51, and ``waive`` requires the HITL Level-4
routing built in FORGE-53 -- none of those exist yet, so accepting those op
values here would let a caller construct a ``Patch`` the engine can't
actually enforce correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

# Which twin_core node type an operation's entity_id/target_id refers to.
# Patch operations are precise graph edits (the caller already resolved
# names to ids, e.g. via api_gateway/twin/_ref_resolver.py) so -- unlike the
# recorders -- there is no by-name matching here, only a type tag telling
# the engine which TwinAPI accessor to call.
ControlledEntityKind = Literal["constraint", "engineering_entity"]


class PatchOp(StrEnum):
    """Operation kinds this sub-task's TransactionEngine can commit."""

    ADD = "add"
    REVISE = "revise"
    LINK = "link"
    UNLINK = "unlink"
    SUPERSEDE = "supersede"
    DEPRECATE = "deprecate"
    INVALIDATE = "invalidate"


class PatchOperation(BaseModel):
    """One typed graph edit inside a :class:`Patch`.

    Field usage per ``op``:

    - ``add``: ``entity_kind`` + ``entity`` (the full node payload as a
      dict, constructed and validated against the real Pydantic model
      before any write happens).
    - ``revise``: ``entity_kind`` + ``entity_id`` + ``fields`` (partial
      update) + optional ``expected_revision`` for the CAS check.
    - ``link`` / ``unlink``: ``entity_id`` (source), ``relation``,
      ``target_id``.
    - ``supersede``: ``entity_id`` (the old node being superseded),
      ``entity_kind``, ``target_id`` (the new node that supersedes it),
      optional ``expected_revision`` on the old node.
    - ``deprecate`` / ``invalidate``: ``entity_kind`` + ``entity_id`` +
      optional ``expected_revision``; sets ``status`` to ``"deprecated"``/
      ``"invalidated"``.
    """

    op: PatchOp
    entity_kind: ControlledEntityKind | None = None
    entity_id: UUID | None = None
    entity: dict[str, Any] | None = None
    fields: dict[str, Any] | None = None
    relation: str | None = None
    target_id: UUID | None = None
    expected_revision: int | None = None

    @model_validator(mode="after")
    def _require_op_fields(self) -> PatchOperation:
        if self.op == PatchOp.ADD:
            if self.entity_kind is None or self.entity is None:
                raise ValueError("'add' operations require entity_kind and entity")
        elif self.op == PatchOp.REVISE:
            if self.entity_kind is None or self.entity_id is None or self.fields is None:
                raise ValueError("'revise' operations require entity_kind, entity_id, fields")
        elif self.op in (PatchOp.LINK, PatchOp.UNLINK):
            if self.entity_id is None or self.relation is None or self.target_id is None:
                raise ValueError(f"'{self.op}' operations require entity_id, relation, target_id")
        elif self.op == PatchOp.SUPERSEDE:
            if self.entity_kind is None or self.entity_id is None or self.target_id is None:
                raise ValueError("'supersede' operations require entity_kind, entity_id, target_id")
        elif self.op in (PatchOp.DEPRECATE, PatchOp.INVALIDATE):
            if self.entity_kind is None or self.entity_id is None:
                raise ValueError(f"'{self.op}' operations require entity_kind, entity_id")
        return self


class Patch(BaseModel):
    """A batch of operations to commit atomically."""

    id: UUID = Field(default_factory=uuid4)
    operations: list[PatchOperation]
    reason: str
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _require_operations(self) -> Patch:
        if not self.operations:
            raise ValueError("Patch must contain at least one operation")
        if not self.reason or not self.reason.strip():
            raise ValueError("Patch.reason is required (change rationale, spec section 39)")
        return self


class PatchOperationResult(BaseModel):
    """What happened when one operation inside a committed patch was applied."""

    op: PatchOp
    entity_id: UUID | None = None
    new_revision: int | None = None


class PatchResult(BaseModel):
    """Outcome of ``TransactionEngine.commit(patch)``."""

    patch_id: UUID
    status: Literal["committed", "conflict"]
    applied: list[PatchOperationResult] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
