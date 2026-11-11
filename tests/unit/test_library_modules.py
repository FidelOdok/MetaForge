"""Electronic modules in the component library (MetaForge, 2026-11-11).

The library held mechanical parts only. A smartwatch's GNSS module, LiPo cell, MCU board
and display were researched once inside one studio and could not be reused. An entry can
now be a module, a battery or an electronic part, and carries who makes it, its order code,
its datasheet and its interfaces, so a later project finds it instead of searching again.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.library import catalog_tools
from twin_core.library.catalog import (
    COMPONENT_FAMILIES,
    COMPONENT_SPECS,
    CatalogError,
    SqliteCatalog,
)

DATASHEET = "https://content.u-blox.com/sites/default/files/MAX-M10S_DataSheet_UBX-20035208.pdf"


def gnss(**over: Any) -> dict[str, Any]:
    src = {"source_kind": "datasheet", "source_ref": DATASHEET}
    return {
        "name": "ublox_max_m10s",
        "title": "u-blox MAX-M10S GNSS module",
        "family": "module",
        "origin": "purchased",
        "manufacturer": "u-blox",
        "mpn": "MAX-M10S-00B",
        "datasheet": DATASHEET,
        "interfaces": [
            {"id": "uart", "bus": "uart", "dir": "bi"},
            {"id": "rf", "bus": "rf", "dir": "in"},
        ],
        "specs": {
            "length_mm": {"value": 10.1, "unit": "mm", **src},
            "width_mm": {"value": 9.7, "unit": "mm", **src},
            "height_mm": {"value": 2.5, "unit": "mm", **src},
        },
        **over,
    }


def test_a_module_registers_with_its_maker_code_datasheet_and_interfaces() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss()).as_dict()
    assert entry["family"] == "module"
    assert entry["manufacturer"] == "u-blox"
    assert entry["mpn"] == "MAX-M10S-00B"
    assert entry["datasheet"] == DATASHEET
    assert [i["bus"] for i in entry["interfaces"]] == ["uart", "rf"]
    again = catalog.get("ublox_max_m10s", "component").as_dict()
    assert again["interfaces"] == entry["interfaces"]
    assert again["mpn"] == "MAX-M10S-00B"


def test_battery_and_electronic_families_and_a_capacity_spec() -> None:
    assert {"module", "battery", "electronic"} <= set(COMPONENT_FAMILIES)
    assert COMPONENT_SPECS["capacity_mah"] == "mAh"
    catalog = SqliteCatalog()
    cell = catalog.register(
        "component",
        {
            "name": "adafruit_1317",
            "family": "battery",
            "origin": "purchased",
            "specs": {
                "capacity_mah": {
                    "value": 150,
                    "unit": "mAh",
                    "source_kind": "supplier",
                    "source_ref": "https://www.adafruit.com/product/1317",
                },
                "voltage_v": {
                    "value": 3.7,
                    "unit": "V",
                    "source_kind": "supplier",
                    "source_ref": "https://www.adafruit.com/product/1317",
                },
            },
        },
    ).as_dict()
    assert cell["specs"]["capacity_mah"]["value"] == 150


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"interfaces": [{"id": "uart"}]}, "bus"),
        ({"interfaces": [{"bus": "uart"}]}, "id"),
        ({"interfaces": [{"id": "a", "bus": "uart", "dir": "sideways"}]}, "dir"),
        ({"interfaces": "uart"}, "list"),
        ({"datasheet": "file:///C:/sheet.pdf"}, "http"),
        ({"family": "gizmo"}, "family"),
    ],
)
def test_a_malformed_module_is_refused(over: dict[str, Any], message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        SqliteCatalog().register("component", gnss(**over))


def test_an_entry_saved_before_these_fields_still_loads() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss()).as_dict()
    old = {
        k: v
        for k, v in entry.items()
        if k not in {"manufacturer", "mpn", "datasheet", "interfaces"}
    }
    loaded = catalog._load(old).as_dict()
    assert loaded["interfaces"] == [] and loaded["mpn"] == ""


def test_an_update_changes_the_module_fields_and_keeps_the_old_version() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss())
    changed = catalog.update(
        entry.id,
        {"interfaces": [{"id": "i2c", "bus": "i2c", "dir": "bi"}], "mpn": "MAX-M10S-01B"},
    ).as_dict()
    assert changed["mpn"] == "MAX-M10S-01B"
    assert [i["id"] for i in changed["interfaces"]] == ["i2c"]
    assert changed["version"] == 2


def test_the_tool_offers_the_same_families_and_the_new_fields() -> None:
    assert catalog_tools.COMPONENT_FAMILIES == list(COMPONENT_FAMILIES)
