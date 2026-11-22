"""Real part data on a library component (FORGE-906).

A part was one sourced number per spec, a part number and a datasheet link.
Nothing said which limit was an absolute maximum and which the range the
part is specified to work in, whether it was still made, or which KiCad
symbol and footprint draw it, so a design could not rely on it. These pin
the new fields and the check that says what a part still lacks.
"""

from __future__ import annotations

import hashlib
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.library.tenancy import with_ownership
from api_gateway.twin.component_thread import part_data_brief
from tool_registry.tools.components.finder import adopted_lifecycle
from twin_core.library.catalog import CatalogError, SqliteCatalog
from twin_core.library.part_data import (
    check_part_data,
    parse_ecad,
    parse_lifecycle,
    parse_ratings,
)

DS = "https://www.ti.com/lit/ds/symlink/lm1117.pdf"
TODAY = date(2026, 11, 22)

ABS_MAX = {
    "param": "input_voltage_v",
    "kind": "abs_max",
    "max": 20,
    "unit": "V",
    "source_kind": "datasheet",
    "source_ref": f"{DS} table 6.1",
}
RECOMMENDED = {
    "param": "junction_temp_c",
    "kind": "recommended",
    "min": 0,
    "max": 125,
    "unit": "degC",
    "source_kind": "datasheet",
    "source_ref": f"{DS} table 6.3",
}
LIFECYCLE = {
    "status": "active",
    "source_kind": "supplier",
    "source_ref": "https://www.mouser.co.uk/ProductDetail/926-LM1117MPX3.3NOPB",
    "read_on": "2026-11-22",
}
ECAD = {
    "symbol": "Regulator_Linear:LM1117-3.3",
    "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    "source_kind": "standard",
    "source_ref": "KiCad 9 standard library",
}
SPICE = b"* LM1117 macro model\n.SUBCKT LM1117 IN OUT ADJ\n.ENDS\n"


def _spice_model() -> dict[str, Any]:
    return {
        "model_id": "lm1117-spice",
        "version": "1",
        "kind": "spice",
        "engine": "ngspice",
        "entrypoint": "LM1117.lib",
        "usage": ".include LM1117.lib, subcircuit LM1117 IN OUT ADJ.",
        "artifacts": [
            {
                "path": "LM1117.lib",
                "url": "https://www.ti.com/lit/zip/snom123",
                "sha256": hashlib.sha256(SPICE).hexdigest(),
                "size": len(SPICE),
                "licence": "MIT",
                "source_revision": "a" * 40,
            }
        ],
    }


def _complete(**over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "component",
        "family": "electronic",
        "mpn": "LM1117MPX-3.3/NOPB",
        "manufacturer": "Texas Instruments",
        "datasheet": DS,
        "ratings": parse_ratings([ABS_MAX, RECOMMENDED]),
        "lifecycle": parse_lifecycle(LIFECYCLE),
        "ecad": parse_ecad(ECAD),
        "geometry": {"step_url": "https://example.com/sot223.step"},
        "suppliers": [{"supplier": "Mouser", "offer": {"read_on": "2026-11-20"}}],
        "models": [],
    }
    entry.update(over)
    return entry


class TestRatings:
    def test_rows_keep_min_typ_max_conditions_and_source(self) -> None:
        rows = parse_ratings([{**RECOMMENDED, "typ": 25, "conditions": "IOUT = 10 mA"}, ABS_MAX])
        assert rows[0] == {
            "param": "junction_temp_c",
            "kind": "recommended",
            "min": 0.0,
            "typ": 25.0,
            "max": 125.0,
            "unit": "degC",
            "conditions": "IOUT = 10 mA",
            "source_kind": "datasheet",
            "source_ref": f"{DS} table 6.3",
            "needs_review": False,
        }
        assert rows[1]["min"] is None and rows[1]["max"] == 20.0

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"param": "volts"}, "unknown param"),
            ({"kind": "typical"}, "kind must be one of"),
            ({"unit": "mV"}, "is in V"),
            ({"max": None}, "give min, typ or max"),
            ({"min": 30}, "must be in order"),
            ({"source_ref": ""}, "needs its reference"),
        ],
    )
    def test_a_bad_row_is_refused(self, change: dict[str, Any], message: str) -> None:
        with pytest.raises(CatalogError, match=message):
            parse_ratings([{**ABS_MAX, **change}])

    def test_an_assumed_row_is_flagged(self) -> None:
        row = parse_ratings([{**ABS_MAX, "source_kind": "assumption", "source_ref": ""}])[0]
        assert row["needs_review"] is True


