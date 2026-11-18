"""Find a component: the library first, then distributors, then adopt (FORGE-899).

Live evidence (sandbox, 2026-11-17): a forge chat turn asked to use library parts searched
component.search_parametric, a separate catalog, and reported the parts 'empty'; an Imagine
turn spent most of its steps listing and paging library entries. component.find searches the
library and the distributors in one call; component.adopt adds a chosen part once.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.components.finder import ComponentFinderServer
from tool_registry.tools.distributors.base import (
    DistributorAdapter,
    LifecycleStatus,
    PartSearchResult,
    PricingBreak,
)
from twin_core.library.catalog import SqliteCatalog
from twin_core.library.search import search_library


class FakeDistributor(DistributorAdapter):
    def __init__(self, name: str, parts: list[PartSearchResult], *, fail: bool = False) -> None:
        self._name, self._parts, self._fail = name, parts, fail

    @property
    def name(self) -> str:
        return self._name

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        if self._fail:
            raise RuntimeError("401 bad credentials")
        words = query.lower().split()
        return [
            p
            for p in self._parts
            if all(w in f"{p.mpn} {p.description} {p.manufacturer}".lower() for w in words)
        ][:limit]

    async def get_part_details(self, mpn: str) -> Any:
        return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        return [
            PricingBreak(quantity=1, unit_price=5.59, currency="GBP"),
            PricingBreak(quantity=10, unit_price=5.10, currency="GBP"),
        ]

    async def get_availability(self, mpn: str) -> Any:
        return None

    async def close(self) -> None:
        return None


def _part(
    mpn: str,
    desc: str,
    *,
    stock: int = 100,
    life: LifecycleStatus = LifecycleStatus.ACTIVE,
    sheet: bool = True,
) -> PartSearchResult:
    return PartSearchResult(
        mpn=mpn,
        manufacturer="Seeed Studio",
        description=desc,
        distributor="Mouser",
        distributor_pn=f"713-{mpn}",
        stock_qty=stock,
        lifecycle_status=life,
        datasheet_url="https://example.test/ds.pdf" if sheet else None,
        product_url="https://mouser.example/p",
    )


class LocalLibrary:
    """The gateway's library service, over an in-memory catalog."""

    def __init__(self, catalog: SqliteCatalog) -> None:
        self.catalog = catalog
        self.registered: list[dict[str, Any]] = []

    async def search(self, args: dict[str, Any]) -> dict[str, Any]:
        class NoMachines:
            def list(self, viewer: Any = None) -> list[Any]:
                return []

        return search_library(
            self.catalog,
            NoMachines(),
            args.get("q", ""),
            kinds=args.get("kinds"),
            region=args.get("region"),
            limit=args.get("limit", 50),
        )

    async def register_entry(self, kind: str, data: dict[str, Any], by: str) -> dict[str, Any]:
        self.registered.append(data)
        return {"entry": self.catalog.register(kind, data, by=by).as_dict()}


def _library() -> LocalLibrary:
    catalog = SqliteCatalog()
    catalog.register(
        "component",
        {
            "name": "seeed_xiao_esp32s3",
            "title": "Seeed Studio XIAO ESP32S3",
            "family": "module",
            "origin": "purchased",
            "manufacturer": "Seeed Studio",
            "mpn": "113991114",
        },
        by="t",
    )
    return LocalLibrary(catalog)


MOUSER = FakeDistributor(
    "Mouser",
    [
        _part("113991114", "XIAO ESP32S3 module"),
        _part("102010551", "XIAO ESP32C3 module", stock=0),
        _part("OLD-1", "XIAO legacy module", life=LifecycleStatus.OBSOLETE),
    ],
)


async def test_the_library_comes_first_and_a_held_part_is_marked() -> None:
    server = ComponentFinderServer(_library(), [MOUSER])
    out = await server.handle_find({"need": "xiao esp32s3"})
    assert [h["name"] for h in out["library"]] == ["seeed_xiao_esp32s3"]
    assert out["library"][0]["use"] == "set the part's library to 'seeed_xiao_esp32s3'"
    assert out["distributors"][0]["mpn"] == "113991114" and out["distributors"][0]["in_library"]
    assert out["next"].startswith("Prefer a library part")
    assert out["searched"]["distributors"] == {"Mouser": "1 found"}


async def test_distributor_parts_rank_in_production_in_stock_with_a_datasheet() -> None:
    server = ComponentFinderServer(_library(), [MOUSER])
    out = await server.handle_find({"need": "xiao module"})
    order = [h["mpn"] for h in out["distributors"]]
    assert order.index("113991114") < order.index("102010551") < order.index("OLD-1")
    assert all("size" in h["unknown"][0] for h in out["distributors"])


async def test_a_failing_distributor_is_reported_not_fatal() -> None:
    broken = FakeDistributor("DigiKey", [], fail=True)
    server = ComponentFinderServer(_library(), [MOUSER, broken])
    out = await server.handle_find({"need": "xiao esp32c3"})
    assert out["searched"]["distributors"]["DigiKey"].startswith("failed: 401")
    assert out["distributors"][0]["mpn"] == "102010551"
    assert out["next"].startswith("No library part fits")


