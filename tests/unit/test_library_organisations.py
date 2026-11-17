"""Organisations as library entries (FORGE-450).

Live evidence (local library, 2026-11-17): 565 components carried their maker as text, with
"TI" and "Texas Instruments" counted as two makers; suppliers were text in offers (Mouser 214,
LCSC 128); 29 service bureaus existed only as 130 machine instances named "Protolabs UK: ...".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from twin_core.library.catalog import CatalogError, SqliteCatalog
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.library.organisation_migration import apply_plan, plan_organisations
from twin_core.library.organisations import (
    ORG_REGIONS,
    ORG_ROLES,
    ORG_STATUSES,
    org_key,
)
from twin_core.library.transfer import export_library, import_library

OFFER = {
    "read_on": "2026-11-15",
    "via": "test",
    "currency": "GBP",
    "breaks": [{"qty": 1, "unit_price": 1.0}],
}


def _part(name: str, maker: str, *suppliers: str) -> dict[str, Any]:
    return {
        "name": name,
        "family": "electronic",
        "origin": "purchased",
        "manufacturer": maker,
        "mpn": name.upper(),
        "suppliers": [{"supplier": s, "sku": f"{s}-{name}", "offer": OFFER} for s in suppliers],
    }


def _ti(catalog: SqliteCatalog) -> Any:
    return catalog.register(
        "organisation",
        {
            "title": "Texas Instruments",
            "roles": ["manufacturer"],
            "aliases": ["TI"],
            "country": "US",
        },
        by="test",
    )


def test_org_key_ignores_case_punctuation_and_legal_form() -> None:
    assert org_key("Texas Instruments Inc.") == org_key("texas instruments") == "texas instruments"
    assert org_key("Proto Labs Limited") == "proto labs"
    # a short form is an alias, not a key match
    assert org_key("TI") != org_key("Texas Instruments")
    # a lone legal word is kept rather than emptied
    assert org_key("Co") == "co"


def test_two_spellings_resolve_to_one_record() -> None:
    # FORGE-450 acceptance
    catalog = SqliteCatalog()
    ti = _ti(catalog)
    for text in ("TI", "Texas Instruments Inc.", "texas instruments", ti.id, "texas_instruments"):
        found = catalog.resolve_organisation(text, viewer=None)
        assert found is not None and found.id == ti.id, text
    assert catalog.get("TI", "organisation", viewer=None).id == ti.id
    assert catalog.resolve_organisation("Analog Devices", viewer=None) is None


def test_two_organisations_may_not_share_a_spelling() -> None:
    catalog = SqliteCatalog()
    _ti(catalog)
    with pytest.raises(CatalogError, match="already names Texas Instruments"):
        catalog.register(
            "organisation",
            {"title": "Thermal Imaging", "roles": ["manufacturer"], "aliases": ["TI"]},
            by="t",
        )


def test_an_organisation_is_checked() -> None:
    catalog = SqliteCatalog()
    bad = [
        ({"title": "X", "roles": []}, "roles"),
        ({"title": "X", "roles": ["reseller"]}, "roles"),
        ({"title": "X", "roles": ["distributor"], "country": "United Kingdom"}, "ISO 3166"),
        ({"title": "X", "roles": ["distributor"], "regions": ["Mars"]}, "regions"),
        ({"title": "X", "roles": ["distributor"], "status": "blocked"}, "says why"),
        ({"title": "X", "roles": ["distributor"], "website": "example.com"}, "http"),
        ({"roles": ["distributor"]}, "title"),
    ]
    for data, message in bad:
        with pytest.raises(CatalogError, match=message):
            catalog.register("organisation", data, by="t")


def test_a_component_links_to_its_maker_and_sellers() -> None:
    catalog = SqliteCatalog()
    ti = _ti(catalog)
    mouser = catalog.register(
        "organisation",
        {"title": "Mouser Electronics", "roles": ["distributor"], "aliases": ["Mouser"]},
        by="t",
    )
    part = catalog.register("component", _part("sn74ahct125n", "TI", "Mouser", "LCSC"), by="t")
    assert part.manufacturer_org == ti.id
    assert [row.get("org") for row in part.suppliers] == [mouser.id, None]
    # an organisation added later links what already names it
    lcsc = catalog.register("organisation", {"title": "LCSC", "roles": ["distributor"]}, by="t")
    again = catalog.get(part.id, viewer=None)
    assert [row.get("org") for row in again.suppliers] == [mouser.id, lcsc.id]
    assert again.version == part.version + 1


def test_an_explicit_link_must_name_an_organisation() -> None:
    catalog = SqliteCatalog()
    with pytest.raises(CatalogError, match="manufacturer_org"):
        catalog.register(
            "component", {**_part("x1", "Nobody"), "manufacturer_org": "or_missing"}, by="t"
        )
    ti = _ti(catalog)
    part = catalog.register("component", _part("x2", "Somebody Else"), by="t")
    assert part.manufacturer_org == ""
    changed = catalog.update(part.id, {"manufacturer_org": "TI"}, by="t", editor=None)
    assert changed.manufacturer_org == ti.id
    # changing the maker's text drops the old link and resolves the new one
    changed = catalog.update(part.id, {"manufacturer": "Nobody"}, by="t", editor=None)
    assert changed.manufacturer_org == ""


def test_status_and_roles_change_with_history() -> None:
    catalog = SqliteCatalog()
    ti = _ti(catalog)
    with pytest.raises(CatalogError, match="says why"):
        catalog.update(ti.id, {"status": "blocked"}, by="t", editor=None)
    blocked = catalog.update(
        ti.id, {"status": "blocked", "status_reason": "test"}, by="t", editor=None
    )
    assert blocked.status == "blocked" and blocked.version == 2
    both = catalog.update(ti.id, {"roles": ["manufacturer", "distributor"]}, by="t", editor=None)
    assert both.roles == ["manufacturer", "distributor"] and both.status == "blocked"
    assert [o.title for o in catalog.list("organisation", family="distributor", viewer=None)] == [
        "Texas Instruments"
    ]


def test_export_and_import_keep_the_links() -> None:
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    _ti(catalog)
    catalog.register("component", _part("lm358", "TI"), by="t")
    doc = export_library(catalog, machines)
    assert doc["entries"]["organisation"][0]["title"] == "Texas Instruments"

    target, target_machines = SqliteCatalog(), SqliteMachineLibrary()
    report = import_library(doc, target, target_machines, by="test import")
    assert report["added"] == {"organisation": 1, "component": 1}
    org = target.resolve_organisation("TI", viewer=None)
    part = target.get("lm358", "component", viewer=None)
    assert org is not None and part.manufacturer_org == org.id
    # the exporting library's id is never carried over
    assert org.id != doc["entries"]["organisation"][0]["id"]


def test_the_migration_merges_spellings_and_links_everything() -> None:
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    catalog.register("component", _part("a1", "TI", "Mouser"), by="t")
    catalog.register("component", _part("a2", "Texas Instruments Inc.", "Mouser"), by="t")
    catalog.register("component", _part("a3", "Adafruit", "Adafruit"), by="t")
    catalog.register("component", _part("a4", "Adafruit (made by Somebody)"), by="t")
    for name in ("Protolabs UK: CNC milling", "Protolabs UK: Injection moulding"):
        machines.register(
            {
                "layer": "instance",
                "name": name,
                "kind": "cnc_mill",
                "route": "vendor",
                "location": "Telford, UK",
            },
            by="t",
        )
    plan = plan_organisations(catalog, machines)
    titles = {p["title"]: p for p in plan["proposed"]}
    assert titles["Texas Instruments"]["aliases"] == ["TI", "Texas Instruments Inc."]
    assert titles["Adafruit"]["roles"] == ["distributor", "manufacturer"]
    assert (
        titles["Protolabs UK"]["roles"] == ["fabricator"]
        and titles["Protolabs UK"]["machines"] == 2
    )
    # a qualified name is left for a person, not merged
    assert {"name": "Adafruit", "also_seen_as_part_of": ["Adafruit (made by Somebody)"]} in plan[
        "review"
    ]

    result = apply_plan(plan, catalog, machines, by="test")
    assert result["failed"] == [] and result["bureaus_linked"] == 2
    ti = catalog.resolve_organisation("TI", viewer=None)
    assert {catalog.get(n, "component", viewer=None).manufacturer_org for n in ("a1", "a2")} == {
        ti.id
    }
    bureau = catalog.resolve_organisation("Protolabs UK", viewer=None)
    assert all(m.as_dict()["operator_org"] == bureau.id for m in machines.list(viewer=None))
    assert "Telford, UK" in bureau.notes and bureau.status == "unreviewed" and bureau.country == ""
    # running it again proposes nothing new
    assert plan_organisations(catalog, machines)["proposed"] == []


def test_the_tool_lists_match_the_library() -> None:
    from tool_registry.tools.library import catalog_tools

    assert catalog_tools.ORG_ROLES == list(ORG_ROLES)
    assert catalog_tools.ORG_STATUSES == list(ORG_STATUSES)
    assert catalog_tools.ORG_REGIONS == list(ORG_REGIONS)
    assert {
        "library.list_organisations",
        "library.get_organisation",
    } <= catalog_tools.catalog_tool_ids()


def test_the_routes_resolve_and_migrate() -> None:
    from api_gateway.library import catalog_routes
    from api_gateway.library.routes import init_machine_library

    catalog = SqliteCatalog()
    catalog_routes.init_catalog(catalog)
    init_machine_library(SqliteMachineLibrary())
    try:
        app = FastAPI()
        app.include_router(catalog_routes.router)
        client = TestClient(app)
        catalog.register("component", _part("b1", "TI"), by="t")
        dry = client.post("/v1/library/organisations/migrate").json()
        assert dry["applied"] is False and catalog.list("organisation", viewer=None) == []
        done = client.post("/v1/library/organisations/migrate?apply=true").json()
        assert done["applied"] is True and done["created"] == 1
        assert (
            client.get("/v1/library/organisations/resolve", params={"name": "ti"}).json()["entry"][
                "title"
            ]
            == "Texas Instruments"
        )
        assert (
            client.get("/v1/library/organisations/resolve", params={"name": "nobody"}).status_code
            == 404
        )
        assert (
            len(
                client.get("/v1/library/organisations", params={"role": "manufacturer"}).json()[
                    "entries"
                ]
            )
            == 1
        )
    finally:
        catalog_routes.init_catalog(None)
        init_machine_library(None)
