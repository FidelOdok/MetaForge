"""Changing a running flow without starting it again (FORGE-539).

A design run that learns something mid-way (the payload went from 5 kg to
15 kg, the battery is heavier, the user now wants it waterproof) used to have
two options: a rework back to an earlier phase, which re-runs that phase and
everything after it, or a new run from the intent. Both throw away work the
change did not touch.

A patch is the third option. It is a set of ordinary tailoring operations
applied to the run's *current* flow, plus any phases whose results the new
information invalidates. The plan computes exactly which phases must run
again: every phase the patch changed or invalidated, and everything that
depends on them. Everything else keeps its result and its approval.

The patch carries the content hash of the flow it was written against. If
the run's flow has moved on since (another patch applied first), the patch
is stale and refused rather than applied on top of something its author
never saw. Like a proposal, a patch is held for a person; nothing here
applies anything.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from orchestrator.design_flow.generator import (
    Operation,
    apply_operations,
    parse_caller_operations,
)
from orchestrator.design_flow.graph import ConditionError, GraphError, build_graph
from orchestrator.design_flow.invariants import ValidationResult, validate_flow
from orchestrator.design_flow.spec import FlowDefinition

__all__ = ["PatchPlan", "StalePatchError", "plan_patch"]


class StalePatchError(ValueError):
    """The run's flow is no longer the one the patch was written against."""


@dataclass(frozen=True)
class PatchPlan:
    definition: FlowDefinition
    operations: tuple[Operation, ...]
    validation: ValidationResult
    #: Phases that must run again: changed or invalidated, plus everything
    #: downstream of them, in flow order.
    rerun: tuple[str, ...]
    #: Completed phases whose results and approvals survive the patch.
    preserved: tuple[str, ...]
    #: Phases the patch removed.
    removed: tuple[str, ...] = ()
    #: Phases the patch added.
    added: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return self.validation.ok

    def diff(self, reason: str) -> list[str]:
        lines = [f"{op.describe()} — {op.full_rationale()}" for op in self.operations]
        if self.rerun:
            lines.append(f"re-run {', '.join(self.rerun)} — {reason}")
        if self.preserved:
            lines.append(f"keep {', '.join(self.preserved)} — unaffected by this change")
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "violations": [str(v) for v in self.validation.violations],
            "rerun": list(self.rerun),
            "preserved": list(self.preserved),
            "removed": list(self.removed),
            "added": list(self.added),
            "notes": list(self.notes),
        }


def plan_patch(
    current: FlowDefinition,
    *,
    current_hash: str,
    expected_hash: str,
    operations: Any,
    invalidate: Sequence[str] = (),
    completed: Sequence[str] = (),
) -> PatchPlan:
    """Plan a patch to ``current``. Raises :class:`StalePatchError` or
    :class:`~orchestrator.design_flow.generator.TailoringError`.

    ``operations`` are caller operations, checked strictly (an unknown phase
    or operation is refused, not dropped). ``invalidate`` names phases whose
    results the new information makes wrong even though their definition did
    not change. ``completed`` is what the run has finished so far.
    """
    if expected_hash != current_hash:
        raise StalePatchError(
            f"this patch was written against flow {expected_hash[:12]}, but the run is now on "
            f"{current_hash[:12]}; reload the run's flow and write the patch again"
        )
    ids = [p.id for p in current.phases]
    unknown = [p for p in invalidate if p not in ids]
    if unknown:
        raise StalePatchError(
            f"cannot invalidate unknown phase(s) {unknown}; phases: {', '.join(ids)}"
        )

    parsed = parse_caller_operations(operations, current)
    tailored, applied = apply_operations(current, parsed)
    validation = validate_flow(tailored)

    before = {p.id: p for p in current.phases}
    after = {p.id: p for p in tailored.phases}
    removed = tuple(p for p in ids if p not in after)
    added = tuple(p.id for p in tailored.phases if p.id not in before)
    changed = {pid for pid, phase in after.items() if pid in before and before[pid] != phase}
    seeds = (changed | set(added) | set(invalidate)) - set(removed)

    notes: list[str] = []
    try:
        graph = build_graph(tailored.phases)
    except (GraphError, ConditionError):
        # validate_flow already reports it; without a graph, re-run every
        # seed and everything after the first one, the conservative answer.
        order = [p.id for p in tailored.phases]
        first = min((order.index(s) for s in seeds), default=len(order))
        rerun = order[first:]
        notes.append("the patched flow's graph is invalid, so the re-run set is conservative")
    else:
        hit: set[str] = set()
        for seed in seeds:
            hit.update(graph.downstream(seed))
        rerun = [p for p in graph.order if p in hit]
    done = [p for p in completed if p in after]
    preserved = tuple(p for p in done if p not in rerun)
    if not seeds:
        notes.append("the patch changes nothing that has run; no phase is re-run")
    return PatchPlan(
        definition=tailored,
        operations=tuple(applied),
        validation=validation,
        rerun=tuple(rerun),
        preserved=preserved,
        removed=removed,
        added=added,
        notes=tuple(notes),
    )
