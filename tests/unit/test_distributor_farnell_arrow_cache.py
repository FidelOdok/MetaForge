"""Farnell and Arrow adapters, and the distributor response cache (FORGE-613)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mcp_core.annotations import OPEN_WORLD, READ_ONLY
from tool_registry.tools.arrow.adapter import ArrowAdapter
from tool_registry.tools.distributors.base import LifecycleStatus
from tool_registry.tools.distributors.cache import DailyLimitReachedError, ResponseCache
from tool_registry.tools.distributors.mcp_adapter import distributor_tool_ids
from tool_registry.tools.farnell.adapter import FarnellAdapter
from tool_registry.tools.mouser.adapter import MouserAdapter

FARNELL_PRODUCT = {
    "sku": "2443202",
    "displayName": "STMICROELECTRONICS STM32F103C8T6 MCU, ARM CORTEX-M3",
    "translatedManufacturerPartNumber": "STM32F103C8T6",
    "brandName": "STMICROELECTRONICS",
    "productStatus": "STOCKED",
    "translatedMinimumOrderQuality": 1,
    "stock": {"level": 1520, "leastLeadTime": 14},
    "prices": [{"from": 1, "to": 9, "cost": 4.21}, {"from": 10, "to": 99, "cost": 3.80}],
    "datasheets": [{"url": "https://www.farnell.com/datasheets/stm32.pdf"}],
    "attributes": [{"attributeLabel": "IC Case / Package", "attributeValue": "LQFP"}],
}

ARROW_PART = {
    "itemId": 123,
    "partNum": "STM32F103C8T6",
    "manufacturer": {"mfrName": "STMicroelectronics"},
    "desc": "MCU 32-bit ARM Cortex M3",
    "resources": [{"type": "datasheet", "uri": "https://static.arrow.com/stm32.pdf"}],
    "InvOrg": {
        "webSites": [
            {
                "code": "arrow.com",
                "sources": [
                    {
                        "currency": "USD",
                        "sourceParts": [
                            {
                                "minimumOrderQuantity": 1,
                                "Prices": {
                                    "resaleList": [
                                        {"price": 5.1, "minQty": 1, "maxQty": 9},
                                        {"price": 4.6, "minQty": 10, "maxQty": 99},
                                    ]
                                },
                                "Availability": [{"fohQty": 300}],
                            },
                            {
                                "minimumOrderQuantity": 5,
                                "Prices": {"resaleList": [{"price": 4.9, "minQty": 1}]},
                                "Availability": [{"fohQty": 50}],
                            },
                        ],
                    }
                ],
            }
        ]
    },
}


def _client(body: dict[str, Any], seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_farnell_maps_a_uk_part_in_gbp() -> None:
    seen: list[httpx.Request] = []
    body = {"manufacturerPartNumberSearchReturn": {"products": [FARNELL_PRODUCT]}}
    async with _client(body, seen) as http:
        farnell = FarnellAdapter(client=http, api_key="k", store="uk.farnell.com")
        detail = await farnell.get_part_details("STM32F103C8T6")
        pricing = await farnell.get_pricing("STM32F103C8T6")
        avail = await farnell.get_availability("STM32F103C8T6")
    assert detail is not None
    assert detail.manufacturer == "STMICROELECTRONICS"
    assert detail.lifecycle_status == LifecycleStatus.ACTIVE
    assert detail.package == "LQFP"
    assert detail.product_url == "https://uk.farnell.com/2443202"
    assert [(b.quantity, b.unit_price, b.currency) for b in pricing] == [
        (1, 4.21, "GBP"),
        (10, 3.80, "GBP"),
    ]
    assert avail is not None and avail.stock_qty == 1520 and avail.lead_time_days == 14
    # Three lookups, one call: the cache answered the other two.
    assert len(seen) == 1
    assert seen[0].url.params["term"] == "manuPartNum:STM32F103C8T6"
    assert seen[0].url.params["storeInfo.id"] == "uk.farnell.com"


@pytest.mark.asyncio
async def test_farnell_with_an_unknown_store_currency_gives_no_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FARNELL_CURRENCY", raising=False)
    body = {"manufacturerPartNumberSearchReturn": {"products": [FARNELL_PRODUCT]}}
    async with _client(body, []) as http:
        farnell = FarnellAdapter(client=http, api_key="k", store="xx.farnell.com")
        assert await farnell.get_pricing("STM32F103C8T6") == []


@pytest.mark.asyncio
async def test_farnell_keyword_search() -> None:
    body = {"keywordSearchReturn": {"products": [FARNELL_PRODUCT]}}
    seen: list[httpx.Request] = []
    async with _client(body, seen) as http:
        results = await FarnellAdapter(client=http, api_key="k").search_parts("stm32", 5)
    assert [r.mpn for r in results] == ["STM32F103C8T6"]
    assert seen[0].url.params["term"] == "any:stm32"


@pytest.mark.asyncio
async def test_arrow_maps_prices_stock_and_leaves_lead_time_unknown() -> None:
    seen: list[httpx.Request] = []
    body = {"itemserviceresult": {"data": [{"PartList": [ARROW_PART]}]}}
    async with _client(body, seen) as http:
        arrow = ArrowAdapter(client=http, login="me", api_key="k")
        detail = await arrow.get_part_details("STM32F103C8T6")
        pricing = await arrow.get_pricing("STM32F103C8T6")
        avail = await arrow.get_availability("STM32F103C8T6")
    assert detail is not None and detail.datasheet_url == "https://static.arrow.com/stm32.pdf"
    # The cheapest price at each break across sources.
    assert [(b.quantity, b.unit_price, b.currency) for b in pricing] == [
        (1, 4.9, "USD"),
        (10, 4.6, "USD"),
    ]
    assert avail is not None
    assert avail.stock_qty == 350
    assert avail.minimum_order_qty == 1
    assert avail.lead_time_days is None
    assert detail.lifecycle_status == LifecycleStatus.UNKNOWN
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_arrow_does_not_pass_off_a_near_match_as_the_part() -> None:
    other = {**ARROW_PART, "partNum": "STM32F103C8T7"}
    body = {"itemserviceresult": {"data": [{"PartList": [other]}]}}
    async with _client(body, []) as http:
        assert (
            await ArrowAdapter(client=http, login="me", api_key="k").get_part_details(
                "STM32F103C8T6"
            )
            is None
        )


@pytest.mark.asyncio
async def test_a_failed_call_is_empty_not_invented() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    async with httpx.AsyncClient(transport=transport) as http:
        assert await FarnellAdapter(client=http, api_key="k").get_pricing("X") == []
        assert await ArrowAdapter(client=http, login="a", api_key="k").get_availability("X") is None


@pytest.mark.asyncio
async def test_mouser_shares_one_call_across_details_pricing_and_availability() -> None:
    seen: list[httpx.Request] = []
    body = {
        "SearchResults": {
            "Parts": [
                {
                    "ManufacturerPartNumber": "ESP32",
                    "Availability": "10 In Stock",
                    "PriceBreaks": [{"Quantity": 1, "Price": "$3.10", "Currency": "USD"}],
                }
            ]
        }
    }
    async with _client(body, seen) as http:
        mouser = MouserAdapter(client=http, api_key="k")
        await mouser.get_part_details("ESP32")
        await mouser.get_pricing("ESP32")
        await mouser.get_availability("ESP32")
    assert len(seen) == 1


class TestResponseCache:
    @pytest.mark.asyncio
    async def test_fresh_entries_are_served_and_stale_ones_refetched(self) -> None:
        now = [1_000_000.0]
        cache = ResponseCache("t", ttl_seconds=60, clock=lambda: now[0])
        calls = []

        async def fetch() -> int:
            calls.append(1)
            return len(calls)

        assert await cache.get("k", fetch) == 1
        assert await cache.get("k", fetch) == 1
        now[0] += 61
        assert await cache.get("k", fetch) == 2
        assert cache.hits == 1

    @pytest.mark.asyncio
    async def test_a_spent_budget_serves_stale_then_refuses(self) -> None:
        now = [1_000_000.0]
        cache = ResponseCache("t", ttl_seconds=10, daily_limit=1, clock=lambda: now[0])

        async def fetch() -> str:
            return "fresh"

        assert await cache.get("a", fetch) == "fresh"
        now[0] += 20
        # Budget spent, entry stale: served stale rather than spending more.
        assert await cache.get("a", fetch) == "fresh"
        with pytest.raises(DailyLimitReachedError):
            await cache.get("b", fetch)
        assert cache.remaining_today == 0

    @pytest.mark.asyncio
    async def test_the_budget_resets_each_utc_day(self) -> None:
        now = [86_400.0 * 100]
        cache = ResponseCache("t", ttl_seconds=0, daily_limit=1, clock=lambda: now[0])

        async def fetch() -> int:
            return 1

        await cache.get("a", fetch)
        with pytest.raises(DailyLimitReachedError):
            await cache.get("b", fetch)
        now[0] += 86_400
        assert await cache.get("b", fetch) == 1


def test_new_distributor_tools_are_classified_as_open_world_reads() -> None:
    for did in ("farnell", "arrow"):
        for suffix in ("search", "get_product", "get_pricing", "get_availability"):
            tool = f"{did}.{suffix}"
            assert tool in distributor_tool_ids()
            assert tool in READ_ONLY and tool in OPEN_WORLD


@pytest.mark.asyncio
async def test_bootstrap_registers_them_only_with_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    for name in ("FARNELL_API_KEY", "ARROW_LOGIN", "ARROW_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    registry = await bootstrap_tool_registry(adapter_ids=["farnell", "arrow"])
    assert not {t.tool_id for t in registry.list_tools()} & {"farnell.search", "arrow.search"}
    monkeypatch.setenv("FARNELL_API_KEY", "k")
    monkeypatch.setenv("ARROW_LOGIN", "me")
    monkeypatch.setenv("ARROW_API_KEY", "k")
    registry = await bootstrap_tool_registry(adapter_ids=["farnell", "arrow"])
    tools = {t.tool_id for t in registry.list_tools()}
    assert {"farnell.get_pricing", "arrow.get_pricing"} <= tools
