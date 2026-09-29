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

FORGE-311: the exact-key match alone silently missed a WorkProduct that
stored the same physical quantity under a different unit (``"mass_g"``
when the metric's declared unit was ``"kg"``) -- indistinguishable from
genuinely absent data. This now also looks for any OTHER
``f"{metric}_<unit>"`` key and, via ``Quantity``, converts it when the
units are dimensionally compatible, and raises ``IncompatibleUnitsError``
(a ``ValueError``) when they're not -- a real dimension mismatch is a data
error worth surfacing, never a silent zero. Callers that need the
established graceful-degrade behavior on a mismatch (never crash a gate
evaluation) catch that at their own level, same as they already do for a
malformed ``budget``/``invariant`` entity (see ``twin_core.consistency.
gates``).
"""

from __future__ import annotations

from uuid import UUID

from twin_core.constraint_engine.context import build_context
from twin_core.graph_engine import GraphEngine
from twin_core.models.quantity import IncompatibleUnitsError, Quantity, is_valid_unit


async def compute_metric_total(
    graph: GraphEngine, project_id: UUID, metric: str, unit: str
) -> float:
    """Sum ``metadata[f"{metric}_{unit}"]`` (or a dimensionally-compatible
    alternate-unit key, converted) across `project_id`'s WorkProducts.

    A WorkProduct missing any matching key, or a project with no matching
    WorkProducts at all, contributes 0 -- not an error. A non-numeric value
    stored under a matching key is skipped (not silently coerced to 0 -- see
    ``metric_total_with_skips`` if you need to know skips happened). A
    matching key whose unit is a genuinely different physical dimension
    raises ``IncompatibleUnitsError``.
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
    exact_key = f"{metric}_{unit}"
    prefix = f"{metric}_"
    total = 0.0
    skipped: list[UUID] = []
    for wp in ctx.work_products():
        if wp.project_id != project_id:
            continue
        if exact_key in wp.metadata:
            key, stored_unit = exact_key, unit
        else:
            key, stored_unit = _find_alternate_unit_key(wp.metadata, prefix)
            if key is None:
                continue
        raw_value = wp.metadata[key]
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            skipped.append(wp.id)
            continue
        if stored_unit == unit:
            total += numeric
            continue
        try:
            total += Quantity(value=numeric, unit=stored_unit).to(unit).value
        except IncompatibleUnitsError as exc:
            raise IncompatibleUnitsError(
                stored_unit, unit, context=f"metric {metric!r} on work product {wp.id}"
            ) from exc
    return total, skipped


def _find_alternate_unit_key(metadata: dict, prefix: str) -> tuple[str | None, str | None]:
    """A metadata key matching ``f"{prefix}<unit>"`` whose suffix is a real
    unit string -- sorted for deterministic selection when more than one
    exists (an unusual case: the same WorkProduct storing the same metric
    under two different units)."""
    for key in sorted(metadata):
        if not key.startswith(prefix) or key == prefix:
            continue
        candidate_unit = key[len(prefix) :]
        if is_valid_unit(candidate_unit):
            return key, candidate_unit
    return None, None
