"""Appearances in the library: colour, material and finish entries (FORGE-631)."""

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
from tool_registry.tools.library.catalog_tools import APPEARANCE_FINISHES, catalog_tool_ids
from twin_core.library.catalog import (
    APPEARANCE_FINISHES as CATALOG_FINISHES,
)
from twin_core.library.catalog import (
    CatalogError,
    SqliteCatalog,
    summarize_appearance,
)

BLACK_ANODISED: dict[str, Any] = {
    "name": "black_anodised_6061",
    "title": "Black anodised 6061",
    "finish": "anodised",
    "colour": {
        "system": "supplier",
        "code": "AB-01",
        "name": "Jet black",
        "srgb_hex": "#1a1a1a",
        "source_kind": "supplier",
        "source_ref": "https://anodiser.example/colours#AB-01",
    },
    "texture": "bead blasted, glass bead 150-250 um",
    "properties": {
        "coating_thickness_um": {
            "value": 15,
            "unit": "um",
            "source_kind": "standard",
            "source_ref": "MIL-A-8625 Type II, Class 2",
        },
        "render_roughness": {"value": 0.45, "unit": "-", "source_kind": "user"},
        "render_metallic": {"value": 0.0, "unit": "-", "source_kind": "assumption"},
    },
    "materials": ["Aluminium_6061_T6"],
    "processes": ["anodising"],
    "suppliers": [{"supplier": "Anodiser Ltd", "sku": "AB-01"}],
}


def test_an_appearance_is_registered_with_its_sourced_colour() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("appearance", BLACK_ANODISED, by="t")
    assert entry.id.startswith("ap_")
    assert entry.finish == "anodised" and entry.family == "anodised"
    assert entry.colour["srgb_hex"] == "#1A1A1A"
    assert entry.colour["source_kind"] == "supplier" and not entry.colour["needs_review"]
    assert entry.materials == ["aluminium_6061_t6"]
    assert entry.properties["render_metallic"].needs_review
    assert entry.properties["render_metallic"].value == 0.0

    again = catalog.get("black_anodised_6061", "appearance")
    assert again.as_dict() == entry.as_dict()
    assert [e.name for e in catalog.list("appearance", family="anodised")] == [entry.name]
    assert catalog.list("appearance", family="painted") == []


def test_appearance_rules() -> None:
    catalog = SqliteCatalog()

    def refused(match: str, **changes: Any) -> None:
        with pytest.raises(CatalogError, match=match):
            catalog.register("appearance", {**BLACK_ANODISED, **changes})

    colour = BLACK_ANODISED["colour"]
    refused("finish must be one of", finish="glittery")
    refused("colour needs", colour=None)
    refused("colour.system", colour={**colour, "system": "rgb"})
    refused("needs its code", colour={**colour, "system": "ral", "code": ""})
    refused("#RRGGBB", colour={**colour, "srgb_hex": "black"})
    refused("needs a source kind", colour={**colour, "source_kind": ""})
    refused("needs its reference", colour={**colour, "source_kind": "standard", "source_ref": ""})
    refused("unknown property", properties={"colour_temp": {"value": 1, "source_kind": "user"}})
    refused(
        "between 0 and 1",
        properties={"render_roughness": {"value": 2, "source_kind": "user"}},
    )
    refused(
        "is in um",
        properties={"coating_thickness_um": {"value": 2, "unit": "mm", "source_kind": "user"}},
    )
    # A custom colour has no code, and an assumed one is flagged for review.
    custom = catalog.register(
        "appearance",
        {
            **BLACK_ANODISED,
            "name": "concept_teal",
            "finish": "painted",
            "colour": {"system": "custom", "srgb_hex": "#118080", "source_kind": "assumption"},
        },
    )
    assert custom.colour["needs_review"]
    assert "assumed, needs review: colour" in summarize_appearance(custom)


