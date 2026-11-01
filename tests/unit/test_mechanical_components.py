"""Mechanical components in the library, standard parts from their tables (FORGE-449)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.library import catalog_routes
from api_gateway.library.mcp_bindings import LocalMachineLibrary
from mcp_core.annotations import ADDITIVE, READ_ONLY
from metaforge.mcp.remote_library import RemoteMachineLibrary
from tool_registry.tools.library.adapter import LibraryServer
from tool_registry.tools.library.catalog_tools import catalog_tool_ids
from twin_core.library.catalog import CatalogError, SqliteCatalog, summarize_component
from twin_core.library.standard_parts import (
    StandardPartError,
    build_standard_part,
    deep_groove_bearing,
    iso4032,
    iso4762,
    iso7089,
    seed_entries,
)


def test_an_iso4762_screw_comes_from_the_table_row() -> None:
    screw = iso4762("M3", 10)
    specs = screw["specs"]
    assert screw["name"] == "iso4762_m3x10"
    assert screw["standard"] == "ISO 4762"
    assert specs["head_diameter_mm"]["value"] == 5.68
    assert specs["socket_size_mm"]["value"] == 2.5
    assert specs["thread_pitch_mm"]["value"] == 0.5  # coarse pitch by default
    assert "socket_head_cap_parameters.csv row M3-0.5" in specs["head_diameter_mm"]["source_ref"]
    assert screw["geometry"]["generator"] == "socket_head_cap_screw"


def test_lengths_and_sizes_outside_the_standard_are_refused() -> None:
    with pytest.raises(StandardPartError, match="lengths run"):
        iso4762("M3", 200)
    with pytest.raises(StandardPartError, match="no M77"):
        iso4032("M77")
    with pytest.raises(StandardPartError, match="metric size"):
        iso7089("3/8")
    with pytest.raises(StandardPartError, match="needs length_mm"):
        build_standard_part("iso4762", "M3")


def test_a_608_bearing_carries_its_catalogue_ratings_with_their_source() -> None:
    bearing = deep_groove_bearing("608")
    specs = bearing["specs"]
    assert (specs["bore_mm"]["value"], specs["outer_diameter_mm"]["value"]) == (8, 22)
    assert specs["width_mm"]["value"] == 7
    assert specs["dynamic_load_kn"]["value"] == 3.45
    assert specs["static_load_kn"]["source_kind"] == "manufacturer_spec"
    assert "catalogue is not named" in bearing["notes"]
    # A marked catalogue designation is matched without its marker.
    assert deep_groove_bearing("* 608")["name"] == "bearing_608"


def test_the_seed_registers_and_meets_the_count() -> None:
    catalog = SqliteCatalog()
    entries = seed_entries()
    for entry in entries:
        catalog.register("component", entry)
    assert len(catalog.list("component")) == len(entries) >= 50
    families = {e.family for e in catalog.list("component")}
    assert families == {"screw", "nut", "washer", "bearing"}
    line = summarize_component(catalog.get("iso4032_m3", "component"))
    assert "ISO 4032 M3" in line and "width_across_flats_mm 5.5 mm" in line


def test_component_rules() -> None:
    catalog = SqliteCatalog()
    base = iso7089("M6")
    with pytest.raises(CatalogError, match="names its standard"):
        catalog.register("component", {**base, "standard": ""})
    with pytest.raises(CatalogError, match="unknown spec"):
        catalog.register("component", {**base, "specs": {"colour": {"value": 1}}})
    with pytest.raises(CatalogError, match="is in mm"):
        catalog.register(
            "component",
            {**base, "specs": {"thickness_mm": {"value": 1, "unit": "in", "source_kind": "user"}}},
        )
    with pytest.raises(CatalogError, match="needs a generator"):
        catalog.register("component", {**base, "geometry": {"params": {}}})
    entry = catalog.register("component", base)
    updated = catalog.update(
        entry.id, {"suppliers": [{"supplier": "Mouser", "sku": "123-ABC"}]}, by="t"
    )
    assert updated.version == 2 and updated.suppliers[0]["sku"] == "123-ABC"


@pytest.fixture
def client() -> Any:
    catalog_routes.init_catalog(SqliteCatalog())
    app = FastAPI()
    app.include_router(catalog_routes.router)
    yield TestClient(app)
    catalog_routes.init_catalog(None)


def test_routes(client: TestClient) -> None:
    built = client.get(
        "/v1/library/components/standard",
        params={"family": "iso4762", "size": "M4", "length_mm": 12},
    )
    assert built.status_code == 200
    entry = built.json()["entry"]
    assert client.post("/v1/library/components", json=entry).status_code == 201
    listed = client.get("/v1/library/components", params={"family": "screw"}).json()
    assert [e["name"] for e in listed["entries"]] == ["iso4762_m4x12"]
    assert client.get("/v1/library/components/iso4762_m4x12").status_code == 200
    bad = client.get("/v1/library/components/standard", params={"family": "iso4762", "size": "M4"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_local_and_remote_bindings_answer_alike(client: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        response = client.request(request.method, str(request.url.raw_path, "ascii"), json=body)
        return httpx.Response(response.status_code, json=response.json())

    local = LibraryServer(LocalMachineLibrary())
    built = await local._tools["library.standard_part"].handler(
        {"family": "deep_groove_bearing", "size": "608"}
    )
    await local._tools["library.register_component"].handler(built["entry"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        remote = LibraryServer(RemoteMachineLibrary("http://gw", client=http))
        for tool, args in (
            ("library.list_components", {}),
            ("library.get_component", {"ref": "bearing_608"}),
            ("library.standard_part", {"family": "iso4032", "size": "M5"}),
        ):
            assert await local._tools[tool].handler(args) == await remote._tools[tool].handler(
                args
            ), tool


def test_annotations_and_tool_ids() -> None:
    reads = {"library.list_components", "library.get_component", "library.standard_part"}
    writes = {"library.register_component", "library.update_component"}
    assert reads <= READ_ONLY and writes <= ADDITIVE
    assert reads | writes <= catalog_tool_ids()
    assert reads | writes <= set(LibraryServer(LocalMachineLibrary()).tool_ids)
