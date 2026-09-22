"""BudgetEngine (FORGE-57, spec section 18 Constraint Propagation / Budget
Allocation).

"Continuously calculated: allocated, actual, remaining, margin, violation."
``allocated`` is the declared breakdown (``Budget.allocations``, a static
sum of numbers the caller already entered); ``actual`` is the REAL current
total read from the graph (same metric-total convention as
``InvariantEngine``) -- the two can legitimately disagree (a component's
real mass can be over or under its allocation), which is exactly what
``margin`` and ``violation`` surface.

``budget_from_entity`` (FORGE-73, budget/invariant persistence) reads a
``Budget`` back out of a persisted ``EngineeringEntity(entity_type="budget")``
-- same ``metadata``-holds-the-type-specific-fields convention as
``objectives.objective_from_entity``, not a new node type. This is what
closes the gap this module's docstring used to name: "there is still no
per-project ... persistence anywhere in the codebase" -- an agent now
declares one via ``twin.record_engineering_entity`` and
``twin_core.consistency.gates.evaluate_g3_feasibility`` loads it
automatically when the caller doesn't supply ``budgets`` explicitly.
"""

from __future__ import annotations

from uuid import UUID

from twin_core.consistency.metrics import compute_metric_total
from twin_core.consistency.models import Budget, BudgetAllocation, BudgetStatus
from twin_core.graph_engine import GraphEngine
from twin_core.models.engineering_entity import EngineeringEntity


def budget_from_entity(entity: EngineeringEntity, project_id: UUID) -> Budget:
    """Read a Budget's fields back out of an EngineeringEntity's metadata
    (spec section 48's schema, stored per Phase 1's convention). ``id``
    comes from the entity's ``title`` (falling back to its node id) so a
    gate's ``budget:<id>`` check label stays human-readable -- give a
    declared budget a stable title like ``"mass_budget"``.
    """
    if entity.entity_type != "budget":
        raise ValueError(f"entity {entity.id} is not a budget (got {entity.entity_type!r})")
    md = entity.metadata
    missing = [k for k in ("metric", "unit", "system_total") if k not in md]
    if missing:
        raise ValueError(f"budget entity {entity.id} metadata missing {missing}")
    try:
        allocations = [BudgetAllocation(**a) for a in md.get("allocations", [])]
        system_total = float(md["system_total"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"budget entity {entity.id} has malformed metadata: {exc}") from exc
    return Budget(
        id=entity.title or str(entity.id),
        project_id=project_id,
        metric=str(md["metric"]),
        unit=str(md["unit"]),
        system_total=system_total,
        allocations=allocations,
    )


class BudgetEngine:
    def __init__(self, graph: GraphEngine) -> None:
        self._graph = graph

    async def compute_status(self, budget: Budget) -> BudgetStatus:
        allocated = sum(a.amount for a in budget.allocations)
        actual = await compute_metric_total(
            self._graph, budget.project_id, budget.metric, budget.unit
        )
        return BudgetStatus(
            budget_id=budget.id,
            system_total=budget.system_total,
            allocated=allocated,
            actual=actual,
            remaining=budget.system_total - allocated,
            margin=budget.system_total - actual,
            violation=actual > budget.system_total,
        )
