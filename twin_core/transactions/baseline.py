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

FORGE-526 extends the same Baseline to items (FORGE-523). ``create_baseline``
also takes ``items`` (``KEY@n`` pins, see :func:`baseline_item_refs`), and a
baseline may hold items alone. :func:`create_item_baseline` is the gate
approval path: it pins every current item of the project (the highest
approved revision; a draft never reaches a baseline) with the approver, gate
and run, and is idempotent per ``(project, gate, run)`` so an approval that is
retried does not record two baselines. :func:`diff_baselines` compares two
pin lists item by item. Item pins carry no authority bump: an item revision is
immutable already, so pinning it needs no write to the revision itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.api import TwinAPI
from twin_core.items.current import current_items
from twin_core.models.baseline import (
    Baseline,
    BaselineItemRef,
    BaselineMember,
    BaselineResult,
)
from twin_core.models.enums import AuthorityState, EdgeType
from twin_core.models.patch import ControlledEntityKind, Patch, PatchOp, PatchOperation
from twin_core.transactions.engine import TransactionEngine

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.transactions.baseline")

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-baselines")
    return _metrics


async def create_baseline(
    twin: TwinAPI,
    engine: TransactionEngine,
    *,
    name: str,
    members: list[tuple[ControlledEntityKind, UUID]],
    approved_by: list[str],
    reason: str,
    project_id: UUID | None = None,
    items: list[BaselineItemRef] | None = None,
    gate_id: str | None = None,
    run_id: str | None = None,
    source: Literal["gate", "manual"] = "manual",
) -> BaselineResult:
    if not members and not items:
        raise ValueError("create_baseline: at least one member or item is required")
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

    if operations:
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
        items=list(items or []),
        gate_id=gate_id,
        run_id=run_id,
        source=source,
    )
    created = await twin.create_baseline(baseline)
    for pin in pins:
        await twin.add_edge(pin.entity_id, created.id, EdgeType.INCLUDED_IN_BASELINE)
    for ref in created.items:
        await twin.add_edge(
            ref.node_id,
            created.id,
            EdgeType.INCLUDED_IN_BASELINE,
            metadata={"item_key": ref.key, "revision": ref.revision},
        )

    return BaselineResult(status="created", baseline=created)


# ---------------------------------------------------------------------------
# Items (FORGE-526)
# ---------------------------------------------------------------------------


async def baseline_item_refs(twin: Any, project_id: UUID | str) -> list[BaselineItemRef]:
    """``KEY@n`` for the current (highest approved) revision of every project item."""
    refs: list[BaselineItemRef] = []
    for entry in await current_items(twin, project_id):
        if entry.current is None:
            continue
        refs.append(
            BaselineItemRef(
                key=entry.item.key,
                item_type=entry.item.item_type,
                revision=entry.current.revision,
                node_id=entry.current.node_id,
                name=entry.current.name or entry.item.name,
            )
        )
    return refs


async def create_item_baseline(
    twin: Any,
    *,
    project_id: UUID | str,
    approved_by: list[str],
    gate_id: str | None = None,
    run_id: str | None = None,
    name: str | None = None,
    reason: str = "",
) -> Baseline:
    """Pin every current item of the project. Idempotent per (project, gate, run).

    Raises ``ValueError`` when the project has no current item (a baseline of
    nothing would satisfy G8's "configuration baseline fixed" vacuously).
    """
    pid = UUID(str(project_id))
    with tracer.start_as_current_span("twin.baseline.create_item_baseline") as span:
        span.set_attribute("baseline.project_id", str(pid))
        span.set_attribute("baseline.gate_id", gate_id or "")
        if gate_id:
            for existing in await twin.list_baselines(project_id=pid):
                if existing.gate_id == gate_id and existing.run_id == run_id:
                    span.set_attribute("baseline.outcome", "existing")
                    logger.info(
                        "item_baseline_exists",
                        baseline_id=str(existing.id),
                        gate_id=gate_id,
                        run_id=run_id,
                    )
                    _collector().record_twin_baseline("gate", "existing")
                    return existing  # type: ignore[no-any-return]
        refs = await baseline_item_refs(twin, pid)
        if not refs:
            _collector().record_twin_baseline("gate" if gate_id else "manual", "empty")
            raise ValueError(
                "create_item_baseline: the project has no current item to baseline yet"
            )
        created_at = datetime.now(UTC)
        label = name or (f"{gate_id} approved" if gate_id else f"Baseline {created_at.isoformat()}")
        result = await create_baseline(
            twin,
            TransactionEngine(twin),
            name=label,
            members=[],
            approved_by=approved_by,
            reason=reason or (f"gate {gate_id} approved" if gate_id else "baseline"),
            project_id=pid,
            items=refs,
            gate_id=gate_id,
            run_id=run_id,
            source="gate" if gate_id else "manual",
        )
        assert result.baseline is not None
        baseline = result.baseline
        span.set_attribute("baseline.outcome", "created")
        span.set_attribute("baseline.item_count", len(refs))
        logger.info(
            "item_baseline_created",
            baseline_id=str(baseline.id),
            project_id=str(pid),
            gate_id=gate_id,
            run_id=run_id,
            item_count=len(refs),
            approved_by=approved_by,
        )
        _collector().record_twin_baseline(baseline.source, "created")
        return baseline


@dataclass
class BaselineItemDiff:
    """How one item differs between baseline ``a`` and baseline ``b``."""

    key: str
    item_type: str
    name: str
    status: Literal["unchanged", "changed", "added", "removed"]
    from_revision: int | None
    to_revision: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "item_type": self.item_type,
            "name": self.name,
            "status": self.status,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "from_ref": f"{self.key}@{self.from_revision}" if self.from_revision else None,
            "to_ref": f"{self.key}@{self.to_revision}" if self.to_revision else None,
        }


def diff_baselines(a: list[BaselineItemRef], b: list[BaselineItemRef]) -> list[BaselineItemDiff]:
    """Per item: unchanged, changed (``@x -> @y``), added (only in ``b``) or removed."""
    left = {r.key: r for r in a}
    right = {r.key: r for r in b}
    out: list[BaselineItemDiff] = []
    for key in sorted(left.keys() | right.keys()):
        old, new = left.get(key), right.get(key)
        ref = new or old
        assert ref is not None
        if old is None:
            status: Literal["unchanged", "changed", "added", "removed"] = "added"
        elif new is None:
            status = "removed"
        elif old.revision == new.revision and old.node_id == new.node_id:
            status = "unchanged"
        else:
            status = "changed"
        out.append(
            BaselineItemDiff(
                key=key,
                item_type=ref.item_type,
                name=ref.name,
                status=status,
                from_revision=old.revision if old else None,
                to_revision=new.revision if new else None,
            )
        )
    return out
