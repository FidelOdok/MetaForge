"""Per-subsystem budget allocation status (FORGE-264, gap G-B4).

``BudgetEngine.compute_status`` (``budgets.py``) answers "is the whole
project over its mass/cost budget" -- a flat, project-wide question. This
module answers the complementary, per-branch question "allocation to
subsystems" actually asks for: is a specific hierarchy branch's ACTUAL
rolled-up mass/cost (``compute_hierarchy_rollup``, FORGE-260) over the
amount its budget allocation says it should have.

``Budget.allocations[].target`` is documented (``models.py``) as a
free-text label ("frame", "actuators", ...) -- reinterpreted here, not
replaced, as a HierarchyNode id when a caller stores one there (see
``twin.record_engineering_entity``'s updated tool description). A target
that isn't a valid UUID, or is a UUID that doesn't resolve to a real
hierarchy node, degrades to "can't check" (``actual=None``,
``over_budget=None``) -- never a false pass, matching the graceful-degrade
discipline ``gates.py``'s own malformed-entity handling already uses.

Only ``mass``/``cost`` metrics can be checked this way -- ``power`` (also
named in this gap's own capability text) has no rollup source yet
(``compute_hierarchy_rollup`` doesn't track it), so it always degrades to
"can't check" too, same as an unresolvable target.

FORGE-345: those degradations are no longer the *same* "can't check".
``AllocationStatus.reason`` says which of the three happened, because
"this metric will never be checkable", "you wrote a label where a node id
goes" and "that node id points at nothing" call for different actions and
were arriving as one identical blank cell.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from twin_core.api import TwinAPI
from twin_core.consistency.hierarchy_rollup import compute_hierarchy_rollup
from twin_core.consistency.models import Budget

_METRIC_ROLLUP_FIELD = {"mass": "mass_kg", "cost": "cost"}


class AllocationStatus(BaseModel):
    """One allocation's actual-vs-allocated status."""

    target: str
    allocated: float
    actual: float | None
    over_budget: bool | None
    # FORGE-313: pass-through of BudgetAllocation.owner/.discipline, so a
    # caller (hierarchy_routes.py) doesn't need its own copy of the Budget
    # to know who owns an over-budget subsystem.
    owner: str = ""
    discipline: str = ""
    reason: str = ""
    """Why ``actual`` is None, when it is. Empty when the check ran.

    FORGE-345. Three unrelated situations used to arrive as the same blank
    cell: a metric no rollup can compute, a target that is not a node id at
    all, and a target id that resolves to nothing. They need different
    things done about them -- wait for a feature, fix a typo, repair a
    dangling reference -- and an engineer looking at the allocation table
    could not tell which they were looking at.
    """


#: Why a rollup could not be computed. Values are stable strings so a UI
#: can branch on them without parsing prose.
UNSUPPORTED_METRIC = "metric_has_no_rollup"
TARGET_NOT_A_NODE_ID = "target_is_not_a_node_id"
TARGET_NOT_FOUND = "target_node_not_found"


async def compute_budget_allocation_status(twin: TwinAPI, budget: Budget) -> list[AllocationStatus]:
    """Per-allocation status for ``budget``, resolving each allocation's
    ``target`` against a real HierarchyNode when it's a valid UUID."""
    rollup_field = _METRIC_ROLLUP_FIELD.get(budget.metric)
    results: list[AllocationStatus] = []
    for allocation in budget.allocations:
        actual: float | None = None
        reason = ""
        if rollup_field is None:
            # e.g. `power`, which this gap's own capability text names but
            # which has no rollup source: no graph node carries a power
            # value the tree walk could read. Saying so beats a blank that
            # looks like missing data somebody could go and enter.
            reason = UNSUPPORTED_METRIC
        else:
            try:
                target_id = UUID(allocation.target)
            except ValueError:
                # `target` is documented as a free-text label ("frame"),
                # reinterpreted as a node id when a caller stores one. A
                # label is not a mistake -- it just cannot be checked.
                reason = TARGET_NOT_A_NODE_ID
            else:
                try:
                    rollup = await compute_hierarchy_rollup(twin, target_id)
                except KeyError:
                    # A UUID that resolves to nothing, or to something that
                    # is not a HierarchyNode. Unlike the case above this is
                    # a broken reference, and worth repairing.
                    reason = TARGET_NOT_FOUND
                else:
                    actual = getattr(rollup, rollup_field)
        over_budget = actual > allocation.amount if actual is not None else None
        results.append(
            AllocationStatus(
                target=allocation.target,
                allocated=allocation.amount,
                actual=actual,
                over_budget=over_budget,
                owner=allocation.owner,
                discipline=allocation.discipline,
                reason=reason,
            )
        )
    return results
