"""A supplier offer can say it is for a pack: a roll, reel, sheet or bag (FORGE-882)."""

from __future__ import annotations

import pytest

from twin_core.library.catalog import CatalogError, parse_offer

BASE = {
    "read_on": "2026-11-15",
    "via": "Mouser Search API",
    "currency": "GBP",
    "breaks": [{"qty": 1, "unit_price": 49.14}],
}


def test_a_pack_is_kept_with_its_amount_and_unit() -> None:
    out = parse_offer(
        "s[0]", {**BASE, "pack": {"amount": 16.4592, "unit": "m", "note": "18 yd roll"}}
    )
    assert out["pack"] == {"amount": 16.4592, "unit": "m", "note": "18 yd roll"}


def test_an_offer_without_a_pack_is_unchanged() -> None:
    assert "pack" not in parse_offer("s[0]", BASE)


@pytest.mark.parametrize(
    "pack",
    [{"amount": 0, "unit": "m"}, {"amount": 5, "unit": "yd"}, {"unit": "m"}, "18 yd roll"],
)
def test_a_malformed_pack_is_refused(pack: object) -> None:
    # negative control: a pack the price step could not read never goes in silently
    with pytest.raises(CatalogError):
        parse_offer("s[0]", {**BASE, "pack": pack})
