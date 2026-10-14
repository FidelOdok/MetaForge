"""A distributor price is a number or unknown, never a silent zero."""

from __future__ import annotations

import pytest

from tool_registry.tools.digikey.adapter import DigiKeyAdapter
from tool_registry.tools.distributors.base import parse_price
from tool_registry.tools.mouser.adapter import MouserAdapter
from tool_registry.tools.nexar.adapter import NexarAdapter


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("£89.42", 89.42),
        ("$1.23", 1.23),
        ("$1,234.56", 1234.56),
        ("1.234,56 €", 1234.56),
        ("0,85 €", 0.85),
        ("1,234", 1234.0),
        (3.5, 3.5),
        (0, 0.0),
        ("", None),
        ("N/A", None),
        ("Quote", None),
        (None, None),
        (-1, None),
        (True, None),
    ],
)
def test_parse_price(raw: object, expected: float | None) -> None:
    assert parse_price(raw) == expected


def _mouser(breaks: list[dict]) -> dict:
    return {"SearchResults": {"Parts": [{"PriceBreaks": breaks}]}}


def test_mouser_gbp_prices_are_read_not_zero() -> None:
    """Live sandbox response, 2026-10-14: every GBP price used to read 0.0."""
    out = MouserAdapter(api_key="k")._map_pricing(
        _mouser(
            [
                {"Quantity": 1, "Price": "£89.42", "Currency": "GBP"},
                {"Quantity": 10, "Price": "£83.03", "Currency": "GBP"},
            ]
        )
    )
    assert [(b.quantity, b.unit_price, b.currency) for b in out] == [
        (1, 89.42, "GBP"),
        (10, 83.03, "GBP"),
    ]


def test_mouser_drops_an_unreadable_price_or_missing_currency() -> None:
    out = MouserAdapter(api_key="k")._map_pricing(
        _mouser(
            [
                {"Quantity": 1, "Price": "Quote", "Currency": "GBP"},
                {"Quantity": 5, "Price": "£2.00"},
                {"Quantity": 10, "Price": "£1.50", "Currency": "GBP"},
            ]
        )
    )
    assert [(b.quantity, b.unit_price) for b in out] == [(10, 1.5)]


def test_digikey_skips_a_break_with_no_price() -> None:
    adapter = DigiKeyAdapter.__new__(DigiKeyAdapter)
    out = adapter._map_pricing(
        {"StandardPricing": [{"BreakQuantity": 1}, {"BreakQuantity": 10, "UnitPrice": 0.42}]}
    )
    assert [(b.quantity, b.unit_price) for b in out] == [(10, 0.42)]


def test_nexar_keeps_currencies_apart_and_skips_missing_prices() -> None:
    data = {
        "supSearchMpn": {
            "results": [
                {
                    "part": {
                        "sellers": [
                            {
                                "offers": [
                                    {
                                        "prices": [
                                            {"quantity": 1, "price": 2.0, "currency": "USD"},
                                            {"quantity": 1, "price": 1.5, "currency": "GBP"},
                                            {"quantity": 1, "currency": "USD"},
                                            {"quantity": 1, "price": 1.8, "currency": "USD"},
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }
    }
    adapter = NexarAdapter.__new__(NexarAdapter)
    out = adapter._map_pricing(data)
    assert [(b.currency, b.quantity, b.unit_price) for b in out] == [
        ("GBP", 1, 1.5),
        ("USD", 1, 1.8),
    ]
