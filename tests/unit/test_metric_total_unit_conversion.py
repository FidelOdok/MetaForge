"""Unit tests for FORGE-311's Quantity-aware upgrade to
compute_metric_total/metric_total_with_skips: a WorkProduct storing the
same metric under a dimensionally-compatible-but-different unit now
contributes (converted) instead of being silently invisible, and a
genuinely incompatible-dimension key raises rather than silently
contributing 0.
"""

from uuid import uuid4

import pytest

from twin_core.consistency.metrics import compute_metric_total, metric_total_with_skips
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import WorkProduct, WorkProductType
from twin_core.models.quantity import IncompatibleUnitsError


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


class TestAlternateUnitConversion:
    async def test_exact_key_still_works_unchanged(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 2.0}))
        total = await compute_metric_total(graph, project_id, "mass", "kg")
        assert total == pytest.approx(2.0)

    async def test_alternate_compatible_unit_is_converted_and_summed(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"mass_kg": 2.0}))
        await graph.add_node(_wp("bracket", project_id, {"mass_g": 500.0}))
        total = await compute_metric_total(graph, project_id, "mass", "kg")
        assert total == pytest.approx(2.5)

    async def test_deflection_metres_converts_to_mm_acceptance_example(self, graph, project_id):
        # Ticket's own acceptance example.
        await graph.add_node(_wp("upper_arm", project_id, {"deflection_m": 0.0003}))
        total = await compute_metric_total(graph, project_id, "deflection", "mm")
        assert total == pytest.approx(0.3)

    async def test_incompatible_dimension_key_raises_acceptance_example(self, graph, project_id):
        # Ticket's own acceptance example: mass <= 4.5kg vs a value in mm.
        await graph.add_node(_wp("frame", project_id, {"mass_mm": 12.0}))
        with pytest.raises(IncompatibleUnitsError):
            await compute_metric_total(graph, project_id, "mass", "kg")

    async def test_no_matching_key_at_all_is_still_a_clean_zero(self, graph, project_id):
        await graph.add_node(_wp("frame", project_id, {"cost_usd": 10.0}))
        total = await compute_metric_total(graph, project_id, "mass", "kg")
        assert total == 0.0

    async def test_unrelated_key_sharing_the_prefix_is_ignored(self, graph, project_id):
        # "mass_report" isn't "mass_<unit>" -- "report" isn't a real unit,
        # so this must not be mistaken for an alternate-unit key.
        await graph.add_node(_wp("frame", project_id, {"mass_report": "see attached"}))
        total, skipped = await metric_total_with_skips(graph, project_id, "mass", "kg")
        assert total == 0.0
        assert skipped == []

    async def test_currency_metric_still_works(self, graph, project_id):
        await graph.add_node(_wp("bom", project_id, {"cost_usd": 120.0}))
        total = await compute_metric_total(graph, project_id, "cost", "usd")
        assert total == pytest.approx(120.0)

    async def test_cross_currency_key_raises(self, graph, project_id):
        await graph.add_node(_wp("bom", project_id, {"cost_gbp": 90.0}))
        with pytest.raises(IncompatibleUnitsError):
            await compute_metric_total(graph, project_id, "cost", "usd")
