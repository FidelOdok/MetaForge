"""Unit tests for InvariantEngine/BudgetEngine (FORGE-57)."""

from uuid import uuid4

import pytest

from twin_core.consistency import (
    Budget,
    BudgetAllocation,
    BudgetEngine,
    Invariant,
    InvariantComparison,
    InvariantEngine,
)
from twin_core.consistency.metrics import compute_metric_total, metric_total_with_skips
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import WorkProduct, WorkProductType


def _wp(name: str, project_id, metadata: dict | None = None) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path=f"{name}.step",
        content_hash="h",
        format="step",
        created_by="user",
        project_id=project_id,
        metadata=metadata or {},
    )


@pytest.fixture
def graph():
    return InMemoryGraphEngine()


@pytest.fixture
def project_id():
    return uuid4()


class TestInvariantEngineEvaluate:
    async def test_current_value_sums_across_project_work_products(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 1.2}))
        await graph.add_node(_wp("battery", project_id, {"mass_kg": 0.8}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).evaluate(project_id, [inv])
        assert result[0].current == pytest.approx(2.0)
        assert result[0].violated is False

    async def test_ignores_work_products_from_other_projects(self, graph, project_id):
        other_project = uuid4()
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 1.0}))
        await graph.add_node(_wp("other_project_part", other_project, {"mass_kg": 100.0}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).evaluate(project_id, [inv])
        assert result[0].current == pytest.approx(1.0)

    async def test_missing_metadata_key_contributes_zero(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).evaluate(project_id, [inv])
        assert result[0].current == 0.0
        assert result[0].violated is False

    async def test_lte_violation(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 6.0}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).evaluate(project_id, [inv])
        assert result[0].violated is True

    async def test_gte_violation(self, graph, project_id):
        await graph.add_node(_wp("battery", project_id, {"runtime_min": 20.0}))
        inv = Invariant(
            id="INV-RUNTIME",
            metric="runtime",
            unit="min",
            limit=30.0,
            comparison=InvariantComparison.GTE,
        )
        result = await InvariantEngine(graph).evaluate(project_id, [inv])
        assert result[0].violated is True

    async def test_eq_violation(self, graph, project_id):
        await graph.add_node(_wp("pcb", project_id, {"layers_count": 3.0}))
        inv = Invariant(
            id="INV-LAYERS",
            metric="layers",
            unit="count",
            limit=4.0,
            comparison=InvariantComparison.EQ,
        )
        result = await InvariantEngine(graph).evaluate(project_id, [inv])
        assert result[0].violated is True


class TestInvariantEnginePredict:
    async def test_doc_worked_example(self, graph, project_id):
        """spec section 17: current 4.31kg + delta 0.92kg = predicted 5.23kg -> violation."""
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 4.31}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).predict(project_id, inv, delta=0.92)
        assert result.current == pytest.approx(4.31)
        assert result.predicted == pytest.approx(5.23)
        assert result.violated is True

    async def test_predict_within_limit_is_not_a_violation(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 3.0}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).predict(project_id, inv, delta=0.5)
        assert result.predicted == pytest.approx(3.5)
        assert result.violated is False

    async def test_negative_delta_can_resolve_an_existing_violation(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 6.0}))
        inv = Invariant(id="INV-MASS", metric="mass", unit="kg", limit=5.0)
        result = await InvariantEngine(graph).predict(project_id, inv, delta=-2.0)
        assert result.predicted == pytest.approx(4.0)
        assert result.violated is False


class TestBudgetEngine:
    async def test_allocated_is_the_sum_of_declared_allocations(self, graph, project_id):
        budget = Budget(
            id="BUD-MASS",
            project_id=project_id,
            metric="mass",
            unit="kg",
            system_total=5.0,
            allocations=[
                BudgetAllocation(target="frame", amount=1.0),
                BudgetAllocation(target="battery", amount=0.8),
            ],
        )
        status = await BudgetEngine(graph).compute_status(budget)
        assert status.allocated == pytest.approx(1.8)
        assert status.remaining == pytest.approx(3.2)

    async def test_actual_is_read_from_the_graph_independent_of_allocations(
        self, graph, project_id
    ):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 1.5}))
        budget = Budget(
            id="BUD-MASS",
            project_id=project_id,
            metric="mass",
            unit="kg",
            system_total=5.0,
            allocations=[BudgetAllocation(target="frame", amount=1.0)],
        )
        status = await BudgetEngine(graph).compute_status(budget)
        assert status.actual == pytest.approx(1.5)  # real, not the 1.0 allocation
        assert status.margin == pytest.approx(3.5)

    async def test_violation_when_actual_exceeds_system_total(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 6.0}))
        budget = Budget(
            id="BUD-MASS", project_id=project_id, metric="mass", unit="kg", system_total=5.0
        )
        status = await BudgetEngine(graph).compute_status(budget)
        assert status.violation is True

    async def test_no_violation_when_actual_is_within_system_total(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 4.0}))
        budget = Budget(
            id="BUD-MASS", project_id=project_id, metric="mass", unit="kg", system_total=5.0
        )
        status = await BudgetEngine(graph).compute_status(budget)
        assert status.violation is False

    async def test_empty_budget_has_zero_allocated_and_actual(self, graph, project_id):
        budget = Budget(
            id="BUD-MASS", project_id=project_id, metric="mass", unit="kg", system_total=5.0
        )
        status = await BudgetEngine(graph).compute_status(budget)
        assert status.allocated == 0.0
        assert status.actual == 0.0
        assert status.violation is False


class TestMetricTotalSkips:
    async def test_non_numeric_value_is_skipped_not_coerced(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": "heavy"}))
        total, skipped = await metric_total_with_skips(graph, project_id, "mass", "kg")
        assert total == 0.0
        assert len(skipped) == 1

    async def test_compute_metric_total_ignores_skip_details(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": "heavy"}))
        await graph.add_node(_wp("battery", project_id, {"mass_kg": 0.8}))
        total = await compute_metric_total(graph, project_id, "mass", "kg")
        assert total == pytest.approx(0.8)

    async def test_no_work_products_at_all_is_zero_not_an_error(self, graph, project_id):
        total = await compute_metric_total(graph, project_id, "mass", "kg")
        assert total == 0.0
