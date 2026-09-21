"""TransactionEngine — commits a Patch atomically with optimistic concurrency
(FORGE-50, Phase 2 of epic FORGE-35).

Two-phase commit, in the "validate everything, then apply everything" sense
(not two-phase-commit across a distributed transaction): ``commit()`` first
resolves and validates every operation in the patch -- CAS-checking any
``expected_revision``, schema-validating any ``add`` payload -- and only
once all of them pass does it apply a single write. If any operation fails
validation, the whole patch is rejected as a conflict and **zero** writes
happen, mirroring the "resolve before construct" zero-partial-writes
discipline the FORGE-45/46 recorders already established.

Known limitation, stated plainly rather than glossed over: once the apply
phase starts, operations are written one at a time through ``TwinAPI`` --
there is no underlying multi-node database transaction wrapping them (see
FORGE-50's research: neither ``InMemoryGraphEngine`` nor
``Neo4jGraphEngine`` support cross-write rollback today). An operation
failing unexpectedly *after* validation passed (a transient backend error,
not a precondition failure) can leave a partially-applied patch. Real
crash-safe atomicity needs a database transaction under ``GraphEngine``
itself -- out of scope here; validation-time conflict detection is what
this sub-task delivers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.api import RevisionConflictError, TwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import EdgeType
from twin_core.models.patch import (
    ControlledEntityKind,
    Patch,
    PatchOp,
    PatchOperation,
    PatchOperationResult,
    PatchResult,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.transactions.engine")

_KIND_TO_MODEL: dict[ControlledEntityKind, type[Constraint] | type[EngineeringEntity]] = {
    "constraint": Constraint,
    "engineering_entity": EngineeringEntity,
}


class TransactionEngine:
    """Commits :class:`~twin_core.models.patch.Patch` batches against a
    :class:`~twin_core.api.TwinAPI`, atomically at the validation boundary."""

    def __init__(self, twin: TwinAPI) -> None:
        self._twin = twin

    async def commit(self, patch: Patch) -> PatchResult:
        with tracer.start_as_current_span("twin.transaction.commit") as span:
            span.set_attribute("patch.id", str(patch.id))
            span.set_attribute("patch.operation_count", len(patch.operations))

            conflicts = await self._validate(patch)
            if conflicts:
                logger.info(
                    "patch_conflict",
                    patch_id=str(patch.id),
                    conflicts=conflicts,
                )
                return PatchResult(patch_id=patch.id, status="conflict", conflicts=conflicts)

            try:
                applied = await self._apply(patch)
            except (RevisionConflictError, KeyError) as exc:
                # A conflict slipped in between validation and apply (another
                # patch committed concurrently) -- surfaced as a conflict,
                # not an unhandled exception. Any operations already applied
                # earlier in *this* patch before the failing one are NOT
                # rolled back (the documented no-multi-write-transaction
                # limitation); logged loudly rather than hidden.
                logger.warning(
                    "patch_conflict_during_apply",
                    patch_id=str(patch.id),
                    error=str(exc),
                )
                return PatchResult(
                    patch_id=patch.id,
                    status="conflict",
                    conflicts=[f"conflict during apply: {exc}"],
                )
            logger.info(
                "patch_committed",
                patch_id=str(patch.id),
                operation_count=len(applied),
                reason=patch.reason,
            )
            return PatchResult(patch_id=patch.id, status="committed", applied=applied)

    async def _validate(self, patch: Patch) -> list[str]:
        conflicts: list[str] = []
        for i, op in enumerate(patch.operations):
            try:
                await self._validate_operation(op)
            except (RevisionConflictError, KeyError, ValueError) as exc:
                conflicts.append(f"operation[{i}] ({op.op}): {exc}")
        return conflicts

    async def _validate_operation(self, op: PatchOperation) -> None:
        # PatchOperation._require_op_fields already guarantees the fields
        # each branch below reads are non-None for that op -- the asserts
        # are for mypy's benefit, not new runtime checks.
        if op.op == PatchOp.ADD:
            assert op.entity_kind is not None
            _KIND_TO_MODEL[op.entity_kind].model_validate(op.entity)  # dry-run, no write
        elif op.op in (PatchOp.REVISE, PatchOp.SUPERSEDE, PatchOp.DEPRECATE, PatchOp.INVALIDATE):
            assert op.entity_kind is not None
            assert op.entity_id is not None
            await self._check_current(op.entity_kind, op.entity_id, op.expected_revision)
        elif op.op in (PatchOp.LINK, PatchOp.UNLINK):
            assert op.relation is not None
            EdgeType(op.relation)  # raises ValueError if unknown

    async def _check_current(
        self, kind: ControlledEntityKind, entity_id: UUID, expected_revision: int | None
    ) -> None:
        current = await self._get(kind, entity_id)
        if current is None:
            raise KeyError(f"{kind} {entity_id} not found")
        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflictError(entity_id, expected_revision, current.revision)

    async def _get(
        self, kind: ControlledEntityKind, entity_id: UUID
    ) -> Constraint | EngineeringEntity | None:
        if kind == "constraint":
            return await self._twin.get_constraint(entity_id)
        return await self._twin.get_engineering_entity(entity_id)

    async def _apply(self, patch: Patch) -> list[PatchOperationResult]:
        applied: list[PatchOperationResult] = []
        for op in patch.operations:
            applied.append(await self._apply_operation(op, patch))
        return applied

    async def _apply_operation(self, op: PatchOperation, patch: Patch) -> PatchOperationResult:
        if op.op == PatchOp.ADD:
            return await self._apply_add(op, patch)
        if op.op == PatchOp.REVISE:
            return await self._apply_revise(op)
        if op.op in (PatchOp.LINK, PatchOp.UNLINK):
            return await self._apply_link(op)
        if op.op == PatchOp.SUPERSEDE:
            return await self._apply_supersede(op)
        # DEPRECATE / INVALIDATE
        return await self._apply_status_change(op)

    async def _apply_add(self, op: PatchOperation, patch: Patch) -> PatchOperationResult:
        entity = dict(op.entity or {})
        entity.setdefault("project_id", patch.project_id)
        if op.entity_kind == "constraint":
            created: Constraint | EngineeringEntity = await self._twin.create_constraint(
                Constraint.model_validate(entity)
            )
        else:
            created = await self._twin.create_engineering_entity(
                EngineeringEntity.model_validate(entity)
            )
        return PatchOperationResult(op=op.op, entity_id=created.id, new_revision=created.revision)

    async def _apply_revise(self, op: PatchOperation) -> PatchOperationResult:
        assert op.entity_kind is not None
        assert op.entity_id is not None
        updated = await self._update(
            op.entity_kind, op.entity_id, op.fields or {}, op.expected_revision
        )
        return PatchOperationResult(op=op.op, entity_id=updated.id, new_revision=updated.revision)

    async def _apply_link(self, op: PatchOperation) -> PatchOperationResult:
        assert op.entity_id is not None
        assert op.target_id is not None
        assert op.relation is not None
        edge_type = EdgeType(op.relation)
        if op.op == PatchOp.LINK:
            await self._twin.add_edge(op.entity_id, op.target_id, edge_type)
        else:
            await self._twin.remove_edge(op.entity_id, op.target_id, edge_type)
        return PatchOperationResult(op=op.op, entity_id=op.entity_id)

    async def _apply_supersede(self, op: PatchOperation) -> PatchOperationResult:
        assert op.entity_kind is not None
        assert op.entity_id is not None
        assert op.target_id is not None
        # New (target_id) supersedes old (entity_id) -- same edge direction
        # as the existing datasheet-ingestion SUPERSEDES precedent
        # (twin_core/api.py InMemoryTwinAPI.ingest_datasheet).
        await self._twin.add_edge(op.target_id, op.entity_id, EdgeType.SUPERSEDES)
        updated = await self._update(
            op.entity_kind, op.entity_id, {"status": "superseded"}, op.expected_revision
        )
        return PatchOperationResult(op=op.op, entity_id=updated.id, new_revision=updated.revision)

    async def _apply_status_change(self, op: PatchOperation) -> PatchOperationResult:
        assert op.entity_kind is not None
        assert op.entity_id is not None
        status = "deprecated" if op.op == PatchOp.DEPRECATE else "invalidated"
        updated = await self._update(
            op.entity_kind, op.entity_id, {"status": status}, op.expected_revision
        )
        return PatchOperationResult(op=op.op, entity_id=updated.id, new_revision=updated.revision)

    async def _update(
        self,
        kind: ControlledEntityKind,
        entity_id: UUID,
        fields: dict[str, Any],
        expected_revision: int | None,
    ) -> Constraint | EngineeringEntity:
        if kind == "constraint":
            return await self._twin.update_constraint(
                entity_id, fields, expected_revision=expected_revision
            )
        return await self._twin.update_engineering_entity(
            entity_id, fields, expected_revision=expected_revision
        )
