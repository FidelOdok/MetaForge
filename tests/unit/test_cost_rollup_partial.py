"""FORGE-700: a cost rollup with unpriced BOM lines says so.

The sandbox e2e run reported a board cost that covered 3 of its 7 parts,
because an unpriced line added nothing and said nothing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.twin.hierarchy_routes import HierarchyNodeResponse
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.hierarchy_budget import (
    COST_PARTIAL,
    compute_budget_allocation_status,
)
from twin_core.consistency.hierarchy_rollup import compute_hierarchy_rollup
from twin_core.consistency.models import Budget, BudgetAllocation
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType
from twin_core.models.hierarchy_node import HierarchyNode


@pytest.fixture
def api():
    return InMemoryTwinAPI.create()


async def _board(api, prices: list[float | None], qty: float = 1):
    """A board node holding one position per price, each INSTANCE_OF a part."""
    board = await api.create_hierarchy_node(HierarchyNode(name="Board", kind="assembly"))
    for i, price in enumerate(prices):
        pos = await api.create_hierarchy_node(HierarchyNode(name=f"P{i}", kind="assembly"))
        await api.add_edge(board.id, pos.id, EdgeType.CONTAINS, {"quantity": qty})
        item = await api.add_bom_item(
            BOMItem(part_number=f"MPN-{i}", manufacturer="m", unit_cost=price)
        )
        await api.add_edge(pos.id, item.id, EdgeType.INSTANCE_OF)
    return board


def _cost_budget(target: str, amount: float) -> Budget:
    return Budget(
        id="cost_budget",
        project_id=uuid4(),
        metric="cost",
        unit="USD",
        system_total=100.0,
        allocations=[BudgetAllocation(target=target, amount=amount)],
    )


class TestRollupListsEveryLine:
    async def test_three_of_seven_priced_is_partial(self, api):
        board = await _board(api, [1.0, 2.0, 3.0, None, None, None, None])
        result = await compute_hierarchy_rollup(api, board.id)

        assert result.cost == pytest.approx(6.0)
        assert result.total_lines == 7
        assert result.priced_lines == 3
        assert result.cost_partial is True
        assert len(result.cost_lines) == 7
        assert sorted(line.part_number for line in result.unpriced_lines) == [
            "MPN-3",
            "MPN-4",
            "MPN-5",
            "MPN-6",
        ]
        assert all(line.extended_cost is None for line in result.unpriced_lines)

    async def test_fully_priced_is_not_partial(self, api):
        board = await _board(api, [1.0, 2.5])
        result = await compute_hierarchy_rollup(api, board.id)

        assert result.cost_partial is False
        assert result.priced_lines == result.total_lines == 2
        assert result.unpriced_lines == []

    async def test_line_quantity_carries_the_contains_multiplier(self, api):
        board = await _board(api, [0.5], qty=4)
        result = await compute_hierarchy_rollup(api, board.id)

        (line,) = result.cost_lines
        assert line.quantity == 4
        assert line.extended_cost == pytest.approx(2.0)
        assert result.cost == pytest.approx(2.0)

    async def test_no_bom_lines_is_not_partial(self, api):
        node = await api.create_hierarchy_node(HierarchyNode(name="Empty", kind="product"))
        result = await compute_hierarchy_rollup(api, node.id)

        assert result.total_lines == 0
        assert result.cost_partial is False


class TestPartialCostNeverPassesABudget:
    async def test_partial_under_allocation_is_undecided(self, api):
        board = await _board(api, [1.0, None])
        (status,) = await compute_budget_allocation_status(api, _cost_budget(str(board.id), 50.0))

        assert status.actual == pytest.approx(1.0)
        assert status.over_budget is None
        assert status.reason == COST_PARTIAL
        assert "1 of 2" in status.detail
        assert "MPN-1" in status.detail

    async def test_partial_already_over_allocation_is_over(self, api):
        board = await _board(api, [80.0, None])
        (status,) = await compute_budget_allocation_status(api, _cost_budget(str(board.id), 50.0))

        assert status.over_budget is True
        assert status.reason == COST_PARTIAL

    async def test_fully_priced_is_checked_as_before(self, api):
        board = await _board(api, [10.0, 5.0])
        (status,) = await compute_budget_allocation_status(api, _cost_budget(str(board.id), 50.0))

        assert status.over_budget is False
        assert status.reason == ""


def test_hierarchy_response_carries_the_coverage_fields():
    fields = HierarchyNodeResponse.model_fields
    assert {"costPricedLines", "costTotalLines", "costPartial"} <= set(fields)