class TestLifecycleAndEcad:
    def test_lifecycle_needs_a_status_a_source_and_a_day(self) -> None:
        assert parse_lifecycle(LIFECYCLE)["status"] == "active"
        with pytest.raises(CatalogError, match="status must be one of"):
            parse_lifecycle({**LIFECYCLE, "status": "fine"})
        with pytest.raises(CatalogError, match="YYYY-MM-DD"):
            parse_lifecycle({**LIFECYCLE, "read_on": "today"})

    def test_ecad_ids_are_kicad_library_ids(self) -> None:
        assert parse_ecad(ECAD)["footprint"] == "Package_TO_SOT_SMD:SOT-223-3_TabPin2"
        with pytest.raises(CatalogError, match="Library:Name"):
            parse_ecad({**ECAD, "symbol": "LM1117"})
        with pytest.raises(CatalogError, match="symbol, a footprint or both"):
            parse_ecad({"source_kind": "user"})


class TestCatalog:
    def test_register_and_update_keep_the_part_data(self) -> None:
        store = SqliteCatalog()
        entry = store.register(
            "component",
            {
                "name": "lm1117_3v3",
                "family": "electronic",
                "origin": "purchased",
                "mpn": "LM1117MPX-3.3/NOPB",
                "ratings": [ABS_MAX],
                "lifecycle": LIFECYCLE,
                "ecad": ECAD,
                "models": [_spice_model()],
            },
        )
        assert entry.ratings[0]["param"] == "input_voltage_v"
        assert entry.models[0]["kind"] == "spice"
        updated = store.update(
            entry.id,
            {"ratings": [ABS_MAX, RECOMMENDED], "lifecycle": {**LIFECYCLE, "status": "nrnd"}},
        )
        assert [r["kind"] for r in updated.ratings] == ["abs_max", "recommended"]
        assert updated.lifecycle["status"] == "nrnd"
        assert store.get("lm1117_3v3", "component").ecad["symbol"] == ECAD["symbol"]

    def test_an_entry_saved_before_the_fields_still_loads(self) -> None:
        store = SqliteCatalog()
        store.register(
            "component", {"name": "old_part", "family": "electronic", "origin": "purchased"}
        )
        entry = store.get("old_part", "component")
        assert entry.ratings == [] and entry.lifecycle == {} and entry.ecad == {}


class TestCheck:
    def test_a_complete_part(self) -> None:
        check = check_part_data(_complete(), today=TODAY)
        assert check["applies"] and check["complete"], check
        spice = next(i for i in check["items"] if i["key"] == "spice")
        assert spice["required"] is False and spice["status"] == "missing"

    def test_each_gap_is_named_and_never_filled(self) -> None:
        check = check_part_data(
            _complete(ratings=[], ecad={}, lifecycle={}, suppliers=[]), today=TODAY
        )
        assert check["complete"] is False
        status = {i["key"]: i["status"] for i in check["items"]}
        assert status["ratings_abs_max"] == "missing"
        assert status["ecad_footprint"] == "missing"
        assert status["lifecycle"] == "missing"
        assert status["offer"] == "missing"
        assert check["summary"].startswith("6 of 9 to do")

    def test_an_ended_part_is_bad_and_old_data_is_for_review(self) -> None:
        ended = _complete(lifecycle=parse_lifecycle({**LIFECYCLE, "status": "obsolete"}))
        item = next(
            i for i in check_part_data(ended, today=TODAY)["items"] if i["key"] == "lifecycle"
        )
        assert item["status"] == "bad"
        stale = _complete(suppliers=[{"supplier": "Mouser", "offer": {"read_on": "2026-01-01"}}])
        offer = next(i for i in check_part_data(stale, today=TODAY)["items"] if i["key"] == "offer")
        assert offer["status"] == "review"

    def test_a_module_needs_no_symbol_or_spice_and_a_bolt_is_not_checked(self) -> None:
        module = check_part_data(_complete(family="module", ecad={}), today=TODAY)
        assert module["complete"] and not any(i["key"].startswith("ecad") for i in module["items"])
        bolt = check_part_data({"family": "fastener"}, today=TODAY)
        assert bolt["applies"] is False and bolt["items"] == []


def test_a_component_read_carries_its_part_data_and_a_thread_its_gaps() -> None:
    shown = with_ownership(_complete(ratings=[]), None)
    assert shown["shared"] is True and shown["part_data"]["complete"] is False
    brief = part_data_brief(shown["part_data"])
    assert brief is not None
    assert "Absolute maximum ratings" in brief["gaps"]
    assert with_ownership({"kind": "material"}, None).get("part_data") is None


def test_adopt_keeps_the_distributors_lifecycle_but_not_unknown() -> None:
    part = SimpleNamespace(lifecycle_status="NRND", product_url="https://mouser.example/p")
    assert adopted_lifecycle(part, "Mouser", "2026-11-22") == {
        "status": "nrnd",
        "source_kind": "supplier",
        "source_ref": "https://mouser.example/p",
        "read_on": "2026-11-22",
    }
    assert adopted_lifecycle(SimpleNamespace(lifecycle_status="UNKNOWN"), "Mouser", "x") == {}
    assert adopted_lifecycle(None, "Mouser", "2026-11-22") == {}
