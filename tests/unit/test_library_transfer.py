"""The whole library as one file and back (2026-11-12): nothing is lost or overwritten."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from twin_core.library.catalog import SqliteCatalog
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.library.transfer import export_library, import_library

SOURCED = {"source_kind": "datasheet", "source_ref": "https://maker.example/ds.pdf"}


def stocked() -> tuple[SqliteCatalog, SqliteMachineLibrary]:
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    catalog.register("material", {
        "name": "pmma_plexiglas_8n", "family": "polymer", "forms": ["pellet"],
        "properties": {"density_kg_m3": {"value": 1190, "unit": "kg/m3", **SOURCED}},
    }, by="test")  # fmt: skip
    first = catalog.register("component", {
        "name": "nordic_nrf52840_qiaa_r", "family": "electronic", "origin": "purchased",
        "manufacturer": "Nordic", "mpn": "nRF52840-QIAA-R", "notes": "first",
        "form_code": "return [g.box(7, 0.85, 7).name('body [epoxy_black]')];",
    }, by="test")  # fmt: skip
    catalog.update(first.id, {"notes": "second"}, by="test")
    model = machines.register({"layer": "model", "kind": "fdm_printer", "make": "Bambu Lab",
                               "model": "P1S"}, by="test")  # fmt: skip
    machines.register({"layer": "instance", "kind": "fdm_printer", "name": "bench p1s",
                       "model_id": model.id}, by="test")  # fmt: skip
    return catalog, machines


def test_an_export_loads_into_an_empty_library_whole() -> None:
    doc = export_library(*stocked())
    assert doc["counts"]["component"] == 1 and doc["counts"]["machine"] == 2
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    report = import_library(doc, catalog, machines, by="import by test")
    assert report["added"] == {"material": 1, "component": 1, "machine": 2}
    assert not report["failed"]
    part = catalog.get("nordic_nrf52840_qiaa_r", "component").as_dict()
    assert part["notes"] == "second" and "epoxy_black" in part["form_code"]  # the current version
    pmma = catalog.get("pmma_plexiglas_8n", "material").as_dict()
    assert pmma["properties"]["density_kg_m3"]["value"] == 1190


def test_a_machine_instance_points_at_its_model_in_the_new_library() -> None:
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    import_library(export_library(*stocked()), catalog, machines, by="import")
    model = machines.list(layer="model")[0]
    (bench,) = machines.list(layer="instance")
    assert bench.as_dict()["model_id"] == model.id


def test_a_second_import_adds_nothing_and_overwrites_nothing() -> None:
    doc = export_library(*stocked())
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    import_library(doc, catalog, machines, by="import")
    part = catalog.get("nordic_nrf52840_qiaa_r", "component")
    catalog.update(part.id, {"notes": "edited here"}, by="person")
    again = import_library(doc, catalog, machines, by="import")
    assert again["added"] == {}
    assert again["skipped"] == {"material": 1, "component": 1, "machine": 2}
    assert catalog.get("nordic_nrf52840_qiaa_r", "component").as_dict()["notes"] == "edited here"


def test_history_is_exported_only_when_asked() -> None:
    lib = stocked()
    assert "history" not in export_library(*lib)["entries"]["component"][0]
    versions = export_library(*lib, history=True)["entries"]["component"][0]["history"]
    assert len(versions) == 2


@pytest.mark.parametrize(
    "doc", [{}, {"format": "something else"}, {"format": "metaforge-library", "version": 9}]
)
def test_a_file_that_is_not_a_library_export_is_refused(doc: dict) -> None:
    with pytest.raises(ValueError):
        import_library(doc, SqliteCatalog(), SqliteMachineLibrary(), by="import")


def test_the_routes_export_import_and_serve_the_drawing_engine() -> None:
    from api_gateway.library import catalog_routes, routes

    catalog, machines = stocked()
    catalog_routes.init_catalog(catalog)
    routes.init_machine_library(machines)
    app = FastAPI()
    app.include_router(catalog_routes.router)
    client = TestClient(app)
    try:
        doc = client.get("/v1/library/export").json()
        assert doc["counts"]["component"] == 1
        catalog_routes.init_catalog(SqliteCatalog())
        routes.init_machine_library(SqliteMachineLibrary())
        added = client.post("/v1/library/import", json=doc).json()["added"]
        assert added == {"material": 1, "component": 1, "machine": 2}
        assert client.post("/v1/library/import", json={"format": "x"}).status_code == 422
        engine = client.get("/v1/library/engine/authored.js")
        assert engine.status_code == 200 and "FGAuthored" in engine.text
        assert client.get("/v1/library/engine/secrets.txt").status_code == 404
    finally:
        catalog_routes.init_catalog(None)
        routes.init_machine_library(None)
