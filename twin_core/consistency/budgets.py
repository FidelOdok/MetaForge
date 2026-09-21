"""BudgetEngine (FORGE-57, spec section 18 Constraint Propagation / Budget
Allocation).

"Continuously calculated: allocated, actual, remaining, margin, violation."
``allocated`` is the declared breakdown (``Budget.allocations``, a static
sum of numbers the caller already entered); ``actual`` is the REAL current
total read from the graph (same metric-total convention as
``InvariantEngine``) -- the two can legitimately disagree (a component's
real mass can be over or under its allocation), which is exactly what
``margin`` and ``violation`` surface.
"""

from __future__ import annotations

from twin_core.consistency.metrics import compute_metric_total
from twin_core.consistency.models import Budget, BudgetStatus
from twin_core.graph_engine import GraphEngine


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
