"""A passive product's studio carries no electronics warnings.

Testing a travelling camera case (no board, no battery) on hosted staging,
the studio warned that UK PSTI applies and that the board and battery
trade-offs were unexplained. PSTI covers connectable products only, and a
product with no board has neither trade-off.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine.formgraph.build_page import validate_programme


def _cfg(board: str, battery: str) -> dict[str, Any]:
    return {
        "power": {"board": board, "battery": battery},
        "programme": {"segment": "consumer", "markets": ["UK"], "rationale": {}},
    }


def _warnings(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []
    validate_programme(cfg, errors, warnings)
    return warnings


def test_a_passive_product_gets_no_psti_or_electronics_trade_offs() -> None:
    w = _warnings(_cfg("none", "none"))
    assert not [x for x in w if "PSTI" in x]
    assert not [x for x in w if "rationale.board" in x or "rationale.battery" in x]
    assert any("rationale.process" in x for x in w)  # the real trade-offs remain


def test_a_connected_consumer_product_still_gets_psti() -> None:
    w = _warnings(_cfg("esp32-s3", "li-ion"))
    assert sum("PSTI" in x for x in w) == 3
    assert any("rationale.board" in x for x in w)
    assert any("rationale.battery" in x for x in w)
