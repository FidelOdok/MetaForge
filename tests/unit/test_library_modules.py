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


# A module's body height has one name (2026-11-12). The first backfill recorded three of
# four bodies as thickness_mm and one as height_mm, so a step that fits parts from the
# library would miss three heights.


def _mm(value: float) -> dict[str, Any]:
    return {"value": value, "unit": "mm", "source_kind": "datasheet", "source_ref": DATASHEET}


@pytest.mark.parametrize("family", ["module", "battery", "electronic"])
def test_a_body_height_given_as_thickness_is_refused_naming_height(family: str) -> None:
    specs = {"length_mm": _mm(10.1), "width_mm": _mm(9.7), "thickness_mm": _mm(2.5)}
    with pytest.raises(CatalogError, match="height_mm"):
        SqliteCatalog().register("component", gnss(family=family, specs=specs))


def test_a_washer_still_takes_thickness() -> None:
    entry = SqliteCatalog().register(
        "component",
        {
            "name": "washer_m3",
            "family": "washer",
            "origin": "standard",
            "standard": "ISO 7089",
            "specs": {"thickness_mm": _mm(0.5)},
        },
    )
    assert entry.as_dict()["specs"]["thickness_mm"]["value"] == 0.5


def test_an_update_cannot_add_thickness_to_a_module() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss())
    with pytest.raises(CatalogError, match="height_mm"):
        catalog.update(entry.id, {"specs": {"thickness_mm": _mm(3.0)}})


def test_an_entry_saved_with_thickness_moves_to_height_when_height_is_given() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss())
    legacy = entry.as_dict()
    specs = legacy["specs"]
    legacy["specs"] = {"length_mm": specs["length_mm"], "thickness_mm": specs["height_mm"]}
    legacy["version"] = entry.version + 1
    old = catalog._load(legacy)
    assert "thickness_mm" in old.as_dict()["specs"]
    catalog._save(old, "test", new=False)
    fixed = catalog.update(entry.id, {"specs": {"height_mm": _mm(2.5)}}).as_dict()
    assert fixed["specs"]["height_mm"]["value"] == 2.5
    assert "thickness_mm" not in fixed["specs"]


# A library part carries the code that draws it once (2026-11-12): the body of a function
# of g, in mm, centred on the part, x its length, y its height, z its width. Any studio
# places it with g.model(name), so the part is drawn once and reused.

XIAO_CODE = "return [g.box(21, 3, 17.8).name('pcb'), g.box(9, 3.2, 7.4).at(-8, 3, 0).name('usb')];"


def test_a_module_carries_its_form_code_and_an_update_versions_it() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss(form_code=XIAO_CODE)).as_dict()
    assert entry["form_code"] == XIAO_CODE
    changed = catalog.update(entry["id"], {"form_code": "return [g.box(1, 1, 1)];"}).as_dict()
    assert changed["form_code"] == "return [g.box(1, 1, 1)];"
    assert changed["version"] == 2


def test_an_entry_saved_before_form_code_loads_with_none() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("component", gnss()).as_dict()
    old = {k: v for k, v in entry.items() if k != "form_code"}
    assert catalog._load(old).as_dict()["form_code"] == ""


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("return [fetch('x')];", "fetch"),
        ("return [g.box(1,1,1)]; // `x`", "template"),
        ("x" * 6_001, "6000"),
        (42, "text"),
    ],
)
def test_form_code_the_studio_would_refuse_is_refused_here(code: Any, message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        SqliteCatalog().register("component", gnss(form_code=code))


def test_the_studio_and_the_library_refuse_the_same_words() -> None:
    from api_gateway.imagine.formgraph import authored
    from twin_core.library import form_code

    assert authored.BANNED_TOKENS is form_code.BANNED_TOKENS
