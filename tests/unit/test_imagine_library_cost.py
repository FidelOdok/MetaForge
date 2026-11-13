"""A linked part is priced from the component library's offers (MetaForge, 2026-11-12).

A real part in ``context.components.parts`` that names its library entry (``library``) and
carries no ``cost`` of its own gets one on each studio write: the cheapest offer confirmed
on a live page, at the break that applies to the build quantity (``process.qty``),
preferring pounds because the page adds only pounds. An unconfirmed offer (a search
snapshot) is never used. The derived cost says where it came from and is derived again on
the next write; a cost the agent wrote stays.
"""

from __future__ import annotations

import copy
from typing import Any

from api_gateway.imagine import library_cost


def offer(currency: str, breaks: list[tuple[int, float]], *, confirmed: bool = True) -> dict:
    return {
        "read_on": "2026-11-12",
        "via": "mouser.get_pricing",
        "currency": currency,
        "breaks": [{"qty": q, "unit_price": p} for q, p in breaks],
        "confirmed": confirmed,
    }


LIBRARY: dict[str, dict[str, Any]] = {
    "alps_switch": {
        "version": 3,
        "suppliers": [
            {
                "supplier": "Mouser",
                "sku": "688-SKRTLA",
                "offer": offer("GBP", [(1, 0.32), (10, 0.274)]),
            },
            {"supplier": "Cheap", "sku": "c", "offer": offer("GBP", [(1, 0.01)], confirmed=False)},
        ],
    },
    "usd_only": {
        "version": 1,
        "suppliers": [{"supplier": "LCSC", "sku": "C1", "offer": offer("USD", [(1, 9.73)])}],
    },
    "no_offers": {"version": 1, "suppliers": [{"supplier": "Mouser", "sku": "x"}]},
}


def lookup(name: str) -> dict[str, Any] | None:
    return LIBRARY.get(name)


def studio(*parts: dict[str, Any], qty: int = 1) -> dict[str, Any]:
    return {
        "process": {"qty": qty},
        "context": {"components": {"schemaVersion": 1, "parts": list(parts)}},
    }


def cost_of(cfg: dict[str, Any], i: int = 0) -> dict[str, Any] | None:
    return cfg["context"]["components"]["parts"][i].get("cost")


def test_the_cheapest_confirmed_pound_offer_at_the_build_quantity_is_used() -> None:
    cfg, notes = library_cost.price_parts(studio({"id": "btn", "library": "alps_switch"}), lookup)
    cost = cost_of(cfg)
    assert cost is not None
    assert cost["unit"] == 0.32 and cost["currency"] == "GBP" and cost["qty"] == 1
    assert "Mouser 688-SKRTLA" in cost["source"] and "2026-11-12" in cost["source"]
    assert cost["derived_from"] == "library"
    assert notes == []


def test_a_larger_build_uses_the_break_it_reaches() -> None:
    cfg, _ = library_cost.price_parts(
        studio({"id": "btn", "library": "alps_switch"}, qty=25), lookup
    )
    assert cost_of(cfg)["unit"] == 0.274


def test_an_unconfirmed_offer_is_never_used_even_when_cheaper() -> None:
    cfg, _ = library_cost.price_parts(studio({"id": "btn", "library": "alps_switch"}), lookup)
    assert cost_of(cfg)["unit"] != 0.01


def test_an_offer_in_another_currency_is_kept_in_that_currency() -> None:
    cfg, notes = library_cost.price_parts(studio({"id": "g", "library": "usd_only"}), lookup)
    assert cost_of(cfg)["currency"] == "USD"
    assert notes == []


def test_no_confirmed_price_leaves_the_part_unpriced_and_says_so() -> None:
    cfg, notes = library_cost.price_parts(studio({"id": "x", "library": "no_offers"}), lookup)
    assert cost_of(cfg) is None
    assert len(notes) == 1 and "x" in notes[0] and "confirmed" in notes[0]


def test_a_cost_the_agent_wrote_stays_and_a_derived_one_is_derived_again() -> None:
    own = {"unit": 5.0, "currency": "GBP", "source": "quote from a friend"}
    cfg, _ = library_cost.price_parts(
        studio({"id": "btn", "library": "alps_switch", "cost": own}), lookup
    )
    assert cost_of(cfg) == own
    stale = {"unit": 9.0, "currency": "GBP", "source": "old", "derived_from": "library"}
    cfg, _ = library_cost.price_parts(
        studio({"id": "btn", "library": "alps_switch", "cost": stale}), lookup
    )
    assert cost_of(cfg)["unit"] == 0.32


def test_a_derived_cost_is_dropped_when_the_library_has_no_price_any_more() -> None:
    stale = {"unit": 9.0, "currency": "GBP", "source": "old", "derived_from": "library"}
    cfg, _ = library_cost.price_parts(
        studio({"id": "x", "library": "no_offers", "cost": stale}), lookup
    )
    assert cost_of(cfg) is None


def test_unlinked_and_stand_in_parts_are_left_alone_and_input_is_unchanged() -> None:
    before = studio({"id": "a"}, {"id": "b", "library": "alps_switch", "standIn": True})
    kept = copy.deepcopy(before)
    cfg, notes = library_cost.price_parts(before, lookup)
    assert before == kept
    assert cost_of(cfg, 0) is None and cost_of(cfg, 1) is None and notes == []


def test_a_broken_lookup_prices_nothing_and_does_not_raise() -> None:
    def broken(name: str) -> dict[str, Any] | None:
        raise RuntimeError("catalog unavailable")

    cfg, notes = library_cost.price_parts(studio({"id": "btn", "library": "alps_switch"}), broken)
    assert cost_of(cfg) is None and notes == []


async def test_a_studio_write_prices_a_linked_part_from_the_real_catalog() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.library import catalog_routes
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI
    from twin_core.library.catalog import SqliteCatalog

    ops.init_operations(None)
    catalog_routes.init_catalog(SqliteCatalog())
    try:
        catalog_routes.get_catalog().register(
            "component",
            {
                "name": "alps_switch",
                "family": "electronic",
                "origin": "purchased",
                "suppliers": [
                    {"supplier": "Mouser", "sku": "688-SKRTLA", "offer": offer("GBP", [(1, 0.32)])}
                ],
            },
        )
        cfg = full_config()
        part = cfg["context"]["components"]["parts"][0]
        part["library"] = "alps_switch"
        part.pop("cost", None)
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        cost = out.config["context"]["components"]["parts"][0]["cost"]
        assert cost["unit"] == 0.32 and cost["currency"] == "GBP"
        assert cost["derived_from"] == "library"
    finally:
        catalog_routes.init_catalog(None)
        ops.init_operations(None)
