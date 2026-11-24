"""component.find_similar: similar parts whose sizes have a source (FORGE-927, R5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.test_component_finder import FakeDistributor, LocalLibrary, _part
from tool_registry.tools.components.finder import ComponentFinderServer
from tool_registry.tools.components.similar import distributor_size, sourced_size
from twin_core.library.catalog import SqliteCatalog

SHEET = "https://cdn-learn.adafruit.com/downloads/pdf/adafruit-feather.pdf"


def _sized(value: float, kind: str = "datasheet") -> dict[str, Any]:
    if kind == "estimate":
        return {
            "value": value,
            "unit": "mm",
            "source_kind": "estimate",
            "method": "photo",
            "source_ref": "image:" + "a" * 64,
            "confidence": 0.6,
        }
    return {"value": value, "unit": "mm", "source_kind": kind, "source_ref": SHEET}


class Library(LocalLibrary):
    async def get_entry(self, kind: str, ref: str) -> dict[str, Any]:
        return {"entry": self.catalog.get(ref, kind).as_dict(), "history": []}


def _library() -> Library:
    catalog = SqliteCatalog()
    catalog.register(
        "component",
        {
            "name": "adafruit-feather-m4",
            "title": "Adafruit Feather M4 Express",
            "family": "module",
            "origin": "purchased",
            "specs": {"length_mm": _sized(50.8), "width_mm": _sized(22.8)},
        },
        owner=None,
    )
    catalog.register(
        "component",
        {
            "name": "adafruit-feather-guess",
            "title": "Adafruit Feather RP2040",
            "family": "module",
            "origin": "purchased",
            # Guessed sizes (an estimate once #1488 lands): not a source, so left out.
            "specs": {"length_mm": _sized(50, "assumption"), "width_mm": _sized(23, "assumption")},
        },
        owner=None,
    )
    return Library(catalog)


class SizedDistributor(FakeDistributor):
    def __init__(self, name: str, parts: list[Any], specs: dict[str, dict[str, Any]]) -> None:
        super().__init__(name, parts)
        self._specs = specs

    async def get_part_details(self, mpn: str) -> Any:
        specs = self._specs.get(mpn)
        return (
            None
            if specs is None
            else SimpleNamespace(specs=specs, product_url=f"https://d.example/{mpn}")
        )


def test_only_real_sources_count() -> None:
    assert sourced_size({"length_mm": _sized(50.8), "width_mm": _sized(22.8)}) is not None
    assert sourced_size({"length_mm": _sized(50.8), "width_mm": _sized(22.8, "estimate")}) is None
    assert sourced_size({"length_mm": _sized(50.8, "assumption")}) is None


def test_a_distributor_size_is_read_from_its_parameters() -> None:
    inch_and_mm = {"Size / Dimension": '1.000" L x 0.700" W (25.40mm x 17.78mm)'}
    assert distributor_size(inch_and_mm) == {"length_mm": 25.4, "width_mm": 17.78}
    assert distributor_size({"Size / Dimension": '2.00" x 0.90"'}) == {
        "length_mm": 50.8,
        "width_mm": 22.86,
    }
    assert distributor_size({"Packaging": "Tray"}) is None


async def test_library_parts_with_sourced_sizes_come_first_with_why() -> None:
    server = ComponentFinderServer(_library(), [])
    out = await server.handle_find_similar({"form_factor": "Feather", "family": "module"})
    (only,) = out["candidates"]  # the guessed-size Feather is left out
    assert only["ref"] == "adafruit-feather-m4" and only["source"] == "library"
    assert (only["length_mm"], only["width_mm"]) == (50.8, 22.8)
    assert "same form factor (Feather)" in only["why"] and "same family (module)" in only["why"]
    assert only["sources"]["length"].startswith("datasheet: ")
    assert only["estimated_from_similar"] == "library:adafruit-feather-m4"


async def test_distributors_count_only_when_they_state_a_size() -> None:
    parts = [
        _part("4480", "LSM6DS33 breakout STEMMA QT"),
        _part("4485", "LSM6DS33 breakout STEMMA QT rev B"),
    ]
    sized = SizedDistributor(
        "DigiKey",
        parts,
        {
            "4480": {"Size / Dimension": '1.000" L x 0.700" W (25.40mm x 17.78mm)'},
            "4485": {"Packaging": "Bulk"},
        },
    )
    server = ComponentFinderServer(Library(SqliteCatalog()), [sized])
    out = await server.handle_find_similar({"series": "STEMMA QT", "need": "LSM6DS33"})
    (only,) = out["candidates"]
    assert only["source"] == "distributor" and only["ref"] == "DigiKey:4480"
    assert (only["length_mm"], only["width_mm"]) == (25.4, 17.78)
    assert only["sources"]["length"].startswith("supplier: DigiKey parameters")
    assert out["searched"]["distributors"] == {"DigiKey": 2}


async def test_something_to_search_by_is_needed() -> None:
    with pytest.raises(ValueError, match="give at least one"):
        await ComponentFinderServer(_library(), []).handle_find_similar({})
