"""Unit tests for the distributor MCP wrapper (MET-434)."""

from __future__ import annotations

import pytest

from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    LifecycleStatus,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)
from tool_registry.tools.distributors.mcp_adapter import DistributorMcpServer


class _FakeAdapter(DistributorAdapter):
    """In-memory DistributorAdapter used to exercise the MCP wrapper."""

    def __init__(self, name: str = "DigiKey") -> None:
        self._name = name
        self.search_calls: list[tuple[str, int]] = []
        self.get_part_calls: list[str] = []
        self.get_pricing_calls: list[str] = []
        self.get_availability_calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        self.search_calls.append((query, limit))
        return [
            PartSearchResult(
                mpn="STM32H743",
                manufacturer="ST",
                description="MCU",
                distributor=self._name,
                stock_qty=42,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        ]

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        self.get_part_calls.append(mpn)
        if mpn == "MISSING":
            return None
        return PartDetail(
            mpn=mpn,
            manufacturer="ST",
            description="MCU",
            distributor=self._name,
            specs={"core": "Cortex-M7"},
            package="LQFP-100",
            category="MCU",
        )

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        self.get_pricing_calls.append(mpn)
        if mpn == "MISSING":
            return []
        return [
            PricingBreak(quantity=1, unit_price=12.5),
            PricingBreak(quantity=100, unit_price=10.0),
        ]

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        self.get_availability_calls.append(mpn)
        if mpn == "MISSING":
            return None
        return AvailabilityInfo(stock_qty=42, lead_time_days=14, minimum_order_qty=1)


# ---------- registration ----------


def test_wrapper_registers_four_tools_per_distributor():
    server = DistributorMcpServer(_FakeAdapter(name="DigiKey"))
    expected = {
        "digikey.search",
        "digikey.get_product",
        "digikey.get_pricing",
        "digikey.get_availability",
    }
    assert expected == set(server.tool_ids)


def test_wrapper_namespace_lowercases_distributor_name():
    server = DistributorMcpServer(_FakeAdapter(name="Mouser"))
    assert all(tid.startswith("mouser.") for tid in server.tool_ids)


def test_wrapper_works_for_all_three_distributors():
    # Schema shape is shared — only the namespace differs.
    namespaces: dict[str, set[str]] = {}
    for name in ("DigiKey", "Mouser", "Nexar"):
        srv = DistributorMcpServer(_FakeAdapter(name=name))
        namespaces[name.lower()] = set(srv.tool_ids)
    digikey = {t.removeprefix("digikey.") for t in namespaces["digikey"]}
    mouser = {t.removeprefix("mouser.") for t in namespaces["mouser"]}
    nexar = {t.removeprefix("nexar.") for t in namespaces["nexar"]}
    assert (
        digikey
        == mouser
        == nexar
        == {
            "search",
            "get_product",
            "get_pricing",
            "get_availability",
        }
    )


# ---------- handler behavior ----------


@pytest.mark.asyncio
async def test_search_returns_serialized_results_and_count():
    adapter = _FakeAdapter()
    server = DistributorMcpServer(adapter)
    result = await server.handle_search({"query": "STM32"})
    assert result["count"] == 1
    assert result["results"][0]["mpn"] == "STM32H743"
    assert result["results"][0]["distributor"] == "DigiKey"
    assert adapter.search_calls == [("STM32", 10)]


@pytest.mark.asyncio
async def test_search_propagates_custom_limit():
    adapter = _FakeAdapter()
    server = DistributorMcpServer(adapter)
    await server.handle_search({"query": "Cortex-M7", "limit": 25})
    assert adapter.search_calls == [("Cortex-M7", 25)]


@pytest.mark.asyncio
async def test_get_product_returns_part_dump():
    server = DistributorMcpServer(_FakeAdapter())
    result = await server.handle_get_product({"mpn": "STM32H743"})
    assert result["part"]["mpn"] == "STM32H743"
    assert result["part"]["package"] == "LQFP-100"


@pytest.mark.asyncio
async def test_get_product_returns_null_when_unknown():
    server = DistributorMcpServer(_FakeAdapter())
    result = await server.handle_get_product({"mpn": "MISSING"})
    assert result["part"] is None


@pytest.mark.asyncio
async def test_get_pricing_returns_breaks_and_count():
    server = DistributorMcpServer(_FakeAdapter())
    result = await server.handle_get_pricing({"mpn": "STM32H743"})
    assert result["count"] == 2
    assert result["breaks"][0]["quantity"] == 1
    assert result["breaks"][1]["unit_price"] == 10.0


@pytest.mark.asyncio
async def test_get_pricing_returns_empty_when_unknown():
    server = DistributorMcpServer(_FakeAdapter())
    result = await server.handle_get_pricing({"mpn": "MISSING"})
    assert result == {"breaks": [], "count": 0}


@pytest.mark.asyncio
async def test_get_availability_returns_serialized():
    server = DistributorMcpServer(_FakeAdapter())
    result = await server.handle_get_availability({"mpn": "STM32H743"})
    assert result["availability"]["stock_qty"] == 42
    assert result["availability"]["lead_time_days"] == 14


@pytest.mark.asyncio
async def test_get_availability_returns_null_when_unknown():
    server = DistributorMcpServer(_FakeAdapter())
    result = await server.handle_get_availability({"mpn": "MISSING"})
    assert result == {"availability": None}


# ---------- input validation ----------


@pytest.mark.asyncio
async def test_search_rejects_missing_query():
    server = DistributorMcpServer(_FakeAdapter())
    with pytest.raises(ValueError, match="query"):
        await server.handle_search({})


@pytest.mark.asyncio
async def test_search_rejects_blank_query():
    server = DistributorMcpServer(_FakeAdapter())
    with pytest.raises(ValueError, match="query"):
        await server.handle_search({"query": "   "})


@pytest.mark.asyncio
async def test_search_rejects_out_of_range_limit():
    server = DistributorMcpServer(_FakeAdapter())
    with pytest.raises(ValueError, match=r"limit"):
        await server.handle_search({"query": "x", "limit": 0})
    with pytest.raises(ValueError, match=r"limit"):
        await server.handle_search({"query": "x", "limit": 51})


@pytest.mark.asyncio
async def test_get_product_rejects_missing_mpn():
    server = DistributorMcpServer(_FakeAdapter())
    with pytest.raises(ValueError, match="mpn"):
        await server.handle_get_product({})


@pytest.mark.asyncio
async def test_close_releases_adapter() -> None:
    closed: list[bool] = []

    class _ClosingAdapter(_FakeAdapter):
        async def close(self) -> None:
            closed.append(True)

    server = DistributorMcpServer(_ClosingAdapter())
    await server.close()
    assert closed == [True]
