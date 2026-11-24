"""Estimates and geometry fidelity in the component library (FORGE-929, R7)."""

from __future__ import annotations

from typing import Any

import pytest

from twin_core.library.catalog import CatalogError, SqliteCatalog, parse_geometry, parse_specs

IMAGE = "image:" + "a" * 64


def _estimate(value: float, **extra: Any) -> dict[str, Any]:
    return {
        "value": value,
        "unit": "mm",
        "source_kind": "estimate",
        "method": "photo",
        "source_ref": IMAGE,
        "confidence": 0.7,
        **extra,
    }


def _sourced(value: float) -> dict[str, Any]:
    return {
        "value": value,
        "unit": "mm",
        "source_kind": "datasheet",
        "source_ref": "https://cdn-learn.adafruit.com/downloads/pdf/lsm6ds33.pdf",
    }


def _register(catalog: SqliteCatalog, **extra: Any) -> Any:
    return catalog.register(
        "component",
        {
            "name": "adafruit-4480",
            "title": "Adafruit LSM6DS33 breakout",
            "family": "module",
            "origin": "purchased",
            "specs": {"length_mm": _estimate(25.28), "width_mm": _estimate(17.8)},
            **extra,
        },
        owner=None,
    )


def test_an_estimate_is_kept_as_an_estimate_with_its_method_and_confidence() -> None:
    spec = parse_specs({"length_mm": _estimate(25.28)})["length_mm"]
    assert spec.source_kind == "estimate" and spec.method == "photo"
    assert spec.confidence == 0.7 and spec.source_ref == IMAGE and spec.needs_review


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"method": "guess"}, "needs a method"),
        ({"source_ref": ""}, "needs its basis"),
        ({"confidence": 1.4}, "confidence between 0 and 1"),
        ({"confidence": None}, "confidence between 0 and 1"),
    ],
)
def test_an_estimate_names_how_what_from_and_how_sure(change: dict[str, Any], why: str) -> None:
    with pytest.raises(CatalogError, match=why):
        parse_specs({"length_mm": _estimate(25.28, **change)})


def test_every_scale_resolve_method_is_an_estimate_method() -> None:
    for method in ("printed", "package_nominal", "similar_part", "photo"):
        assert (
            parse_specs({"length_mm": _estimate(25.4, method=method)})["length_mm"].method == method
        )


def test_an_estimate_survives_a_save_and_load() -> None:
    catalog = SqliteCatalog()
    entry = _register(catalog)
    again = catalog.get(entry.id)
    assert again.specs["length_mm"].method == "photo"
    assert again.as_dict()["specs"]["width_mm"]["confidence"] == 0.7


def test_a_sourced_value_replaces_an_estimate_but_not_the_other_way() -> None:
    catalog = SqliteCatalog()
    entry = _register(catalog)
    better = catalog.update(entry.id, {"specs": {"length_mm": _sourced(25.4)}})
    assert better.specs["length_mm"].source_kind == "datasheet"
    with pytest.raises(CatalogError, match="never replaces a sourced value"):
        catalog.update(entry.id, {"specs": {"length_mm": _estimate(25.2)}})
    assert catalog.get(entry.id).specs["length_mm"].value == 25.4
    # A new estimate may still refine an old one.
    refined = catalog.update(entry.id, {"specs": {"width_mm": _estimate(17.78, confidence=0.9)}})
    assert refined.specs["width_mm"].value == 17.78


def test_geometry_carries_a_fidelity_and_never_gets_worse() -> None:
    assert (
        parse_geometry({"generator": "box", "params": {}, "fidelity": "outline"})["fidelity"]
        == "outline"
    )
    with pytest.raises(CatalogError, match="fidelity is one of"):
        parse_geometry({"generator": "box", "params": {}, "fidelity": "great"})
    catalog = SqliteCatalog()
    entry = _register(
        catalog, geometry={"generator": "box", "params": {}, "fidelity": "photo_estimate"}
    )
    cad = {"step_url": "https://example.com/4480.step", "fidelity": "maker_cad"}
    assert catalog.update(entry.id, {"geometry": cad}).geometry["fidelity"] == "maker_cad"
    with pytest.raises(CatalogError, match="less faithful"):
        catalog.update(
            entry.id, {"geometry": {"generator": "box", "params": {}, "fidelity": "outline"}}
        )


@pytest.mark.parametrize("colour", ["black", "Matte Black", "silver"])
def test_a_colour_is_never_a_material(colour: str) -> None:
    with pytest.raises(CatalogError, match="is a colour, not a material"):
        _register(SqliteCatalog(), material=colour)
