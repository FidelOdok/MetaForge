"""Per-branch mass/cost rollup over the product hierarchy (FORGE-260, gap G-B1).

Distinct from ``twin_core.consistency.metrics.compute_metric_total``, which
sums a metadata key flatly across every WorkProduct in a *project* (no
hierarchy awareness at all -- used by the G3 budget/invariant gate for one
project-wide total). This module walks a *branch* of the hierarchy tree
rooted at one HierarchyNode, so "what's the moving mass of just the Upper
Arm subsystem" is answerable, not only "what's the whole project's mass."

Sources read, never re-instrumented (FORGE-260 deliberately adds zero new
fields to WorkProduct/BOMItem):

- ``WorkProduct.metadata["mass_kg"]`` (the FORGE-100 canonical measured-
  property key, already populated on any committed ``cad_model``), read
  through each node's ``EdgeType.REALIZED_BY`` targets.
- ``BOMItem.unit_cost``, read through each node's ``EdgeType.INSTANCE_OF``
  target (a COTS leaf's canonical component record).
- Each ``EdgeType.CONTAINS`` edge's own ``metadata["quantity"]`` (default 1)
  weights that child's whole rolled-up subtotal, not just its own mass --
  3 identical gripper fingers each carrying an upstream assembly of their
  own all multiply through correctly.

A value present but non-numeric is skipped, not coerced (same discipline
``metric_total_with_skips`` uses) -- a rollup silently returning 0 for bad
data would look identical to "genuinely no mass yet," which is exactly the
"no data" vs "pass" distinction the Structure tab (FORGE-261) needs to
render honestly.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from twin_core.api import TwinAPI
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct

_ROLLUP_EDGE_TYPES = [EdgeType.CONTAINS, EdgeType.REALIZED_BY, EdgeType.INSTANCE_OF]
# Generous enough for any real product hierarchy (Product -> System ->
# Subsystem -> Assembly -> Part, plus one hop for its REALIZED_BY/
# INSTANCE_OF leaf link) without an unbounded/configurable depth this
# module doesn't need yet.
_MAX_ROLLUP_DEPTH = 20


class PowerAssumption(BaseModel):
    """A dissipation figure that was assumed rather than stated (FORGE-390).

    A component with a draw and no ``output_w`` has its dissipation
    defaulted to its full draw. That is the right default -- most
    electronics turn nearly all of their draw into heat -- and it is
    wrong for a motor, an LED or a transmitter, where much of the draw
    leaves as work, light or RF.

    So it is recorded rather than applied silently, per F3: an unknown is
    an assumption, never data.
    """

    node_id: UUID
    draw_w: float
    reason: str = "no output_w declared; dissipation assumed equal to draw"


class HierarchyRollup(BaseModel):
    """The computed mass/cost/power totals for one hierarchy branch."""

    root_id: UUID
    mass_kg: float
    cost: float
    #: Power TAKEN FROM THE SUPPLY, peak and average tracked apart. Peak is
    #: what brown-outs and inrush are judged against; average is what
    #: battery life is. A single number cannot answer both.
    draw_peak_w: float = 0.0
    draw_average_w: float = 0.0
    #: Power that LEAVES the product usefully -- shaft work, light, RF.
    output_w: float = 0.0
    #: Heat that STAYS IN, derived as draw - output (FORGE-390).
    #:
    #: Deliberately not the same number as draw. Merging the two gets one
    #: budget wrong every time: thermal is overestimated for anything that
    #: does useful work, or supply is underestimated if you size from heat.
    dissipation_w: float = 0.0
    node_count: int
    """HierarchyNode count in the branch (root included) -- NOT the count of
    REALIZED_BY/INSTANCE_OF leaves, which can outnumber or undernumber it."""
    skipped_node_ids: list[UUID]
    """Nodes whose linked mass_kg/unit_cost existed but wasn't numeric --
    contributed 0, distinct from a node with no value at all."""
    power_assumptions: list[PowerAssumption] = []
    """Nodes whose dissipation was assumed from draw for want of an
    ``output_w``. Empty when every contributor declared one."""


async def compute_hierarchy_rollup(twin: TwinAPI, root_id: UUID) -> HierarchyRollup:
    """Sum mass/cost over the CONTAINS tree rooted at ``root_id``.

    Raises ``KeyError`` if ``root_id`` doesn't exist (mirrors
    ``get_subgraph``'s own contract) or isn't a HierarchyNode.
    """
    subgraph = await twin.get_subgraph(
        root_id, depth=_MAX_ROLLUP_DEPTH, edge_types=_ROLLUP_EDGE_TYPES, direction="outgoing"
    )
    nodes_by_id = {n.id: n for n in subgraph.nodes}
    if not isinstance(nodes_by_id.get(root_id), HierarchyNode):
        raise KeyError(f"{root_id} is not a HierarchyNode")
    contains_children: dict[UUID, list[tuple[UUID, float]]] = {}
    realized_by: dict[UUID, list[UUID]] = {}
    instance_of: dict[UUID, list[UUID]] = {}
    for edge in subgraph.edges:
        if edge.edge_type == EdgeType.CONTAINS:
            qty = edge.metadata.get("quantity", 1)
            qty = qty if isinstance(qty, (int, float)) else 1
            contains_children.setdefault(edge.source_id, []).append((edge.target_id, qty))
        elif edge.edge_type == EdgeType.REALIZED_BY:
            realized_by.setdefault(edge.source_id, []).append(edge.target_id)
        elif edge.edge_type == EdgeType.INSTANCE_OF:
            instance_of.setdefault(edge.source_id, []).append(edge.target_id)

    skipped: list[UUID] = []
    assumptions: list[PowerAssumption] = []
    visited_for_count: set[UUID] = set()

    def _number(source: dict[str, Any], key: str, node_id: UUID) -> float | None:
        """A numeric field, or None. Non-numeric is skipped, never coerced."""
        value = source.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            skipped.append(node_id)
            return None

    def _power_of(source: dict[str, Any], node_id: UUID) -> tuple[float, float, float, float]:
        """``(peak, average, output, dissipation)`` for one contributor.

        ``draw_w`` on its own counts as both peak and average: a caller who
        gave one number has not distinguished them, and silently treating it
        as only one would understate the other budget.
        """
        flat = _number(source, "draw_w", node_id)
        peak = _number(source, "draw_peak_w", node_id)
        average = _number(source, "draw_average_w", node_id)
        if peak is None:
            peak = flat
        if average is None:
            average = flat
        output = _number(source, "output_w", node_id)

        if peak is None and average is None and output is None:
            return 0.0, 0.0, 0.0, 0.0

        peak = peak or 0.0
        average = average or 0.0

        # Dissipation is derived, not read: a stated dissipation that
        # disagrees with draw - output is two sources of truth.
        #
        # Which draw to derive from: average. Thermal capacity is about
        # steady-state heat, and sizing an enclosure for peak draw would
        # over-specify it for every product with an inrush.
        basis = average if average else peak
        if output is None:
            dissipation = basis
            if basis:
                assumptions.append(PowerAssumption(node_id=node_id, draw_w=basis))
        else:
            dissipation = max(basis - output, 0.0)
        return peak, average, output or 0.0, dissipation

    def _own(node_id: UUID) -> tuple[float, float, float, float, float, float]:
        """``(mass, cost, peak, average, output, dissipation)`` for one node."""
        mass = 0.0
        cost = 0.0
        peak = average = output = dissipation = 0.0
        for target_id in realized_by.get(node_id, []):
            target = nodes_by_id.get(target_id)
            if not isinstance(target, WorkProduct):
                continue
            value = target.metadata.get("mass_kg")
            if value is not None:
                try:
                    mass += float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    skipped.append(target_id)
            # FORGE-390: a subsystem's own work product can carry power --
            # an electronics design states a rail's draw without there
            # being a BOM line for it.
            p, a, o, d = _power_of(target.metadata, target_id)
            peak += p
            average += a
            output += o
            dissipation += d
        for target_id in instance_of.get(node_id, []):
            target = nodes_by_id.get(target_id)
            if not isinstance(target, BOMItem):
                continue
            if target.unit_cost is not None:
                cost += target.unit_cost
            # And a COTS part carries its own, on the specifications the
            # component-selection path already writes.
            p, a, o, d = _power_of(target.specifications, target_id)
            peak += p
            average += a
            output += o
            dissipation += d
        return mass, cost, peak, average, output, dissipation

    def _rollup(node_id: UUID) -> tuple[float, float, float, float, float, float]:
        visited_for_count.add(node_id)
        totals = list(_own(node_id))
        for child_id, qty in contains_children.get(node_id, []):
            child = _rollup(child_id)
            for i, value in enumerate(child):
                totals[i] += value * qty
        return (totals[0], totals[1], totals[2], totals[3], totals[4], totals[5])

    mass, cost, peak, average, output, dissipation = _rollup(root_id)
    return HierarchyRollup(
        root_id=root_id,
        mass_kg=mass,
        cost=cost,
        draw_peak_w=peak,
        draw_average_w=average,
        output_w=output,
        dissipation_w=dissipation,
        node_count=len(visited_for_count),
        skipped_node_ids=skipped,
        power_assumptions=assumptions,
    )
