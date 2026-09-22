"""InvariantEngine (FORGE-57, spec section 17 Runtime Invariants).

``evaluate`` computes each Invariant's CURRENT value from the graph.
``predict`` answers the spec's own worked example directly: given a
proposed delta, is ``current + delta`` still within the limit -- BEFORE
anything commits. Note the caller supplies the delta explicitly; this
engine does not attempt to derive one automatically from a Patch
(twin_core.models.patch, FORGE-50) — FORGE-50's Patch operates on
Constraint/EngineeringEntity nodes, not WorkProduct metadata, so there is
no existing mechanism that would let this engine compute "how much would
this patch change total mass" on its own. A caller that already knows its
proposed metadata change (a mechanical agent about to bump a CAD model's
mass_g, for instance) can call ``predict`` with that number today; wiring
an automatic WorkProduct-delta-from-patch pipeline is its own, larger
integration (closer to Phase 7's Engineering Change Transactions) and is
not guessed into this pass.
"""

from __future__ import annotations

from uuid import UUID

from twin_core.consistency.metrics import compute_metric_total
from twin_core.consistency.models import Invariant, InvariantComparison, InvariantResult
from twin_core.graph_engine import GraphEngine
from twin_core.models.engineering_entity import EngineeringEntity


def invariant_from_entity(entity: EngineeringEntity) -> Invariant:
    """Read an Invariant's fields back out of an EngineeringEntity's
    metadata (spec section 17's schema, stored per Phase 1's convention;
    same ``objective_from_entity``-style conversion FORGE-73 adds for
    "budget" too). ``id`` comes from the entity's ``title`` (falling back
    to its node id) so a gate's ``invariant:<id>`` check label stays
    human-readable -- give a declared invariant a stable title like
    ``"INV-MASS"``.
    """
    if entity.entity_type != "invariant":
        raise ValueError(f"entity {entity.id} is not an invariant (got {entity.entity_type!r})")
    md = entity.metadata
    missing = [k for k in ("metric", "unit", "limit") if k not in md]
    if missing:
        raise ValueError(f"invariant entity {entity.id} metadata missing {missing}")
    try:
        limit = float(md["limit"])
        comparison = InvariantComparison(md.get("comparison", InvariantComparison.LTE.value))
        source_raw = md.get("source_constraint_id")
        source_constraint_id = UUID(str(source_raw)) if source_raw else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invariant entity {entity.id} has malformed metadata: {exc}") from exc
    return Invariant(
        id=entity.title or str(entity.id),
        metric=str(md["metric"]),
        unit=str(md["unit"]),
        limit=limit,
        comparison=comparison,
        source_constraint_id=source_constraint_id,
    )


def _violates(comparison: InvariantComparison, value: float, limit: float) -> bool:
    if comparison == InvariantComparison.LTE:
        return value > limit
    if comparison == InvariantComparison.GTE:
        return value < limit
    return value != limit


class InvariantEngine:
    def __init__(self, graph: GraphEngine) -> None:
        self._graph = graph

    async def evaluate(
        self, project_id: UUID, invariants: list[Invariant]
    ) -> list[InvariantResult]:
        return [await self._evaluate_one(project_id, inv, delta=0.0) for inv in invariants]

    async def predict(
        self, project_id: UUID, invariant: Invariant, delta: float
    ) -> InvariantResult:
        """ "current mass = 4.31 kg; proposed delta = +0.92 kg; predicted =
        5.23 kg -> violation -> block transaction" (spec section 17)."""
        return await self._evaluate_one(project_id, invariant, delta)

    async def _evaluate_one(
        self, project_id: UUID, invariant: Invariant, delta: float
    ) -> InvariantResult:
        current = await compute_metric_total(
            self._graph, project_id, invariant.metric, invariant.unit
        )
        predicted = current + delta
        return InvariantResult(
            invariant_id=invariant.id,
            current=current,
            delta=delta,
            predicted=predicted,
            limit=invariant.limit,
            comparison=invariant.comparison,
            violated=_violates(invariant.comparison, predicted, invariant.limit),
        )
