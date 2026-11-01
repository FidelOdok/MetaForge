"""Unit tests for make_bom_risk_scorer / api_gateway.twin.bom_risk
(FORGE-268)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from api_gateway.twin.bom_risk import make_bom_risk_scorer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.bom_item import BOMItem


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


def _offer(
    *,
    distributor: str = "Mouser",
    stock_qty: int = 500,
    moq: int = 1,
    lead_time_days: int | None = 7,
    total_committed_cost: float | None = 12.5,
) -> dict[str, Any]:
    return {
        "mpn": "TEST-MPN",
        "distributor": distributor,
        "stock_qty": stock_qty,
        "moq": moq,
        "lead_time_days": lead_time_days,
        "total_committed_cost": total_committed_cost,
        "unit_price_at_qty": total_committed_cost,
    }


class _FakeBridge:
    """Records calls; answers ``distributors.resolve_offers`` and
    ``{distributor}.get_product`` with canned real-shaped responses."""

    def __init__(
        self,
        *,
        resolve_offers_result: dict[str, Any],
        lifecycle_status: str | None = "ACTIVE",
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._resolve_offers_result = resolve_offers_result
        self._lifecycle_status = lifecycle_status

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id == "distributors.resolve_offers":
            return self._resolve_offers_result
        if tool_id.endswith(".get_product"):
            if self._lifecycle_status is None:
                return {"part": None}
            return {"part": {"lifecycle_status": self._lifecycle_status}}
        raise AssertionError(f"unexpected tool call: {tool_id}")


async def _seed_bom_item(twin: InMemoryTwinAPI, **kwargs: Any) -> BOMItem:
    item = BOMItem(part_number="TEST-MPN", manufacturer="Acme", quantity=2, **kwargs)
    await twin._graph.add_node(item)
    return item


class TestMakeBomRiskScorer:
    async def test_empty_project_returns_zeroed_report(self, twin: InMemoryTwinAPI) -> None:
        bridge = _FakeBridge(resolve_offers_result={"results": []})
        score = make_bom_risk_scorer(twin, mcp_bridge=bridge)

        project_id = str(uuid4())
        report = await score(project_id)

        assert report["total_parts"] == 0
        assert report["overall_score"] == 0
        assert report["part_scores"] == []
        assert bridge.calls == []  # never calls distributors for an empty BOM

    async def test_multi_source_low_risk_part_scores_low(self, twin: InMemoryTwinAPI) -> None:
        pid = uuid4()
        await _seed_bom_item(twin, project_id=pid)
        offers = [_offer(distributor=d) for d in ("Mouser", "DigiKey", "Nexar")]
        bridge = _FakeBridge(
            resolve_offers_result={"results": [{"mpn": "TEST-MPN", "offers": offers}]}
        )
        score = make_bom_risk_scorer(twin, mcp_bridge=bridge)

        report = await score(str(pid))

        assert report["total_parts"] == 1
        part = report["part_scores"][0]
        assert part["mpn"] == "TEST-MPN"
        # 3 sources -> single_source factor scores 0; active lifecycle -> 0;
        # short lead time -> 0; abundant stock -> 0; single price point ->
        # "insufficient data" -> 0; compliance unknown (not sourced) -> 100
        # (weight 0.10). Overall should land low, not flagged.
        assert report["overall_score"] <= 25
        assert part["flagged"] is False
        factor_names = {f["name"] for f in part["factors"]}
        assert factor_names == {
            "single_source",
            "lead_time",
            "lifecycle",
            "price_volatility",
            "stock_level",
            "compliance",
        }
        single_source = next(f for f in part["factors"] if f["name"] == "single_source")
        assert single_source["score"] == 0

    async def test_no_offers_found_scores_as_single_source_critical(
        self, twin: InMemoryTwinAPI
    ) -> None:
        pid = uuid4()
        await _seed_bom_item(twin, project_id=pid)
        bridge = _FakeBridge(
            resolve_offers_result={
                "results": [{"mpn": "TEST-MPN", "offers": [], "insufficient_offers": []}]
            }
        )
        score = make_bom_risk_scorer(twin, mcp_bridge=bridge)

        report = await score(str(pid))

        part = report["part_scores"][0]
        single_source = next(f for f in part["factors"] if f["name"] == "single_source")
        assert single_source["score"] == 100
        # No offer at all -> no lifecycle lookup attempted (nothing to key it on).
        assert not any(tid.endswith(".get_product") for tid, _ in bridge.calls)

    async def test_real_lifecycle_lookup_is_attempted_against_top_offer_distributor(
        self, twin: InMemoryTwinAPI
    ) -> None:
        pid = uuid4()
        await _seed_bom_item(twin, project_id=pid)
        bridge = _FakeBridge(
            resolve_offers_result={
                "results": [{"mpn": "TEST-MPN", "offers": [_offer(distributor="Mouser")]}]
            },
            lifecycle_status="NRND",
        )
        score = make_bom_risk_scorer(twin, mcp_bridge=bridge)

        report = await score(str(pid))

        assert ("mouser.get_product", {"mpn": "TEST-MPN"}) in bridge.calls
        part = report["part_scores"][0]
        lifecycle = next(f for f in part["factors"] if f["name"] == "lifecycle")
        assert lifecycle["score"] == 50  # NRND

    async def test_lifecycle_lookup_failure_does_not_abort_scoring(
        self, twin: InMemoryTwinAPI
    ) -> None:
        pid = uuid4()
        await _seed_bom_item(twin, project_id=pid)

        class _RaisingBridge:
            async def invoke(
                self, tool_id: str, params: dict[str, Any], timeout: int | None = None
            ) -> dict[str, Any]:
                if tool_id == "distributors.resolve_offers":
                    return {"results": [{"mpn": "TEST-MPN", "offers": [_offer()]}]}
                raise RuntimeError("distributor API down")

        score = make_bom_risk_scorer(twin, mcp_bridge=_RaisingBridge())

        report = await score(str(pid))

        assert report["total_parts"] == 1
        lifecycle = next(f for f in report["part_scores"][0]["factors"] if f["name"] == "lifecycle")
        assert lifecycle["score"] == 50  # falls back to "unknown"

    async def test_price_volatility_uses_real_multi_distributor_prices(
        self, twin: InMemoryTwinAPI
    ) -> None:
        pid = uuid4()
        await _seed_bom_item(twin, project_id=pid)
        offers = [
            _offer(distributor="Mouser", total_committed_cost=10.0),
            _offer(distributor="DigiKey", total_committed_cost=25.0),
        ]
        bridge = _FakeBridge(
            resolve_offers_result={"results": [{"mpn": "TEST-MPN", "offers": offers}]}
        )
        score = make_bom_risk_scorer(twin, mcp_bridge=bridge)

        report = await score(str(pid))

        volatility = next(
            f for f in report["part_scores"][0]["factors"] if f["name"] == "price_volatility"
        )
        assert volatility["score"] == 100  # CV well above 0.3 for [10.0, 25.0]

    async def test_items_without_a_part_number_are_skipped(self, twin: InMemoryTwinAPI) -> None:
        pid = uuid4()
        # part_number is a required field; simulate the "no real MPN" case
        # bom/routes.py already guards against (legacy/degraded rows) by
        # using an empty string, which this module must not send to a
        # distributor lookup.
        item = BOMItem(part_number="", manufacturer="Acme", project_id=pid)
        await twin._graph.add_node(item)
        bridge = _FakeBridge(resolve_offers_result={"results": []})
        score = make_bom_risk_scorer(twin, mcp_bridge=bridge)

        report = await score(str(pid))

        assert report["total_parts"] == 0
        assert bridge.calls == []
