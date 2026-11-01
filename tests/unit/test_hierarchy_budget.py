"""compute_budget_allocation_status (FORGE-264, gap G-B4) -- per-subsystem
budget allocation vs. actual rolled-up mass/cost.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.hierarchy_budget import compute_budget_allocation_status
from twin_core.consistency.models import Budget, BudgetAllocation
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def api():
    return InMemoryTwinAPI.create()


def _cad_model(name: str, mass_kg: float) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="deadbeef",
        format="step",
        created_by="test",
        metadata={"mass_kg": mass_kg},
    )


def _budget(metric: str, unit: str, allocations: list[BudgetAllocation]) -> Budget:
    return Budget(
        id="mass_budget",
        project_id=uuid4(),
        metric=metric,
        unit=unit,
        system_total=10.0,
        allocations=allocations,
    )


class TestComputeBudgetAllocationStatus:
    async def test_under_budget_branch(self, api):
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        part = await api.create_work_product(_cad_model("Base plate", 1.2))
        await api.add_edge(base.id, part.id, EdgeType.REALIZED_BY)

        budget = _budget("mass", "kg", [BudgetAllocation(target=str(base.id), amount=4.5)])
        results = await compute_budget_allocation_status(api, budget)

        assert len(results) == 1
        assert results[0].allocated == 4.5
        assert results[0].actual == 1.2
        assert results[0].over_budget is False

    async def test_over_budget_branch(self, api):
        """Matches the ticket's own acceptance yardstick: moving mass 5.16 /
        4.5 kg shown as over-budget on the arm node."""
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        part = await api.create_work_product(_cad_model("Base plate", 5.16))
        await api.add_edge(base.id, part.id, EdgeType.REALIZED_BY)

        budget = _budget("mass", "kg", [BudgetAllocation(target=str(base.id), amount=4.5)])
        results = await compute_budget_allocation_status(api, budget)

        assert results[0].actual == 5.16
        assert results[0].over_budget is True

    async def test_cost_metric_reads_the_cost_rollup(self, api):
        from twin_core.models.bom_item import BOMItem

        base = await api.create_hierarchy_node(HierarchyNode(name="Wrist", kind="subsystem"))
        bom = await api.add_bom_item(
            BOMItem(part_number="igus", manufacturer="igus", unit_cost=50.0)
        )
        await api.add_edge(base.id, bom.id, EdgeType.INSTANCE_OF)

        budget = _budget("cost", "usd", [BudgetAllocation(target=str(base.id), amount=40.0)])
        results = await compute_budget_allocation_status(api, budget)

        assert results[0].actual == 50.0
        assert results[0].over_budget is True

    async def test_free_text_target_cannot_be_checked(self, api):
        """A pre-existing free-text label (e.g. 'frame') degrades to
        can't-check, never a false pass -- backward compatible with every
        budget recorded before this ticket."""
        budget = _budget("mass", "kg", [BudgetAllocation(target="frame", amount=2.0)])
        results = await compute_budget_allocation_status(api, budget)

        assert results[0].actual is None
        assert results[0].over_budget is None

    async def test_unresolvable_uuid_target_cannot_be_checked(self, api):
        budget = _budget("mass", "kg", [BudgetAllocation(target=str(uuid4()), amount=2.0)])
        results = await compute_budget_allocation_status(api, budget)

        assert results[0].actual is None
        assert results[0].over_budget is None

    async def test_power_metric_cannot_be_checked_yet(self, api):
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        budget = _budget("power", "w", [BudgetAllocation(target=str(base.id), amount=10.0)])
        results = await compute_budget_allocation_status(api, budget)

        assert results[0].actual is None
        assert results[0].over_budget is None

    async def test_multiple_allocations_each_checked_independently(self, api):
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        shoulder = await api.create_hierarchy_node(HierarchyNode(name="Shoulder", kind="subsystem"))
        base_part = await api.create_work_product(_cad_model("Base plate", 1.0))
        shoulder_part = await api.create_work_product(_cad_model("Shoulder housing", 3.0))
        await api.add_edge(base.id, base_part.id, EdgeType.REALIZED_BY)
        await api.add_edge(shoulder.id, shoulder_part.id, EdgeType.REALIZED_BY)

        budget = _budget(
            "mass",
            "kg",
            [
                BudgetAllocation(target=str(base.id), amount=2.0),
                BudgetAllocation(target=str(shoulder.id), amount=2.0),
            ],
        )
        results = await compute_budget_allocation_status(api, budget)

        by_target = {r.target: r for r in results}
        assert by_target[str(base.id)].over_budget is False
        assert by_target[str(shoulder.id)].over_budget is True

    async def test_owner_and_discipline_pass_through(self, api):
        # FORGE-313
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        budget = _budget(
            "mass",
            "kg",
            [
                BudgetAllocation(
                    target=str(base.id), amount=2.0, owner="alice", discipline="mechanical"
                )
            ],
        )
        results = await compute_budget_allocation_status(api, budget)
        assert results[0].owner == "alice"
        assert results[0].discipline == "mechanical"

    async def test_owner_and_discipline_default_to_empty(self, api):
        base = await api.create_hierarchy_node(HierarchyNode(name="Base", kind="subsystem"))
        budget = _budget("mass", "kg", [BudgetAllocation(target=str(base.id), amount=2.0)])
        results = await compute_budget_allocation_status(api, budget)
        assert results[0].owner == ""
        assert results[0].discipline == ""
