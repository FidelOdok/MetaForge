"""create_baseline — builds a Baseline atomically alongside the authority
bump it implies (FORGE-51, Phase 2 of epic FORGE-35).

Reuses ``TransactionEngine.commit`` (FORGE-50) rather than writing its own
write path: every member's authority is advanced to
``AuthorityState.BASELINED`` as ``revise`` operations inside one Patch, with
each operation's ``expected_revision`` pinning the exact revision the caller
read. If any member changed since the caller read it, the whole patch
conflicts and rejects -- no Baseline is created, no member's authority
moves, and the caller sees exactly which member changed underneath it (the
same optimistic-concurrency guarantee FORGE-50 gives every other mutation).

Deliberate simplification versus the spec's own illustration (``includes:
[REQ-001@3, ...]``): FORGE-50's ``update_constraint``/``update_engineering_
entity`` increments ``revision`` on *every* applied write, authority-only
changes included -- there is no separate "administrative update that
doesn't bump revision" write path, and this module doesn't invent one just
for baselines (that would fork the "every write increments revision"
invariant FORGE-50 already tests). So a Baseline pins the revision
*immediately after* the authority bump (e.g. REQ-001@4, not @3) -- the
first revision at which the entity's authority genuinely was BASELINED --
rather than the pre-baseline revision the doc's own example shows. The
member's substantive fields are identical between the two; only the
revision number and ``authority`` differ.
"""

from __future__ import annotations

from uuid import UUID

from twin_core.api import TwinAPI
from twin_core.models.baseline import Baseline, BaselineMember, BaselineResult
from twin_core.models.enums import AuthorityState, EdgeType
from twin_core.models.patch import ControlledEntityKind, Patch, PatchOp, PatchOperation
from twin_core.transactions.engine import TransactionEngine


async def create_baseline(
    twin: TwinAPI,
    engine: TransactionEngine,
    *,
    name: str,
    members: list[tuple[ControlledEntityKind, UUID]],
    approved_by: list[str],
    reason: str,
    project_id: UUID | None = None,
) -> BaselineResult:
    if not members:
        raise ValueError("create_baseline: at least one member is required")
    if not approved_by:
        raise ValueError("create_baseline: approved_by is required (spec section 11)")

    pins: list[BaselineMember] = []
    operations: list[PatchOperation] = []
    for kind, entity_id in members:
        current = (
            await twin.get_constraint(entity_id)
            if kind == "constraint"
            else await twin.get_engineering_entity(entity_id)
        )
        if current is None:
            return BaselineResult(status="conflict", conflicts=[f"{kind} {entity_id} not found"])
        pins.append(
            BaselineMember(entity_kind=kind, entity_id=entity_id, revision=current.revision)
        )
        operations.append(
            PatchOperation(
                op=PatchOp.REVISE,
                entity_kind=kind,
                entity_id=entity_id,
                fields={"authority": AuthorityState.BASELINED},
                expected_revision=current.revision,
            )
        )

    patch = Patch(operations=operations, reason=reason, project_id=project_id)
    result = await engine.commit(patch)
    if result.status == "conflict":
        return BaselineResult(status="conflict", conflicts=result.conflicts)

    # Record the *post-bump* revision (see module docstring) -- the applied
    # results are in the same order as `operations`/`pins`.
    for pin, applied in zip(pins, result.applied, strict=True):
        assert applied.new_revision is not None
        pin.revision = applied.new_revision

    baseline = Baseline(
        name=name,
        includes=pins,
        approved_by=approved_by,
        reason=reason,
        project_id=project_id,
    )
    created = await twin.create_baseline(baseline)
    for pin in pins:
        await twin.add_edge(pin.entity_id, created.id, EdgeType.INCLUDED_IN_BASELINE)

    return BaselineResult(status="created", baseline=created)
