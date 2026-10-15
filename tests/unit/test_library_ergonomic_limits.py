"""Ergonomic limits in the library (FORGE-635)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.library import catalog_routes
from api_gateway.library.mcp_bindings import LocalMachineLibrary
from domain_agents.industrial_design.skills.check_ergonomics.schema import UNITS, ErgoLimit
from mcp_core.annotations import ADDITIVE, READ_ONLY
from mcp_core.profiles import PROFILES
from metaforge.mcp.remote_library import RemoteMachineLibrary
from tool_registry.tools.library.adapter import LibraryServer
from tool_registry.tools.library.catalog_tools import ERGONOMIC_KINDS as TOOL_KINDS
from tool_registry.tools.library.catalog_tools import catalog_tool_ids
from twin_core.library.catalog import (
    ERGONOMIC_KINDS,
    CatalogError,
    SqliteCatalog,
    summarize_ergonomic_limit,
)

BUTTON: dict[str, Any] = {
    "name": "pushbutton_finger_diameter",
    "title": "Push button diameter, fingertip",
    "limit_kind": "button_size",
    "applies_to": "finger-operated push button",
    "min": 10,
    "max": 25,
    "population": "US military personnel, 5th to 95th percentile",
    "source_kind": "standard",
    "source_ref": "MIL-STD-1472H, 5.1.x, Table X",
}


def test_a_limit_is_registered_sourced_and_filtered_by_kind() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("ergonomic_limit", BUTTON, by="t")
    assert entry.id.startswith("el_") and entry.unit == "mm" and not entry.needs_review
    assert entry.family == "button_size"
    assert [e.name for e in catalog.list("ergonomic_limit", family="button_size")] == [entry.name]
    assert catalog.list("ergonomic_limit", family="grip_diameter") == []
    line = summarize_ergonomic_limit(entry)
    assert "button_size >= 10 mm and <= 25 mm (finger-operated push button)" in line
    assert "source: MIL-STD-1472H" in line


def test_limit_rules() -> None:
    catalog = SqliteCatalog()

    def refused(match: str, **changes: Any) -> None:
        with pytest.raises(CatalogError, match=match):
            catalog.register("ergonomic_limit", {**BUTTON, **changes})

    refused("limit_kind must be one of", limit_kind="comfort")
    refused("is in mm", unit="in")
    refused("give min, max or both", min=None, max=None)
    refused("min is larger than max", min=30)
    refused("population is required", population=" ")
    refused("needs its reference", source_ref="")
    refused("needs a source kind", source_kind="")
    assumed = catalog.register(
        "ergonomic_limit", {**BUTTON, "name": "guess", "source_kind": "assumption"}
    )
    assert assumed.needs_review


def test_an_update_revalidates_and_keeps_the_old_version() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("ergonomic_limit", BUTTON, by="t")
    updated = catalog.update(entry.id, {"max": 19, "applies_to": "gloved finger"}, by="t")
    assert (updated.version, updated.max, updated.min) == (2, 19, 10)
    with pytest.raises(CatalogError, match="min is larger than max"):
        catalog.update(entry.id, {"max": 5})
    with pytest.raises(CatalogError, match="cannot change"):
        catalog.update(entry.id, {"limit_kind": "grip_width"})
    assert [h["version"] for h in catalog.history(entry.id)] == [1, 2]


def test_check_ergonomics_takes_a_library_entry_as_it_is() -> None:
    entry = SqliteCatalog().register("ergonomic_limit", BUTTON).as_dict()
    limit = ErgoLimit.model_validate(entry)
    assert (limit.kind, limit.min, limit.max) == ("button_size", 10, 25)
    assert limit.source_ref == "MIL-STD-1472H, 5.1.x, Table X [pushbutton_finger_diameter v1]"


def test_the_kinds_and_units_agree_everywhere() -> None:
    assert ERGONOMIC_KINDS == UNITS
    assert list(ERGONOMIC_KINDS) == TOOL_KINDS


@pytest.fixture
def client() -> Any:
    catalog_routes.init_catalog(SqliteCatalog())
    app = FastAPI()
    app.include_router(catalog_routes.router)
    yield TestClient(app)
    catalog_routes.init_catalog(None)


def test_routes(client: TestClient) -> None:
    assert client.post("/v1/library/ergonomic-limits", json=BUTTON).status_code == 201
    listed = client.get("/v1/library/ergonomic-limits", params={"family": "button_size"}).json()
    assert [e["name"] for e in listed["entries"]] == ["pushbutton_finger_diameter"]
    assert client.get("/v1/library/ergonomic-limits/pushbutton_finger_diameter").status_code == 200
    changed = client.patch(
        "/v1/library/ergonomic-limits/pushbutton_finger_diameter", json={"changes": {"max": 20}}
    )
    assert changed.status_code == 200
    bad = client.post("/v1/library/ergonomic-limits", json={**BUTTON, "name": "x2", "min": 99})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_local_and_remote_bindings_answer_alike(client: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        response = client.request(request.method, str(request.url.raw_path, "ascii"), json=body)
        return httpx.Response(response.status_code, json=response.json())

    local = LibraryServer(LocalMachineLibrary())
    await local._tools["library.register_ergonomic_limit"].handler(dict(BUTTON))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        remote = LibraryServer(RemoteMachineLibrary("http://gw", client=http))
        for tool, args in (
            ("library.list_ergonomic_limits", {}),
            ("library.list_ergonomic_limits", {"family": "button_size"}),
            ("library.get_ergonomic_limit", {"ref": "pushbutton_finger_diameter"}),
        ):
            assert await local._tools[tool].handler(args) == await remote._tools[tool].handler(
                args
            ), tool


def test_annotations_tool_ids_and_profile() -> None:
    reads = {"library.list_ergonomic_limits", "library.get_ergonomic_limit"}
    writes = {"library.register_ergonomic_limit", "library.update_ergonomic_limit"}
    assert reads <= READ_ONLY and writes <= ADDITIVE
    assert reads | writes <= catalog_tool_ids()
    server = LibraryServer(LocalMachineLibrary())
    assert reads | writes <= set(server.tool_ids)
    assert reads <= PROFILES["industrial_design"]
    schema = server._tools["library.register_ergonomic_limit"].manifest.input_schema
    assert schema["required"] == ["name", "limit_kind", "population", "source_kind"]


def test_the_mil_std_1472h_seed_registers_and_cites_every_figure() -> None:
    from twin_core.library.ergonomic_limits import seed_documents, seed_entries

    (doc,) = seed_documents()
    assert "MIL-STD-1472H" in doc["source"]["document"]
    assert "Approved for public release" in doc["source"]["licence"]
    catalog = SqliteCatalog()
    entries = [catalog.register("ergonomic_limit", e, by="seed") for e in seed_entries()]
    assert len(entries) == 30
    assert all(e.source_kind == "standard" and not e.needs_review for e in entries)
    assert all(e.source_ref.startswith("MIL-STD-1472H (15 Sep 2020), ") for e in entries)
    assert all("everyspec.com" in e.source_ref for e in entries)
    by_name = {e.name: e for e in entries}
    # Spot values, as read in the standard (Figure 14, Figure 101, 5.10.3.9.2.2).
    button = by_name["mil1472h_pushbutton_fingertip_diameter"]
    assert (button.min, button.max, button.unit) == (10, 25, "mm")
    assert by_name["mil1472h_pushbutton_single_finger_force"].max == 11
    assert by_name["mil1472h_one_hand_bar_length_bare"].min == 111
    assert by_name["mil1472h_handle_diameter_over_18kg"].min == 25
    desk = by_name["mil1472h_seated_work_surface_height"]
    assert (desk.min, desk.max) == (740, 790)
    kinds = {e.limit_kind for e in entries}
    assert kinds == set(ERGONOMIC_KINDS) - {"grip_width", "product_mass"}
