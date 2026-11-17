"""One search across the whole library (FORGE-895).

Live evidence (2026-11-17): the library had no text search. REST and MCP listed a kind by
family and fetched one entry by exact id or name; the dashboard filtered in the browser.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from twin_core.library.catalog import SqliteCatalog
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.library.search import SEARCH_KINDS, search_library

OFFER = {
    "read_on": "2026-11-15",
    "via": "test",
    "currency": "GBP",
    "breaks": [{"qty": 1, "unit_price": 1.0}],
}


def _stocked() -> tuple[SqliteCatalog, SqliteMachineLibrary]:
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    catalog.register(
        "organisation",
        {"title": "Texas Instruments", "roles": ["manufacturer"], "aliases": ["TI"]},
        by="t",
    )
    catalog.register(
        "organisation",
        {
            "title": "Seeed Studio",
            "roles": ["manufacturer", "distributor"],
            "country": "CN",
            "regions": ["Global"],
        },
        by="t",
    )
    catalog.register(
        "organisation",
        {
            "title": "Mouser Electronics",
            "roles": ["distributor"],
            "aliases": ["Mouser"],
            "regions": ["UK", "EU"],
        },
        by="t",
    )
    catalog.register(
        "organisation",
        {
            "title": "Shady Parts",
            "roles": ["distributor"],
            "status": "blocked",
            "status_reason": "counterfeits",
        },
        by="t",
    )
    catalog.register(
        "organisation",
        {"title": "Protolabs UK", "roles": ["fabricator"], "country": "GB", "regions": ["UK"]},
        by="t",
    )
    catalog.register(
        "component",
        {
            "name": "seeed_xiao_esp32s3",
            "title": "Seeed Studio XIAO ESP32S3",
            "family": "module",
            "origin": "purchased",
            "manufacturer": "Seeed Studio",
            "mpn": "113991114",
            "suppliers": [{"supplier": "Mouser", "sku": "713-113991114", "offer": OFFER}],
        },
        by="t",
    )
    catalog.register(
        "component",
        {
            "name": "sn74ahct125n",
            "title": "SN74AHCT125N quad buffer",
            "family": "electronic",
            "origin": "purchased",
            "manufacturer": "TI",
            "mpn": "SN74AHCT125N",
            "suppliers": [{"supplier": "Shady Parts", "sku": "x1", "offer": OFFER}],
        },
        by="t",
    )
    catalog.register(
        "material",
        {"name": "pla", "title": "PLA filament", "family": "polymer", "forms": ["filament"]},
        by="t",
    )
    bureau = catalog.resolve_organisation("Protolabs UK", viewer=None)
    machines.register(
        {
            "layer": "instance",
            "name": "Protolabs UK: SLS 3D printing",
            "kind": "sls_printer",
            "route": "vendor",
            "operator_org": bureau.id,
        },
        by="t",
    )
    machines.register(
        {"layer": "instance", "name": "My CR-10", "kind": "fdm_printer", "route": "in_house"},
        by="t",
    )
    return catalog, machines


def _titles(result: dict[str, Any]) -> list[str]:
    return [r["title"] for r in result["results"]]


def test_one_word_finds_the_part_first() -> None:
    catalog, machines = _stocked()
    out = search_library(catalog, machines, "xiao")
    assert _titles(out) == ["Seeed Studio XIAO ESP32S3"]
    assert out["results"][0]["matched"] == ["name"]


def test_every_word_must_match() -> None:
    catalog, machines = _stocked()
    assert _titles(search_library(catalog, machines, "xiao esp32s3")) == [
        "Seeed Studio XIAO ESP32S3"
    ]
    assert search_library(catalog, machines, "xiao stm32")["results"] == []


def test_a_maker_alias_finds_its_parts() -> None:
    catalog, machines = _stocked()
    out = search_library(catalog, machines, "ti", kinds=["component"])
    assert _titles(out) == ["SN74AHCT125N quad buffer"]
    assert "manufacturer" in out["results"][0]["matched"]


def test_an_mpn_or_a_sku_finds_the_part() -> None:
    catalog, machines = _stocked()
    assert _titles(search_library(catalog, machines, "113991114"))[0] == "Seeed Studio XIAO ESP32S3"
    assert _titles(search_library(catalog, machines, "713-113991114")) == [
        "Seeed Studio XIAO ESP32S3"
    ]


def test_a_part_sold_only_by_a_blocked_seller_says_so() -> None:
    catalog, machines = _stocked()
    hit = search_library(catalog, machines, "sn74ahct125n")["results"][0]
    assert hit["blocked"] == ["every seller is blocked"]
    clean = search_library(catalog, machines, "xiao")["results"][0]
    assert "blocked" not in clean


def test_region_filters_organisations_parts_and_machines() -> None:
    catalog, machines = _stocked()
    uk = search_library(catalog, machines, "", region="UK")
    titles = _titles(uk)
    # a UK seller (Mouser) makes the XIAO pass; Seeed serves Global; the CR-10 is your own
    assert "Seeed Studio XIAO ESP32S3" in titles and "Protolabs UK" in titles
    assert "Mouser Electronics" in titles and "Seeed Studio" in titles
    assert "Protolabs UK: SLS 3D printing" in titles and "My CR-10" in titles
    # an unlinked kind is not something a region can vouch for
    assert "PLA filament" not in titles
    assert "SN74AHCT125N quad buffer" not in titles


def test_role_and_status_filter() -> None:
    catalog, machines = _stocked()
    fab = search_library(catalog, machines, "", kinds=["organisation"], role="fabricator")
    assert _titles(fab) == ["Protolabs UK"]
    blocked = search_library(catalog, machines, "", kinds=["organisation"], status="blocked")
    assert _titles(blocked) == ["Shady Parts"]


def test_a_machine_answers_to_its_operator() -> None:
    catalog, machines = _stocked()
    out = search_library(catalog, machines, "protolabs sls")
    assert _titles(out)[0] == "Protolabs UK: SLS 3D printing"


def test_the_route_and_the_tool_agree_on_the_kinds() -> None:
    from api_gateway.library import catalog_routes
    from api_gateway.library.routes import init_machine_library
    from tool_registry.tools.library import catalog_tools

    assert catalog_tools.SEARCH_KINDS == list(SEARCH_KINDS)
    assert "library.search" in catalog_tools.catalog_tool_ids()
    catalog, machines = _stocked()
    catalog_routes.init_catalog(catalog)
    init_machine_library(machines)
    try:
        app = FastAPI()
        app.include_router(catalog_routes.router)
        client = TestClient(app)
        out = client.get("/v1/library/search", params=[("q", "pla"), ("kind", "material")]).json()
        assert _titles(out) == ["PLA filament"] and out["filters"]["kinds"] == ["material"]
        assert (
            client.get("/v1/library/search", params={"q": "x", "kind": "planet"}).status_code == 422
        )
    finally:
        catalog_routes.init_catalog(None)
        init_machine_library(None)
