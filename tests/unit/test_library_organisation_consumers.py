"""Where organisations change what the app does (FORGE-450, part 2).

A blocked vendor is flagged in component selection; two spellings of one maker give one BOM
identity; the studio's cost skips a blocked seller's offer and prefers one serving the home
region.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine.library_cost import best_offer, price_parts
from api_gateway.twin.component_recorder import make_component_recorder
from api_gateway.twin.component_selection import make_component_selector
from api_gateway.twin.decision_recorder import make_decision_recorder
from twin_core.api import InMemoryTwinAPI

TI = {"id": "or_ti", "title": "Texas Instruments", "status": "approved", "status_reason": ""}
SHADY = {
    "id": "or_shady",
    "title": "Shady Parts",
    "status": "blocked",
    "status_reason": "counterfeits",
}
ORGS = {"texas instruments": TI, "ti": TI, "shady": SHADY}


async def _resolve(text: str) -> dict[str, Any] | None:
    return ORGS.get(text.strip().lower())


async def test_a_blocked_maker_is_flagged_and_the_part_still_recorded() -> None:
    record = make_component_recorder(InMemoryTwinAPI.create(), organisations=_resolve)
    out = await record(mpn="X1", manufacturer="Shady", category="ic", purchase_unit="each")
    assert out["node_id"]
    assert out["blocked"] == ["maker Shady Parts is blocked: counterfeits"]
    assert out["organisations"]["manufacturer"]["id"] == "or_shady"


async def test_two_spellings_of_a_maker_give_one_bom_identity() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_component_recorder(twin, organisations=_resolve)
    a = await record(
        mpn="LM358", manufacturer="TI", category="opamp", purchase_unit="each", role="a"
    )
    b = await record(
        mpn="LM358",
        manufacturer="Texas Instruments",
        category="opamp",
        purchase_unit="each",
        role="b",
    )
    items = {str(i.id): i for i in await twin.list_bom_items()}
    nodes = [items[a["node_id"]], items[b["node_id"]]]
    assert {n.global_asset_id for n in nodes} == {"urn:metaforge:bom:Texas-Instruments:LM358"}
    assert all(n.specifications["manufacturer_org"] == "or_ti" for n in nodes)
    assert "blocked" not in a


async def test_without_a_resolver_nothing_changes() -> None:
    twin = InMemoryTwinAPI.create()
    out = await make_component_recorder(twin)(
        mpn="LM358", manufacturer="TI", category="opamp", purchase_unit="each"
    )
    node = {str(i.id): i for i in await twin.list_bom_items()}[out["node_id"]]
    assert node.global_asset_id == "urn:metaforge:bom:TI:LM358" and "organisations" not in out


async def test_a_blocked_candidate_is_flagged_in_selection() -> None:
    twin = InMemoryTwinAPI.create()
    select = make_component_selector(
        twin,
        decision_recorder=make_decision_recorder(twin),
        component_recorder=make_component_recorder(twin, organisations=_resolve),
        organisations=_resolve,
    )
    out = await select(
        candidates=[
            {"mpn": "GOOD1", "manufacturer": "TI", "specs": {}},
            {"mpn": "BAD1", "manufacturer": "Shady", "specs": {}},
        ],
        required_specs={},
        selected_mpn="GOOD1",
        category="ic",
        purchase_unit="each",
        title="Buffer",
        rationale="TI part",
    )
    by = {c["mpn"]: c for c in out["candidates"]}
    assert by["BAD1"]["blocked"] == "maker Shady Parts is blocked: counterfeits"
    assert "blocked" not in by["GOOD1"] and "blocked" not in out


def _offer(price: float, currency: str = "GBP") -> dict[str, Any]:
    return {
        "read_on": "2026-11-15",
        "via": "test",
        "currency": currency,
        "breaks": [{"qty": 1, "unit_price": price}],
    }


def test_cost_skips_a_blocked_seller_and_prefers_the_home_region() -> None:
    entry = {
        "suppliers": [
            {"supplier": "Shady", "sku": "s", "org": "or_shady", "offer": _offer(1.0)},
            {"supplier": "Far", "sku": "f", "org": "or_far", "offer": _offer(2.0)},
            {"supplier": "Near", "sku": "n", "org": "or_near", "offer": _offer(3.0)},
        ],
        "_orgs": {
            "or_shady": {"status": "blocked", "regions": ["UK"]},
            "or_far": {"status": "approved", "regions": ["Asia"]},
            "or_near": {"status": "unreviewed", "regions": ["UK"]},
        },
    }
    assert best_offer(entry, 1)["source"].startswith("Near")
    # with no region known on either, the cheaper wins as before
    entry["_orgs"]["or_near"]["regions"] = []
    entry["_orgs"]["or_far"]["regions"] = []
    assert best_offer(entry, 1)["source"].startswith("Far")


def test_a_part_sold_only_by_blocked_sellers_says_so() -> None:
    entry = {
        "version": 1,
        "suppliers": [{"supplier": "Shady", "sku": "s", "org": "or_shady", "offer": _offer(1.0)}],
        "_orgs": {"or_shady": {"status": "blocked", "regions": []}},
    }
    config = {"context": {"components": {"parts": [{"id": "x-a", "library": "x"}]}}}
    out, notes = price_parts(config, lambda name: entry)
    assert "cost" not in out["context"]["components"]["parts"][0]
    assert any("blocks" in n and "x-a" in n for n in notes)
