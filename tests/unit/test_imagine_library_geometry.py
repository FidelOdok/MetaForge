"""A linked part's size comes from its component library entry (FORGE-877).

Live evidence (night-light project, Imagine studio revision 2, 2026-11-15): four of five
parts were linked to library entries, yet every one was drawn from a size the assistant
typed in, and the handover's ``parts`` item failed for parts whose library entries hold
``length_mm``/``width_mm``/``height_mm``. Price already came from the library; size did not.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine import library_geometry
from api_gateway.imagine.part_model import size_is_read


def _spec(value: float, ref: str = "https://www.adafruit.com/product/1643") -> dict[str, Any]:
    return {"value": value, "unit": "mm", "source_kind": "manufacturer_spec", "source_ref": ref}


RING = {
    "name": "adafruit_1643_neopixel_ring_12",
    "version": 3,
    "datasheet": "https://www.adafruit.com/product/1643",
    "specs": {"length_mm": _spec(36.8), "width_mm": _spec(36.8), "height_mm": _spec(6.7)},
}
XIAO = {  # footprint read, height not: the library holds no height_mm
    "name": "seeed_xiao_esp32s3",
    "version": 1,
    "specs": {
        "length_mm": _spec(21.0, "https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/"),
        "width_mm": _spec(17.8, "https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/"),
    },
}
LIBRARY = {e["name"]: e for e in (RING, XIAO)}


def _lookup(name: str) -> dict[str, Any] | None:
    return LIBRARY.get(name)


def _config(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"context": {"components": {"parts": list(parts)}}}


def _part(out: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in out["context"]["components"]["parts"] if p["id"] == pid)


def test_a_linked_part_with_no_size_takes_the_library_size() -> None:
    out, notes = library_geometry.derive_part_geometry(
        _config({"id": "ring-a", "library": RING["name"]}), _lookup
    )
    geo = _part(out, "ring-a")["geometry"]
    assert geo["envelope"] == [36.8, 36.8, 6.7]
    assert geo["derived_from"] == "library"
    assert geo["size_basis"] == "read"
    assert "adafruit_1643_neopixel_ring_12 v3" in geo["source"]
    assert size_is_read(geo)  # the handover's parts check counts it
    assert any("ring-a" in n for n in notes)


def test_a_hand_typed_approximation_is_replaced_and_the_note_names_both() -> None:
    typed = {"envelope": [36.8, 6.7, 36.8], "fidelity": "approximation", "size_basis": "read"}
    out, notes = library_geometry.derive_part_geometry(
        _config({"id": "ring-a", "library": RING["name"], "geometry": typed}), _lookup
    )
    assert _part(out, "ring-a")["geometry"]["envelope"] == [36.8, 36.8, 6.7]
    assert any("36.8 x 6.7 x 36.8" in n and "36.8 x 36.8 x 6.7" in n for n in notes)


def test_an_exact_model_on_the_part_is_kept() -> None:
    exact = {"envelope": [36.83, 36.83, 1.57], "fidelity": "exact", "source": "measured STEP"}
    out, _ = library_geometry.derive_part_geometry(
        _config({"id": "ring-a", "library": RING["name"], "geometry": exact}), _lookup
    )
    assert _part(out, "ring-a")["geometry"] == exact


def test_an_incomplete_library_size_is_not_used_and_says_what_is_missing() -> None:
    # negative control: a footprint without a height is not a size; the next rung draws it
    out, notes = library_geometry.derive_part_geometry(
        _config({"id": "board-a", "library": XIAO["name"]}), _lookup
    )
    assert "geometry" not in _part(out, "board-a")
    assert any("seeed_xiao_esp32s3" in n and "height_mm" in n for n in notes)


def test_a_library_derived_size_is_dropped_when_the_entry_loses_it() -> None:
    stale = {"envelope": [21, 17.8, 3], "derived_from": "library", "size_basis": "read"}
    out, _ = library_geometry.derive_part_geometry(
        _config({"id": "board-a", "library": XIAO["name"], "geometry": stale}), _lookup
    )
    assert "geometry" not in _part(out, "board-a")


def test_unlinked_stand_in_and_excluded_parts_are_untouched() -> None:
    parts = (
        {"id": "touch-a", "geometry": {"envelope": [25, 25, 0.1]}},
        {"id": "x", "library": RING["name"], "standIn": True},
        {"id": "y", "library": RING["name"], "excluded": True},
        {"id": "z", "library": "not_in_library"},
    )
    out, notes = library_geometry.derive_part_geometry(_config(*parts), _lookup)
    assert out == _config(*parts)
    assert notes == []


def test_the_input_config_is_not_changed() -> None:
    config = _config({"id": "ring-a", "library": RING["name"]})
    library_geometry.derive_part_geometry(config, _lookup)
    assert "geometry" not in config["context"]["components"]["parts"][0]


async def test_a_studio_write_sizes_a_linked_part_from_the_real_catalog() -> None:
    from api_gateway.imagine.service import ImagineService, _part_gaps
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
                "name": "ring_12",
                "family": "module",
                "origin": "purchased",
                "datasheet": "https://www.adafruit.com/product/1643",
                "specs": {
                    "length_mm": _spec(36.8),
                    "width_mm": _spec(36.8),
                    "height_mm": _spec(6.7),
                },
            },
        )
        cfg = full_config()
        part = cfg["context"]["components"]["parts"][0]
        part["library"] = "ring_12"
        part.pop("geometry", None)
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        written = out.config["context"]["components"]["parts"][0]
        assert written["geometry"]["envelope"] == [36.8, 36.8, 6.7]
        assert written["geometry"]["derived_from"] == "library"
        assert "3D envelope" not in _part_gaps(written)
    finally:
        catalog_routes.init_catalog(None)
        ops.init_operations(None)


def test_an_entry_with_no_size_says_nothing_when_the_part_is_already_sized() -> None:
    # nothing the library could add: no note (a note on every write would be noise)
    entry = {"name": "bare", "version": 1, "specs": {}}
    sized = {"envelope": [10, 10, 2], "fidelity": "approximation", "size_basis": "read"}
    out, notes = library_geometry.derive_part_geometry(
        _config({"id": "p", "library": "bare", "geometry": sized}),
        lambda name: entry if name == "bare" else None,
    )
    assert _part(out, "p")["geometry"] == sized
    assert notes == []
