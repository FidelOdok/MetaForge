"""Shared metric-total computation for Invariant/Budget (FORGE-57).

A metric's current value is the sum of ``metadata[f"{metric}_{unit}"]``
across a project's WorkProducts, reading through
``twin_core.constraint_engine.context.build_context`` -- the same
pre-loaded, read-only graph snapshot the existing constraint expression
evaluator already builds for ``ctx.work_products()`` (twin_core/constraint_
engine/context.py). Not a new graph read path.

Known limitation, inherited rather than newly introduced: ``build_context``
loads WorkProducts globally with no project filter (pre-existing behavior
of the constraint engine it's borrowed from), so this module filters to
`project_id` itself after loading -- correct results, just not as cheap as
a project-scoped query would be for a very large multi-project graph.
"""

from __future__ import annotations

from uuid import UUID

from twin_core.constraint_engine.context import build_context
from twin_core.graph_engine import GraphEngine


async def compute_metric_total(
    graph: GraphEngine, project_id: UUID, metric: str, unit: str
) -> float:
    """Sum ``metadata[f"{metric}_{unit}"]`` across `project_id`'s WorkProducts.

    A WorkProduct missing the key, or a project with no matching
    WorkProducts at all, contributes 0 -- not an error. A non-numeric value
    stored under that key is skipped (not silently coerced to 0 -- see
    ``metric_total_with_skips`` if you need to know skips happened).
    """
    total, _skipped = await metric_total_with_skips(graph, project_id, metric, unit)
    return total


async def metric_total_with_skips(
    graph: GraphEngine, project_id: UUID, metric: str, unit: str
) -> tuple[float, list[UUID]]:
    """Same as ``compute_metric_total`` but also returns the ids of
    WorkProducts whose metadata value existed but wasn't numeric -- callers
    that care about silently-skipped data (rather than genuinely absent
    data) can surface it instead of treating a skip as a clean zero."""
    ctx = await build_context(graph)
    key = f"{metric}_{unit}"
    total = 0.0
    skipped: list[UUID] = []
    for wp in ctx.work_products():
        if wp.project_id != project_id:
            continue
        if key not in wp.metadata:
            continue
        value = wp.metadata[key]
        try:
            total += float(value)
        except (TypeError, ValueError):
            skipped.append(wp.id)
    return total, skipped