def test_an_update_replaces_the_colour_merges_properties_and_keeps_the_old_version() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("appearance", BLACK_ANODISED, by="t")
    updated = catalog.update(
        entry.id,
        {
            "finish": "powder_coated",
            "colour": {
                "system": "ral",
                "code": "RAL 9005",
                "name": "Jet black",
                "srgb_hex": "#0E0E10",
                "source_kind": "supplier",
                "source_ref": "https://coater.example/ral-9005",
            },
            "properties": {"gloss_gu_60": {"value": 30, "source_kind": "user"}},
        },
        by="t",
    )
    assert updated.version == 2 and updated.finish == "powder_coated"
    assert updated.colour["code"] == "RAL 9005"
    assert set(updated.properties) == {
        "coating_thickness_um",
        "render_roughness",
        "render_metallic",
        "gloss_gu_60",
    }
    assert [h["version"] for h in catalog.history(entry.id)] == [1, 2]
    with pytest.raises(CatalogError, match="cannot change"):
        catalog.update(entry.id, {"family": "x"})
    with pytest.raises(CatalogError, match="finish must be one of"):
        catalog.update(entry.id, {"finish": "chrome"})


def test_the_summary_names_the_colour_and_its_screen_value() -> None:
    line = summarize_appearance(SqliteCatalog().register("appearance", BLACK_ANODISED))
    assert line.startswith("Black anodised 6061 [ap_")
    assert "anodised, for aluminium_6061_t6" in line
    assert "colour AB-01 Jet black (#1A1A1A on screen)" in line
    assert "coating_thickness_um 15 um" in line
    assert "assumed, needs review: render_metallic" in line


@pytest.fixture
def client() -> Any:
    catalog_routes.init_catalog(SqliteCatalog())
    app = FastAPI()
    app.include_router(catalog_routes.router)
    yield TestClient(app)
    catalog_routes.init_catalog(None)


def test_routes(client: TestClient) -> None:
    assert client.post("/v1/library/appearances", json=BLACK_ANODISED).status_code == 201
    listed = client.get("/v1/library/appearances", params={"family": "anodised"}).json()
    assert [e["name"] for e in listed["entries"]] == ["black_anodised_6061"]
    got = client.get("/v1/library/appearances/black_anodised_6061").json()
    assert got["entry"]["colour"]["srgb_hex"] == "#1A1A1A"
    changed = client.patch(
        "/v1/library/appearances/black_anodised_6061",
        json={"changes": {"texture": "VDI 3400 Ref 27"}},
    )
    assert changed.status_code == 200
    assert client.get("/v1/library/appearances/nope").status_code == 404
    bad = client.post(
        "/v1/library/appearances", json={**BLACK_ANODISED, "name": "x2", "colour": {}}
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_local_and_remote_bindings_answer_alike(client: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        response = client.request(request.method, str(request.url.raw_path, "ascii"), json=body)
        return httpx.Response(response.status_code, json=response.json())

    local = LibraryServer(LocalMachineLibrary())
    await local._tools["library.register_appearance"].handler(dict(BLACK_ANODISED))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        remote = LibraryServer(RemoteMachineLibrary("http://gw", client=http))
        for tool, args in (
            ("library.list_appearances", {}),
            ("library.list_appearances", {"family": "anodised"}),
            ("library.get_appearance", {"ref": "black_anodised_6061"}),
        ):
            assert await local._tools[tool].handler(args) == await remote._tools[tool].handler(
                args
            ), tool


def test_annotations_tool_ids_and_schema() -> None:
    reads = {"library.list_appearances", "library.get_appearance"}
    writes = {"library.register_appearance", "library.update_appearance"}
    assert reads <= READ_ONLY and writes <= ADDITIVE
    assert reads | writes <= catalog_tool_ids()
    server = LibraryServer(LocalMachineLibrary())
    assert reads | writes <= set(server.tool_ids)
    schema = server._tools["library.register_appearance"].manifest.input_schema
    assert schema["required"] == ["name", "finish", "colour"]
    assert tuple(APPEARANCE_FINISHES) == CATALOG_FINISHES
