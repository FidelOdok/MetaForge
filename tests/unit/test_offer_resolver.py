"""Unit tests for the cross-distributor offer-resolution layer (MET-436)."""

from __future__ import annotations

import pytest

from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)
from tool_registry.tools.distributors.offer_resolver import (
    OfferResolverServer,
    _select_tier,
    fetch_offers_for_mpn,
    resolve_offers_for_item,
)


class _FakeAdapter(DistributorAdapter):
    """In-memory DistributorAdapter used to exercise offer resolution."""

    def __init__(
        self,
        name: str,
        *,
        pricing: list[PricingBreak] | None = None,
        availability: AvailabilityInfo | None = None,
        pricing_error: Exception | None = None,
        availability_error: Exception | None = None,
    ) -> None:
        self._name = name
        self._pricing = pricing if pricing is not None else []
        self._availability = availability
        self._pricing_error = pricing_error
        self._availability_error = availability_error

    @property
    def name(self) -> str:
        return self._name

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        if self._pricing_error is not None:
            raise self._pricing_error
        return self._pricing

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        if self._availability_error is not None:
            raise self._availability_error
        return self._availability


# ---------------------------------------------------------------------------
# _select_tier
# ---------------------------------------------------------------------------


def test_select_tier_extrapolates_when_required_qty_below_every_tier():
    breaks = [PricingBreak(quantity=100, unit_price=0.48)]
    tier, extrapolated = _select_tier(breaks, required_qty=10)
    assert extrapolated is True
    assert tier.quantity == 100


def test_select_tier_picks_highest_eligible_tier():
    breaks = [
        PricingBreak(quantity=1, unit_price=0.62),
        PricingBreak(quantity=10, unit_price=0.55),
        PricingBreak(quantity=100, unit_price=0.48),
    ]
    tier, extrapolated = _select_tier(breaks, required_qty=50)
    assert extrapolated is False
    assert tier.quantity == 10
    assert tier.unit_price == 0.55


def test_select_tier_rejects_empty_breaks():
    with pytest.raises(ValueError, match="non-empty"):
        _select_tier([], required_qty=10)


# ---------------------------------------------------------------------------
# fetch_offers_for_mpn — offer construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_committed_qty_reflects_moq_forced_overbuy():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=1, unit_price=0.10)],
        availability=AvailabilityInfo(stock_qty=10_000, lead_time_days=1, minimum_order_qty=500),
    )
    offers = await fetch_offers_for_mpn([adapter], "R0402", required_qty=50)
    assert len(offers) == 1
    offer = offers[0]
    assert offer.committed_qty == 500
    assert offer.total_committed_cost == pytest.approx(50.0)
    assert offer.price_extrapolated_below_min_tier is False


@pytest.mark.asyncio
async def test_committed_qty_reflects_tier_extrapolation():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=100, unit_price=0.48)],
        availability=AvailabilityInfo(stock_qty=10_000, lead_time_days=1, minimum_order_qty=1),
    )
    offers = await fetch_offers_for_mpn([adapter], "MP2459", required_qty=10)
    assert len(offers) == 1
    offer = offers[0]
    assert offer.price_extrapolated_below_min_tier is True
    assert offer.committed_qty == 100
    assert offer.total_committed_cost == pytest.approx(48.0)


@pytest.mark.asyncio
async def test_meets_qty_requires_stock_at_or_above_required_not_just_nonzero():
    adapter = _FakeAdapter(
        "Mouser",
        pricing=[PricingBreak(quantity=1, unit_price=0.5)],
        availability=AvailabilityInfo(stock_qty=12, lead_time_days=5, minimum_order_qty=1),
    )
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=50)
    assert offers[0].meets_qty is False


@pytest.mark.asyncio
async def test_no_reported_lead_time_but_in_stock_treated_as_ships_now():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=1, unit_price=0.5)],
        availability=AvailabilityInfo(stock_qty=100, lead_time_days=None, minimum_order_qty=1),
    )
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=10, deadline_days=7)
    assert offers[0].meets_deadline is True


@pytest.mark.asyncio
async def test_meets_deadline_is_none_when_no_deadline_given():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=1, unit_price=0.5)],
        availability=AvailabilityInfo(stock_qty=100, lead_time_days=30, minimum_order_qty=1),
    )
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=10, deadline_days=None)
    assert offers[0].meets_deadline is None


