"""Processes and materials as library data, and in the flow context (FORGE-451, FORGE-446)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_gateway.design_flows.routes import ProposeFlowRequest
from api_gateway.library import catalog_routes
from api_gateway.library import routes as machine_routes
from api_gateway.library.mcp_bindings import LocalMachineLibrary
from mcp_core.annotations import ADDITIVE, READ_ONLY
from mcp_core.profiles import PROFILES
from metaforge.mcp.remote_library import RemoteMachineLibrary
from tool_registry.tools.library.adapter import LibraryServer
from twin_core.library.catalog import CatalogError, CatalogNotFoundError, SqliteCatalog
from twin_core.library.machines import SqliteMachineLibrary

STD = "ISO 2768-1 class m"
DS = "https://example.com/6061-t6-datasheet.pdf"

CNC: dict[str, Any] = {
    "name": "cnc_milling",
    "title": "CNC milling",
    "family": "subtractive",
    "machine_kinds": ["cnc_mill"],
    "material_families": ["metal", "polymer"],
    "rules": [
        {"kind": "wall_thickness", "min": 0.8, "material_family": "metal", "source_kind": "user"},
        {"kind": "wall_thickness", "min": 1.5, "material_family": "polymer", "source_kind": "user"},
        {"kind": "inside_corner_radius", "min": 0.5, "source_kind": "user"},
        {"kind": "tolerance", "value": 0.1, "source_kind": "standard", "source_ref": STD},
        {"kind": "draft_angle", "min": 0, "source_kind": "assumption"},
    ],
}

AL: dict[str, Any] = {
    "name": "aluminium_6061_t6",
    "title": "Aluminium 6061-T6",
    "family": "metal",
    "designation": "EN AW-6061 T6",
    "forms": ["sheet", "bar"],
    "processes": ["cnc_milling"],
    "properties": {
        "density_kg_m3": {
            "value": 2700,
            "unit": "kg/m3",
            "source_kind": "datasheet",
            "source_ref": DS,
        },
        "yield_strength_mpa": {
            "value": 276,
            "unit": "MPa",
            "source_kind": "datasheet",
            "source_ref": DS,
        },
        "cost_per_kg": {"value": 9.5, "unit": "GBP/kg", "source_kind": "assumption"},
    },
}


@pytest.fixture
def catalog() -> Any:
    cat = SqliteCatalog()
    catalog_routes.init_catalog(cat)
    machine_routes.init_machine_library(SqliteMachineLibrary())
    yield cat
    catalog_routes.init_catalog(None)
    machine_routes.init_machine_library(None)


class TestStore:
    def test_process_limits_apply_the_rules_for_the_material_family(
        self, catalog: SqliteCatalog
    ) -> None:
        catalog.register("process", CNC)
        catalog.register("material", AL)
        limits = catalog.process_limits("cnc_milling", "aluminium_6061_t6")
        walls = [lim for lim in limits["limits"] if lim["kind"] == "wall_thickness"]
        assert walls == [
            {"kind": "wall_thickness", "min": 0.8, "source_ref": "user (cnc_milling rule)"}
        ]
        assert limits["candidate"]["achievable_tolerance_mm"] == 0.1
        assert STD in limits["candidate"]["source_refs"]
        assert limits["needs_review"] == ["draft_angle"]
        # With no material named, a rule for one family does not apply.
        bare = catalog.process_limits("cnc_milling")
        assert [x["kind"] for x in bare["limits"]] == ["inside_corner_radius", "draft_angle"]

    def test_a_process_is_refused_for_a_family_it_does_not_make(
        self, catalog: SqliteCatalog
    ) -> None:
        catalog.register("process", {**CNC, "name": "fdm", "material_families": ["filament"]})
        catalog.register("material", AL)
        with pytest.raises(CatalogError, match="not recorded as making metal"):
            catalog.process_limits("fdm", "aluminium_6061_t6")

    def test_sources_units_and_names_are_checked(self, catalog: SqliteCatalog) -> None:
        bad_ref = {
            **AL,
            "properties": {"density_kg_m3": {"value": 2700, "source_kind": "datasheet"}},
        }
        with pytest.raises(CatalogError, match="needs its reference"):
            catalog.register("material", bad_ref)
        bad_unit = {
            **AL,
            "properties": {"density_kg_m3": {"value": 2.7, "unit": "g/cm3", "source_kind": "user"}},
        }
        with pytest.raises(CatalogError, match="kg/m3"):
            catalog.register("material", bad_unit)
        no_currency = {
            **AL,
            "properties": {"cost_per_kg": {"value": 9, "unit": "/kg", "source_kind": "user"}},
        }
        with pytest.raises(CatalogError, match="currency"):
            catalog.register("material", no_currency)
        with pytest.raises(CatalogError, match="unknown property"):
            catalog.register(
                "material", {**AL, "properties": {"hardness": {"value": 1, "source_kind": "user"}}}
            )
        with pytest.raises(CatalogError, match="lower-case identifier"):
            catalog.register("process", {**CNC, "name": "CNC Milling!"})
        with pytest.raises(CatalogError, match="kind must be one of"):
            catalog.register(
                "process", {**CNC, "rules": [{"kind": "surface", "min": 1, "source_kind": "user"}]}
            )
        with pytest.raises(CatalogError, match="machine_kinds"):
            catalog.register("process", {**CNC, "machine_kinds": ["toaster"]})

    def test_names_are_unique_per_kind_and_updates_keep_versions(
        self, catalog: SqliteCatalog
    ) -> None:
        entry = catalog.register("material", AL, by="person")
        with pytest.raises(CatalogError, match="already exists"):
            catalog.register("material", AL)
        updated = catalog.update(
            entry.id,
            {"properties": {"elongation_pct": {"value": 12, "unit": "%", "source_kind": "user"}}},
            by="person",
        )
        assert updated.version == 2
        assert set(updated.properties) == {
            "density_kg_m3",
            "yield_strength_mpa",
            "cost_per_kg",
            "elongation_pct",
        }
        assert [h["version"] for h in catalog.history(entry.id)] == [1, 2]
        assert catalog.get("aluminium_6061_t6", "material").id == entry.id
        with pytest.raises(CatalogNotFoundError):
            catalog.get("aluminium_6061_t6", "process")

    def test_the_file_survives_a_reopen(self, tmp_path: Any) -> None:
        path = str(tmp_path / "lib.db")
        SqliteCatalog(path).register("process", CNC)
        assert SqliteCatalog(path).get("cnc_milling", "process").title == "CNC milling"


@pytest.fixture
def client(catalog: SqliteCatalog) -> TestClient:
    app = FastAPI()
    app.include_router(catalog_routes.router)
    return TestClient(app)


def test_routes(client: TestClient) -> None:
    assert client.post("/v1/library/processes", json=CNC).status_code == 201
    assert client.post("/v1/library/materials", json=AL).status_code == 201
    assert [e["name"] for e in client.get("/v1/library/processes").json()["entries"]] == [
        "cnc_milling"
    ]
    got = client.get("/v1/library/materials/aluminium_6061_t6").json()
    assert got["entry"]["properties"]["density_kg_m3"]["value"] == 2700
    limits = client.get(
        "/v1/library/processes/cnc_milling/limits", params={"material": "aluminium_6061_t6"}
    )
    assert limits.status_code == 200
    patched = client.patch("/v1/library/processes/cnc_milling", json={"changes": {"notes": "x"}})
    assert patched.json()["entry"]["version"] == 2
    assert client.get("/v1/library/processes/nope").status_code == 404
    assert client.post("/v1/library/processes", json={**CNC, "family": "magic"}).status_code == 422


@pytest.mark.asyncio
async def test_local_and_remote_bindings_answer_alike(client: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        response = client.request(request.method, str(request.url.raw_path, "ascii"), json=body)
        return httpx.Response(response.status_code, json=response.json())

    local = LibraryServer(LocalMachineLibrary())
    await local._tools["library.register_process"].handler(CNC)
    await local._tools["library.register_material"].handler(AL)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        remote = LibraryServer(RemoteMachineLibrary("http://gw", client=http))
        for tool, args in (
            ("library.list_processes", {}),
            ("library.list_materials", {"family": "metal"}),
            ("library.get_material", {"ref": "aluminium_6061_t6"}),
            ("library.process_limits", {"ref": "cnc_milling", "material": "aluminium_6061_t6"}),
        ):
            assert await local._tools[tool].handler(args) == await remote._tools[tool].handler(
                args
            ), tool


def test_annotations_and_profile() -> None:
    reads = {
        "library.list_processes",
        "library.get_process",
        "library.process_limits",
        "library.list_materials",
        "library.get_material",
    }
    writes = {
        "library.register_process",
        "library.update_process",
        "library.register_material",
        "library.update_material",
    }
    assert reads <= READ_ONLY
    assert writes <= ADDITIVE
    assert "library.process_limits" in PROFILES["manufacturing"]


def _request(**manufacturing: Any) -> ProposeFlowRequest:
    return ProposeFlowRequest.model_validate(
        {"intent": "a bracket", "manufacturingContext": {"route": "in_house", **manufacturing}}
    )


class TestFlowContext:
    def test_chosen_entries_reach_the_generator_and_every_phase(
        self, catalog: SqliteCatalog
    ) -> None:
        catalog.register("process", CNC)
        catalog.register("process", {**CNC, "name": "laser_cutting", "family": "cutting"})
        catalog.register("material", AL)
        context = _request(
            process_ids=["cnc_milling"], material_ids=["aluminium_6061_t6"]
        ).flow_context()
        prompt = context.describe_for_prompt()
        assert "Manufacturing processes chosen for this project" in prompt
        assert "wall_thickness >= 0.8 mm (metal)" in prompt
        assert "laser_cutting" not in prompt
        assert "Materials chosen for this project" in prompt
        assert "density 2700 kg/m3" in prompt
        assert "assumed, needs review: cost_per_kg" in prompt
        assert "library.process_limits" in context.render_for_phases()
        assert context.manufacturing is not None
        assert "library processes" in context.capability_basis()

    def test_stated_processes_and_stock_are_matched(self, catalog: SqliteCatalog) -> None:
        catalog.register("process", CNC)
        catalog.register("process", {**CNC, "name": "laser_cutting", "family": "cutting"})
        catalog.register("material", AL)
        catalog.register(
            "material",
            {**AL, "name": "abs", "title": "ABS", "family": "polymer", "designation": ""},
        )
        prompt = (
            _request(processes=["CNC milling"], stockMaterials=["6061"])
            .flow_context()
            .describe_for_prompt()
        )
        assert "matching what was stated" in prompt
        assert "CNC milling [" in prompt and "laser_cutting" not in prompt
        assert "Aluminium 6061-T6" in prompt and "ABS [" not in prompt

    def test_a_library_machines_processes_are_matched(self, catalog: SqliteCatalog) -> None:
        catalog.register("process", {**CNC, "name": "fdm", "title": "FDM", "family": "additive"})
        catalog.register("process", CNC)
        machine_routes.get_machine_library().register(
            {"layer": "instance", "kind": "fdm_printer", "name": "CR-10", "processes": ["fdm"]}
        )
        prompt = _request().flow_context().describe_for_prompt()
        assert "FDM [" in prompt and "CNC milling [" not in prompt

    def test_with_nothing_matched_the_library_is_listed(self, catalog: SqliteCatalog) -> None:
        catalog.register("process", CNC)
        prompt = _request(processes=["waterjet"]).flow_context().describe_for_prompt()
        assert "in the library (none chosen or matched)" in prompt

    def test_an_unknown_entry_is_refused(self, catalog: SqliteCatalog) -> None:
        with pytest.raises(HTTPException) as exc:
            _request(material_ids=["unobtainium"]).flow_context()
        assert exc.value.status_code == 422
        assert "library.list_materials" in str(exc.value.detail)

    def test_an_empty_library_leaves_the_text_unchanged(self, catalog: SqliteCatalog) -> None:
        prompt = _request().flow_context().describe_for_prompt()
        assert "Manufacturing processes" not in prompt and "Materials " not in prompt


def test_timber_strengths_have_their_own_properties(catalog: SqliteCatalog) -> None:
    oak = catalog.register(
        "material",
        {
            "name": "oak_d30",
            "family": "wood",
            "properties": {
                "bending_strength_mpa": {
                    "value": 30,
                    "unit": "MPa",
                    "source_kind": "standard",
                    "source_ref": "EN 338 D30",
                },
                "tensile_strength_across_mpa": {
                    "value": 0.6,
                    "unit": "MPa",
                    "source_kind": "standard",
                    "source_ref": "EN 338 D30",
                },
            },
        },
    )
    assert "yield_strength_mpa" not in oak.properties
    assert oak.properties["bending_strength_mpa"].value == 30
