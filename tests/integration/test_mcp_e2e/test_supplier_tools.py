"""Phase 3 — supplier (distributor) MCP tools coverage (MET-477).

The distributor adapters (Digi-Key, Mouser, Nexar) are bootstrap-gated on
their respective credential env vars:

* ``digikey.*``  — needs ``DIGIKEY_CLIENT_ID`` + ``DIGIKEY_CLIENT_SECRET``
* ``mouser.*``   — needs ``MOUSER_API_KEY``
* ``nexar.*``    — needs ``NEXAR_CLIENT_ID`` + ``NEXAR_CLIENT_SECRET``

Each distributor exposes four MCP tools: ``search`` / ``get_product`` /
``get_pricing`` / ``get_availability``.

CI never has those creds, so the default `mcp_client` fixture must
**skip** all three adapters — the suite asserts that contract. A
separate fixture wires a `_FakeDistributorAdapter` into the unified MCP
bootstrap by patching `_make_digikey`, letting the four tools register
without real OAuth. That fake drives the contract round-trip
deterministically. Tests gated on live credentials are
`_REQUIRES_LIVE_CREDS`-marked.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import httpx
import pytest

from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    LifecycleStatus,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)

from ._helpers import McpRpcError, call_tool, rpc

pytestmark = pytest.mark.asyncio


_HAS_DIGIKEY_CREDS = bool(
    os.environ.get("DIGIKEY_CLIENT_ID") and os.environ.get("DIGIKEY_CLIENT_SECRET")
)
_HAS_MOUSER_CREDS = bool(os.environ.get("MOUSER_API_KEY"))
_HAS_NEXAR_CREDS = bool(os.environ.get("NEXAR_CLIENT_ID") and os.environ.get("NEXAR_CLIENT_SECRET"))


# ---------------------------------------------------------------------------
# Default-mode behaviour: no creds → no distributor tools.
# ---------------------------------------------------------------------------


async def test_no_distributor_tools_without_creds(mcp_client) -> None:
    """The CI default: no creds, none of digikey/mouser/nexar tools registered.

    This is the contract MET-434 promised: missing creds should *skip*
    the adapter (with a structured log line), not crash bootstrap. The
    default ``mcp_client`` fixture is the in-process app with no
    distributor env vars set in CI.
    """
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}

    if not _HAS_DIGIKEY_CREDS:
        digikey = {tid for tid in tool_ids if tid and tid.startswith("digikey.")}
        assert digikey == set(), (
            f"digikey tools registered without DIGIKEY_CLIENT_ID/SECRET: {digikey}"
        )
    if not _HAS_MOUSER_CREDS:
        mouser = {tid for tid in tool_ids if tid and tid.startswith("mouser.")}
        assert mouser == set(), f"mouser tools registered without MOUSER_API_KEY: {mouser}"
    if not _HAS_NEXAR_CREDS:
        nexar = {tid for tid in tool_ids if tid and tid.startswith("nexar.")}
        assert nexar == set(), f"nexar tools registered without NEXAR_CLIENT_ID/SECRET: {nexar}"


# ---------------------------------------------------------------------------
# Fake-adapter mode: tools register, contract round-trips work in-process.
# ---------------------------------------------------------------------------


class _FakeDistributorAdapter(DistributorAdapter):
    """Tiny DistributorAdapter stand-in that returns deterministic data."""

    def __init__(self, name: str = "DigiKey") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        return [
            PartSearchResult(
                mpn="BME280",
                manufacturer="Bosch",
                description=f"Match for {query}",
                distributor=self._name,
                distributor_pn="828-1063-1-ND",
                stock_qty=5_000,
                lead_time_days=14,
                lifecycle_status=LifecycleStatus.ACTIVE,
                datasheet_url="https://example.com/bme280.pdf",
            )
        ]

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        if mpn != "BME280":
            return None
        return PartDetail(
            mpn="BME280",
            manufacturer="Bosch",
            description="Combined humidity / temperature / pressure sensor",
            distributor=self._name,
            distributor_pn="828-1063-1-ND",
            stock_qty=5_000,
            lifecycle_status=LifecycleStatus.ACTIVE,
            specs={"supply_voltage_v": "1.71-3.6"},
            package="LGA-8",
            category="Environmental Sensors",
        )

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        if mpn != "BME280":
            return []
        return [
            PricingBreak(quantity=1, unit_price=8.42, currency="USD"),
            PricingBreak(quantity=100, unit_price=6.20, currency="USD"),
        ]

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        if mpn != "BME280":
            return None
        return AvailabilityInfo(
            stock_qty=5_000,
            lead_time_days=14,
            minimum_order_qty=1,
            factory_stock=2_500,
        )


@pytest.fixture
async def digikey_mcp_client() -> AsyncIterator[httpx.AsyncClient]:
    """MCP client where ``digikey.*`` is wired to a fake DistributorAdapter.

    Patches ``tool_registry.tools.digikey.adapter.DigiKeyAdapter`` so
    bootstrap's ``_make_digikey()`` factory builds the fake regardless
    of whether real creds are present. Setting fake credentials in the
    env lets the bootstrap factory return the patched adapter.
    """
    live_url = os.environ.get("METAFORGE_MCP_URL") or None
    if live_url:
        async with httpx.AsyncClient(base_url=live_url, timeout=60.0) as client:
            yield client
        return

    from digital_twin.knowledge.embedding_service import create_embedding_service
    from digital_twin.memory.client import MemoryClient
    from digital_twin.memory.consolidation import InMemoryInsightStore
    from digital_twin.memory.store import InMemoryExperienceStore
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import build_unified_server
    from twin_core.api import InMemoryTwinAPI

    memory_client = MemoryClient(
        store=InMemoryExperienceStore(),
        embeddings=create_embedding_service("local"),
    )

    fake_env = {
        "DIGIKEY_CLIENT_ID": "fake-id",
        "DIGIKEY_CLIENT_SECRET": "fake-secret",
    }
    with (
        patch.dict(os.environ, fake_env, clear=False),
        patch(
            "tool_registry.tools.digikey.adapter.DigiKeyAdapter",
            return_value=_FakeDistributorAdapter("DigiKey"),
        ),
    ):
        server = await build_unified_server(
            knowledge_service=None,
            twin=InMemoryTwinAPI.create(),
            constraint_engine=None,
            project_backend=None,
            memory_client=memory_client,
            memory_insight_store=InMemoryInsightStore(),
        )
        app = build_http_app(server, enable_sse=False, api_key=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
            yield client


# ---------------------------------------------------------------------------
# digikey.* contract round-trips against the fake adapter.
# ---------------------------------------------------------------------------


_EXPECTED_DIGIKEY_TOOLS = {
    "digikey.search",
    "digikey.get_product",
    "digikey.get_pricing",
    "digikey.get_availability",
}


async def test_digikey_tools_register_when_creds_present(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    result = await rpc(digikey_mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    missing = _EXPECTED_DIGIKEY_TOOLS - tool_ids
    assert not missing, f"missing digikey tools when creds set: {missing}"


async def test_digikey_search_returns_results(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    envelope = await call_tool(
        digikey_mcp_client,
        "digikey.search",
        {"query": "BME280", "limit": 5},
    )
    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert payload["count"] == 1
    assert payload["results"][0]["mpn"] == "BME280"
    assert payload["results"][0]["distributor"] == "DigiKey"


async def test_digikey_search_requires_query(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(McpRpcError):
        await call_tool(digikey_mcp_client, "digikey.search", {})


async def test_digikey_get_product_returns_part_detail(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    envelope = await call_tool(
        digikey_mcp_client,
        "digikey.get_product",
        {"mpn": "BME280"},
    )
    payload = envelope["data"]
    part = payload["part"]
    assert part is not None
    assert part["mpn"] == "BME280"
    assert part["package"] == "LGA-8"
    assert part["specs"]["supply_voltage_v"] == "1.71-3.6"


async def test_digikey_get_product_unknown_mpn_returns_null(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    envelope = await call_tool(
        digikey_mcp_client,
        "digikey.get_product",
        {"mpn": "DEFINITELY-NOT-A-REAL-MPN"},
    )
    assert envelope["data"]["part"] is None


async def test_digikey_get_pricing_returns_breaks(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    envelope = await call_tool(
        digikey_mcp_client,
        "digikey.get_pricing",
        {"mpn": "BME280"},
    )
    payload = envelope["data"]
    assert payload["count"] == 2
    assert payload["breaks"][0]["quantity"] == 1
    # Quantity tiers are sorted ascending, so the 100-piece break is second.
    assert payload["breaks"][1]["quantity"] == 100
    assert payload["breaks"][1]["unit_price"] < payload["breaks"][0]["unit_price"]


async def test_digikey_get_availability_returns_envelope(
    digikey_mcp_client: httpx.AsyncClient,
) -> None:
    envelope = await call_tool(
        digikey_mcp_client,
        "digikey.get_availability",
        {"mpn": "BME280"},
    )
    avail = envelope["data"]["availability"]
    assert avail is not None
    assert avail["stock_qty"] == 5_000
    assert avail["lead_time_days"] == 14
    assert avail["minimum_order_qty"] == 1


# ---------------------------------------------------------------------------
# Live-credential gated tests — exercise the real OAuth path when creds
# are present in the environment, otherwise skip.
# ---------------------------------------------------------------------------


_REQUIRES_DIGIKEY_LIVE = pytest.mark.skipif(
    not _HAS_DIGIKEY_CREDS,
    reason="needs DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET to exercise the live API",
)
_REQUIRES_MOUSER_LIVE = pytest.mark.skipif(
    not _HAS_MOUSER_CREDS,
    reason="needs MOUSER_API_KEY to exercise the live API",
)
_REQUIRES_NEXAR_LIVE = pytest.mark.skipif(
    not _HAS_NEXAR_CREDS,
    reason="needs NEXAR_CLIENT_ID + NEXAR_CLIENT_SECRET to exercise the live API",
)


@_REQUIRES_DIGIKEY_LIVE
async def test_digikey_live_search_smokes(mcp_client) -> None:
    """Live OAuth round-trip — only runs when real creds are wired."""
    envelope = await call_tool(
        mcp_client,
        "digikey.search",
        {"query": "STM32H7", "limit": 3},
    )
    assert envelope["status"] == "success"
    # Don't assert on result count — sandbox catalogs can vary.
    assert "results" in envelope["data"]


@_REQUIRES_MOUSER_LIVE
async def test_mouser_live_search_smokes(mcp_client) -> None:
    envelope = await call_tool(
        mcp_client,
        "mouser.search",
        {"query": "STM32H7", "limit": 3},
    )
    assert envelope["status"] == "success"
    assert "results" in envelope["data"]


@_REQUIRES_NEXAR_LIVE
async def test_nexar_live_search_smokes(mcp_client) -> None:
    envelope = await call_tool(
        mcp_client,
        "nexar.search",
        {"query": "STM32H7", "limit": 3},
    )
    assert envelope["status"] == "success"
    assert "results" in envelope["data"]