# ---------------------------------------------------------------------------
# Partial-data and failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_data_flagged_when_availability_call_fails():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=1, unit_price=0.5)],
        availability_error=RuntimeError("boom"),
    )
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=10)
    assert len(offers) == 1
    assert offers[0].partial_data is True
    assert offers[0].stock_qty == 0
    assert offers[0].unit_price_at_qty == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_partial_data_flagged_when_pricing_call_fails():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing_error=RuntimeError("boom"),
        availability=AvailabilityInfo(stock_qty=100, lead_time_days=1, minimum_order_qty=1),
    )
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=10)
    assert len(offers) == 1
    assert offers[0].partial_data is True
    assert offers[0].unit_price_at_qty is None
    assert offers[0].total_committed_cost is None
    assert offers[0].stock_qty == 100


@pytest.mark.asyncio
async def test_no_offer_contributed_when_both_calls_fail():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing_error=RuntimeError("x"),
        availability_error=RuntimeError("y"),
    )
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=10)
    assert offers == []


@pytest.mark.asyncio
async def test_no_offer_contributed_when_both_calls_succeed_but_empty():
    adapter = _FakeAdapter("DigiKey", pricing=[], availability=None)
    offers = await fetch_offers_for_mpn([adapter], "X", required_qty=10)
    assert offers == []


@pytest.mark.asyncio
async def test_zero_adapters_returns_empty_list_not_error():
    offers = await fetch_offers_for_mpn([], "X", required_qty=10)
    assert offers == []


# ---------------------------------------------------------------------------
# resolve_offers_for_item — status + ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_offers_found_status_for_cubeorange_style_specialty_part():
    """All three configured distributors carry nothing for this MPN — a
    normal outcome for a specialty COTS module, never an exception."""
    adapters = [
        _FakeAdapter("DigiKey", pricing=[], availability=None),
        _FakeAdapter("Mouser", pricing=[], availability=None),
        _FakeAdapter("Nexar", pricing=[], availability=None),
    ]
    resolution = await resolve_offers_for_item(adapters, "CUBE-ORANGE-STANDARD", required_qty=1)
    assert resolution.status == "no_offers_found"
    assert resolution.offers == []
    assert resolution.insufficient_offers == []
    assert resolution.reason is not None
    assert "3 configured" in resolution.reason


@pytest.mark.asyncio
async def test_no_offers_found_status_with_zero_configured_adapters():
    resolution = await resolve_offers_for_item([], "X", required_qty=1)
    assert resolution.status == "no_offers_found"


@pytest.mark.asyncio
async def test_insufficient_stock_everywhere_returns_closest_options_not_empty():
    adapters = [
        _FakeAdapter(
            "DigiKey",
            pricing=[PricingBreak(quantity=1, unit_price=0.5)],
            availability=AvailabilityInfo(stock_qty=5, lead_time_days=3, minimum_order_qty=1),
        ),
        _FakeAdapter(
            "Mouser",
            pricing=[PricingBreak(quantity=1, unit_price=0.6)],
            availability=AvailabilityInfo(stock_qty=20, lead_time_days=2, minimum_order_qty=1),
        ),
    ]
    resolution = await resolve_offers_for_item(adapters, "X", required_qty=50)
    assert resolution.status == "insufficient_stock_everywhere"
    assert resolution.offers != []
    # Ranked by deepest stock first among the insufficient options.
    assert resolution.offers[0].distributor == "Mouser"
    assert all(not o.meets_qty for o in resolution.offers)


@pytest.mark.asyncio
async def test_deadline_miss_ranks_below_deadline_meeting_offer_even_if_cheaper():
    cheap_but_late = _FakeAdapter(
        "Mouser",
        pricing=[PricingBreak(quantity=1, unit_price=0.30)],
        availability=AvailabilityInfo(stock_qty=1000, lead_time_days=30, minimum_order_qty=1),
    )
    pricier_on_time = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=1, unit_price=0.60)],
        availability=AvailabilityInfo(stock_qty=1000, lead_time_days=2, minimum_order_qty=1),
    )
    resolution = await resolve_offers_for_item(
        [cheap_but_late, pricier_on_time], "X", required_qty=10, deadline_days=7
    )
    assert resolution.status == "ok"
    assert [o.distributor for o in resolution.offers] == ["DigiKey", "Mouser"]
    assert resolution.offers[0].meets_deadline is True
    assert resolution.offers[1].meets_deadline is False


@pytest.mark.asyncio
async def test_no_deadline_ranks_by_cost_alone():
    adapters = [
        _FakeAdapter(
            "Mouser",
            pricing=[PricingBreak(quantity=1, unit_price=0.30)],
            availability=AvailabilityInfo(stock_qty=1000, lead_time_days=30, minimum_order_qty=1),
        ),
        _FakeAdapter(
            "DigiKey",
            pricing=[PricingBreak(quantity=1, unit_price=0.60)],
            availability=AvailabilityInfo(stock_qty=1000, lead_time_days=2, minimum_order_qty=1),
        ),
    ]
    resolution = await resolve_offers_for_item(adapters, "X", required_qty=10, deadline_days=None)
    assert resolution.status == "ok"
    assert [o.distributor for o in resolution.offers] == ["Mouser", "DigiKey"]