async def test_library_only_and_nothing_found() -> None:
    server = ComponentFinderServer(_library(), [MOUSER])
    only = await server.handle_find({"need": "xiao", "distributors": False})
    assert only["distributors"] == [] and only["library"]
    nothing = await ComponentFinderServer(_library(), []).handle_find({"need": "flux capacitor"})
    assert nothing["library"] == [] and nothing["distributors"] == []
    assert nothing["searched"]["distributors"] == {"none": "no distributor is configured"}
    with pytest.raises(ValueError, match="need"):
        await server.handle_find({"need": " "})


async def test_adopt_registers_the_part_with_a_dated_offer_and_only_given_sizes() -> None:
    lib = _library()
    server = ComponentFinderServer(lib, [MOUSER])
    out = await server.handle_adopt(
        {
            "mpn": "102010551",
            "manufacturer": "Seeed Studio",
            "distributor": "mouser",
            "family": "module",
            "specs": {
                "length_mm": {
                    "value": 21,
                    "unit": "mm",
                    "source_kind": "datasheet",
                    "source_ref": "https://example.test/ds.pdf p.2",
                },
                "width_mm": {
                    "value": 17.8,
                    "unit": "mm",
                    "source_kind": "datasheet",
                    "source_ref": "https://example.test/ds.pdf p.2",
                },
            },
        }
    )
    entry = out["entry"]
    assert entry["name"] == "seeed_studio_102010551" and entry["mpn"] == "102010551"
    supplier = entry["suppliers"][0]
    assert supplier["supplier"] == "Mouser" and supplier["sku"] == "713-102010551"
    assert supplier["offer"]["breaks"] == [
        {"qty": 1, "unit_price": 5.59},
        {"qty": 10, "unit_price": 5.1},
    ]
    assert supplier["offer"]["stock"] == 0 and supplier["offer"]["via"].startswith("Mouser API")
    assert set(entry["specs"]) == {"length_mm", "width_mm"}  # no height guessed
    assert entry["datasheet"] == "https://example.test/ds.pdf"


async def test_adopt_refuses_a_held_part_an_unknown_distributor_and_a_bad_family() -> None:
    server = ComponentFinderServer(_library(), [MOUSER])
    with pytest.raises(ValueError, match="already holds"):
        await server.handle_adopt(
            {
                "mpn": "113991114",
                "manufacturer": "Seeed Studio",
                "distributor": "Mouser",
                "family": "module",
            }
        )
    with pytest.raises(ValueError, match="not configured"):
        await server.handle_adopt(
            {"mpn": "X", "manufacturer": "Y", "distributor": "Arrow", "family": "module"}
        )
    with pytest.raises(ValueError, match="family"):
        await server.handle_adopt(
            {"mpn": "X", "manufacturer": "Y", "distributor": "Mouser", "family": "toaster"}
        )


async def test_search_intent_reads_the_library_first() -> None:
    from tool_registry.tools.components.adapter import ComponentServer

    class NoStore:
        async def query(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("not used")

    server = ComponentServer(search_store=NoStore(), library=_library())  # type: ignore[arg-type]
    # a sentence rarely matches every word; its longest words are tried alone
    hits = await server._library_matches("I need an esp32s3 board for a lamp")
    assert [h["name"] for h in hits] == ["seeed_xiao_esp32s3"]


class FakeWeb:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = fail

    async def search(self, query: str, *, limit: int = 10) -> list[Any]:
        from tool_registry.tools.web.base import SearchHit

        self.calls.append(query)
        if self._fail:
            raise RuntimeError("429 rate limited")
        return [
            SearchHit(title="Acme Glow Ring", url="https://acme.example/glow", snippet="12 LEDs"),
            SearchHit(title="Glow Ring datasheet", url="https://acme.example/glow.pdf?v=2"),
        ]


async def test_the_web_is_searched_only_when_the_others_find_little() -> None:
    web = FakeWeb()
    server = ComponentFinderServer(_library(), [MOUSER], web=web)
    # the library finds the XIAO: no web search
    found = await server.handle_find({"need": "xiao esp32s3"})
    assert found["web"] == [] and web.calls == [] and found["searched"]["web"] == "not searched"
    # nothing in the library or the distributors: the web, with 'datasheet' added
    glow = await server.handle_find({"need": "glow ring"})
    assert web.calls == ["glow ring datasheet"]
    assert [h["page"] for h in glow["web"]] == ["page", "datasheet"]
    assert glow["web"][0]["unknown"][0] == "maker"
    assert glow["next"].startswith("Only web pages found")
    # on: even with a library hit; off: never
    await server.handle_find({"need": "xiao esp32s3", "web": "on"})
    assert len(web.calls) == 2
    off = await server.handle_find({"need": "glow ring", "web": "off"})
    assert off["web"] == [] and len(web.calls) == 2


async def test_no_web_key_or_a_failing_web_is_said_not_fatal() -> None:
    none = await ComponentFinderServer(_library(), []).handle_find({"need": "glow ring"})
    assert none["searched"]["web"] == "no web search is configured"
    failing = ComponentFinderServer(_library(), [], web=FakeWeb(fail=True))
    out = await failing.handle_find({"need": "glow ring"})
    assert out["web"] == [] and out["searched"]["web"].startswith("failed: 429")
