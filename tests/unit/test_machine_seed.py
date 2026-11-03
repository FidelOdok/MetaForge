"""The machine models seed and the vendor directory (FORGE-753).

The library shipped nearly empty: the sandbox held one printer, no laser
cutter and no vendor, so a laser-cut plywood route had nothing real to
compare a design with. Every seeded value must pass the library's own checks
and cite the official page it came from.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.vendors.handoffs import HANDOFFS
from twin_core.library import machine_seed
from twin_core.library.machines import CAPABILITIES, MACHINE_KINDS, SqliteMachineLibrary


def _models() -> list[dict[str, Any]]:
    return [m for doc in machine_seed.seed_documents() for m in doc["models"]]


def test_there_is_a_seed_for_each_category() -> None:
    docs = machine_seed.seed_documents()
    assert docs, "no seed files"
    for doc in docs:
        assert doc.get("checked") and doc.get("category") and doc.get("models")
    kinds = {m["kind"] for m in _models()}
    assert "laser_cutter" in kinds


def test_every_value_is_sourced_to_an_official_page() -> None:
    for model in _models():
        assert model["kind"] in MACHINE_KINDS
        for key, cap in model["capabilities"].items():
            assert key in CAPABILITIES, f"{model['model']}: unknown capability {key}"
            assert str(cap.get("url", "")).startswith("https://"), f"{model['model']} {key}"


def test_every_seed_model_passes_the_librarys_own_checks() -> None:
    library = SqliteMachineLibrary()
    out = machine_seed.seed_machine_library(library)
    assert out["failed"] == []
    assert len(out["added"]) == len(_models())
    for model in library.list(layer="model"):
        for cap in model.capabilities.values():
            assert cap.source_kind == "manufacturer_spec" and cap.source_ref


def test_loading_again_adds_nothing_and_keeps_what_a_person_entered() -> None:
    library = SqliteMachineLibrary()
    first = _models()[0]
    library.register(
        {"layer": "model", "kind": first["kind"], "make": first["make"], "model": first["model"]},
        by="user:fidel",
    )
    out = machine_seed.seed_machine_library(library)
    assert f"{first['make']} {first['model']}" in out["skipped"]
    again = machine_seed.seed_machine_library(library)
    assert again["added"] == []
    mine = [m for m in library.list(layer="model") if m.model == first["model"]]
    assert len(mine) == 1 and mine[0].capabilities == {}


def test_the_seed_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(machine_seed.SEED_ENV, "off")
    library = SqliteMachineLibrary()
    assert machine_seed.seed_machine_library(library)["added"] == []
    assert library.list(layer="model") == []


def test_the_vendor_directory_says_where_each_vendor_is_and_what_it_takes() -> None:
    ids = [v.id for v in HANDOFFS]
    assert len(ids) == len(set(ids))
    for vendor in HANDOFFS:
        assert vendor.url.startswith("https://")
        assert vendor.based_in and vendor.services and vendor.formats
        assert all(f == f.lower() and "." not in f for f in vendor.formats)
    laser = [v for v in HANDOFFS if "laser_cutting" in v.services]
    places = " ".join(v.based_in for v in laser)
    assert "UK" in places and "US" in places