@pytest.mark.asyncio
async def test_mp2459_worked_example_from_plan():
    """Reproduces the plan's worked example: DigiKey sufficient, Mouser
    insufficient (out of stock), Nexar sufficient and cheapest via its
    own multi-seller aggregation."""
    digikey = _FakeAdapter(
        "DigiKey",
        pricing=[
            PricingBreak(quantity=1, unit_price=0.62),
            PricingBreak(quantity=10, unit_price=0.55),
            PricingBreak(quantity=100, unit_price=0.48),
        ],
        availability=AvailabilityInfo(stock_qty=500, lead_time_days=2, minimum_order_qty=1),
    )
    mouser = _FakeAdapter(
        "Mouser",
        pricing=[
            PricingBreak(quantity=1, unit_price=0.58),
            PricingBreak(quantity=10, unit_price=0.50),
        ],
        availability=AvailabilityInfo(stock_qty=0, lead_time_days=5, minimum_order_qty=1),
    )
    nexar = _FakeAdapter(
        "Nexar",
        pricing=[
            PricingBreak(quantity=1, unit_price=0.60),
            PricingBreak(quantity=10, unit_price=0.52),
            PricingBreak(quantity=50, unit_price=0.39),
        ],
        availability=AvailabilityInfo(stock_qty=1200, lead_time_days=1, minimum_order_qty=1),
    )

    resolution = await resolve_offers_for_item([digikey, mouser, nexar], "MP2459", required_qty=50)

    assert resolution.status == "ok"
    assert [o.distributor for o in resolution.offers] == ["Nexar", "DigiKey"]

    nexar_offer = resolution.offers[0]
    assert nexar_offer.unit_price_at_qty == pytest.approx(0.39)
    assert nexar_offer.total_committed_cost == pytest.approx(19.50)
    assert nexar_offer.is_multi_seller_aggregate is True
    assert nexar_offer.meets_qty is True

    digikey_offer = resolution.offers[1]
    assert digikey_offer.unit_price_at_qty == pytest.approx(0.55)
    assert digikey_offer.total_committed_cost == pytest.approx(27.50)
    assert digikey_offer.is_multi_seller_aggregate is False

    # "ok" status still surfaces the insufficient offer as an audit trail —
    # never silently dropped.
    assert [o.distributor for o in resolution.insufficient_offers] == ["Mouser"]
    assert resolution.insufficient_offers[0].meets_qty is False


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


def test_offer_resolver_registers_exactly_one_tool():
    server = OfferResolverServer(adapters=[])
    assert server.tool_ids == ["distributors.resolve_offers"]


@pytest.mark.asyncio
async def test_handle_resolve_offers_batch_returns_one_result_per_item():
    adapter = _FakeAdapter(
        "DigiKey",
        pricing=[PricingBreak(quantity=1, unit_price=1.0)],
        availability=AvailabilityInfo(stock_qty=100, lead_time_days=1, minimum_order_qty=1),
    )
    server = OfferResolverServer(adapters=[adapter])
    result = await server.handle_resolve_offers(
        {"items": [{"mpn": "X", "required_qty": 5}, {"mpn": "Y", "required_qty": 3}]}
    )
    assert len(result["results"]) == 2
    assert result["results"][0]["status"] == "ok"
    assert result["results"][0]["mpn"] == "X"
    assert result["results"][1]["mpn"] == "Y"


@pytest.mark.asyncio
async def test_handle_resolve_offers_rejects_missing_items():
    server = OfferResolverServer(adapters=[])
    with pytest.raises(ValueError, match="items"):
        await server.handle_resolve_offers({})


@pytest.mark.asyncio
async def test_handle_resolve_offers_rejects_missing_mpn():
    server = OfferResolverServer(adapters=[])
    with pytest.raises(ValueError, match="mpn"):
        await server.handle_resolve_offers({"items": [{"required_qty": 5}]})


@pytest.mark.asyncio
async def test_handle_resolve_offers_rejects_bad_required_qty():
    server = OfferResolverServer(adapters=[])
    with pytest.raises(ValueError, match="required_qty"):
        await server.handle_resolve_offers({"items": [{"mpn": "X", "required_qty": 0}]})


@pytest.mark.asyncio
async def test_close_is_a_deliberate_noop_and_does_not_close_borrowed_adapters():
    closed: list[bool] = []

    class _ClosingAdapter(_FakeAdapter):
        async def close(self) -> None:
            closed.append(True)

    adapter = _ClosingAdapter("DigiKey")
    server = OfferResolverServer(adapters=[adapter])
    await server.close()
    assert closed == []
