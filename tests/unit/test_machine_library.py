"""The machine library: store, routes, MCP tools and both bindings (FORGE-451)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.library import routes as library_routes
from api_gateway.library.mcp_bindings import LocalMachineLibrary
from mcp_core.annotations import ADDITIVE, READ_ONLY
from mcp_core.profiles import PROFILES
from metaforge.mcp.remote_library import RemoteMachineLibrary
from tool_registry.tools.library.adapter import LibraryServer
from twin_core.library.machines import (
    MachineLibraryError,
    MachineNotFoundError,
    SqliteMachineLibrary,
    parse_capabilities,
)

SPEC = "https://www.creality.com/products/cr-10 (spec sheet)"

CR10_MODEL: dict[str, Any] = {
    "layer": "model",
    "kind": "fdm_printer",
    "make": "Creality",
    "model": "CR-10",
    "processes": ["fdm"],
    "capabilities": {
        "build_volume_mm": {
            "value": [300, 300, 400],
            "unit": "mm",
            "source_kind": "manufacturer_spec",
            "source_ref": SPEC,
        },
        "nozzle_mm": {
            "value": 0.4,
            "unit": "mm",
            "source_kind": "manufacturer_spec",
            "source_ref": SPEC,
        },
        "materials": {
            "value": ["PLA", "PETG"],
            "unit": "list",
            "source_kind": "manufacturer_spec",
            "source_ref": SPEC,
        },
    },
}


def _instance(model_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "layer": "instance",
        "kind": "fdm_printer",
        "name": "Workshop CR-10",
        "model_id": model_id,
        "route": "in_house",
        "status": "available",
        "capabilities": {
            "min_wall_mm": {
                "value": 0.8,
                "unit": "mm",
                "source_kind": "measurement",
                "source_ref": "calibration print 2026-10-01",
            },
            "max_overhang_deg": {"value": 45, "unit": "deg", "source_kind": "assumption"},
        },
        **extra,
    }


@pytest.fixture
def library() -> SqliteMachineLibrary:
    return SqliteMachineLibrary()


class TestCapabilities:
    def test_a_spec_value_needs_its_reference(self) -> None:
        with pytest.raises(MachineLibraryError, match="needs its reference"):
            parse_capabilities(
                {"nozzle_mm": {"value": 0.4, "unit": "mm", "source_kind": "manufacturer_spec"}}
            )

    def test_unknown_names_and_wrong_units_are_refused(self) -> None:
        with pytest.raises(MachineLibraryError):
            parse_capabilities({"speed": {"value": 1, "unit": "mm", "source_kind": "user"}})
        with pytest.raises(MachineLibraryError, match="in mm"):
            parse_capabilities({"nozzle_mm": {"value": 0.4, "unit": "in", "source_kind": "user"}})

    def test_an_envelope_is_three_numbers(self) -> None:
        with pytest.raises(MachineLibraryError, match="three numbers"):
            parse_capabilities(
                {"build_volume_mm": {"value": [300, 300], "unit": "mm", "source_kind": "user"}}
            )

    def test_an_assumption_is_flagged_for_review(self) -> None:
        caps = parse_capabilities(
            {"max_overhang_deg": {"value": 45, "unit": "deg", "source_kind": "assumption"}}
        )
        assert caps["max_overhang_deg"].needs_review is True


class TestStore:
    def test_instance_limits_merge_its_model_and_cite_sources(
        self, library: SqliteMachineLibrary
    ) -> None:
        model = library.register(CR10_MODEL, by="person")
        instance = library.register(_instance(model.id), by="person")
        limits = library.limits(instance.id)
        assert limits["processes"] == ["fdm"]
        assert limits["candidate"]["max_envelope_mm"] == [300, 300, 400]
        assert limits["candidate"]["materials"] == ["PLA", "PETG"]
        assert limits["candidate"]["available"] is True
        by_kind = {lim["kind"]: lim for lim in limits["limits"]}
        assert by_kind["envelope"]["max_dims_mm"] == [300, 300, 400]
        assert SPEC in by_kind["envelope"]["source_ref"]
        assert by_kind["wall_thickness"]["min"] == 0.8
        assert by_kind["overhang_angle"]["max"] == 45
        assert limits["needs_review"] == ["max_overhang_deg"]
        # Nothing it was not given: no feature or bend limits appear.
        assert "feature_size" not in by_kind
        assert "bend_radius" not in by_kind

    def test_a_tool_diameter_bounds_inside_corners_at_its_radius(
        self, library: SqliteMachineLibrary
    ) -> None:
        mill = library.register(
            {
                "layer": "instance",
                "kind": "cnc_mill",
                "name": "Shop mill",
                "processes": ["cnc_milling"],
                "capabilities": {
                    "min_tool_diameter_mm": {"value": 3, "unit": "mm", "source_kind": "user"}
                },
            }
        )
        (corner,) = library.limits(mill.id)["limits"]
        assert corner == {
            "kind": "inside_corner_radius",
            "min": 1.5,
            "source_ref": corner["source_ref"],
        }

    def test_an_update_keeps_the_old_version(self, library: SqliteMachineLibrary) -> None:
        model = library.register(CR10_MODEL)
        instance = library.register(_instance(model.id))
        updated = library.update(instance.id, {"status": "busy"}, by="person")
        assert updated.version == 2
        assert [h["version"] for h in library.history(instance.id)] == [1, 2]
        assert library.limits(instance.id)["candidate"]["available"] is False

    def test_secrets_are_refused_in_a_connection(self, library: SqliteMachineLibrary) -> None:
        with pytest.raises(MachineLibraryError):
            library.register(_instance("", connection={"protocol": "octoprint", "api_key": "abc"}))
        ok = library.register(
            _instance(
                "",
                connection={
                    "protocol": "octoprint",
                    "url": "http://printer.local",
                    "credential_env": "METAFORGE_MACHINE_CR10_KEY",
                },
            )
        )
        assert ok.connection["credential_env"] == "METAFORGE_MACHINE_CR10_KEY"

    def test_a_model_has_no_limits_of_its_own(self, library: SqliteMachineLibrary) -> None:
        model = library.register(CR10_MODEL)
        with pytest.raises(MachineLibraryError, match="instance"):
            library.limits(model.id)

    def test_unknown_ids_and_kinds(self, library: SqliteMachineLibrary) -> None:
        with pytest.raises(MachineNotFoundError):
            library.get("mi_nope")
        with pytest.raises(MachineLibraryError, match="kind"):
            library.register({**CR10_MODEL, "kind": "toaster"})

    def test_the_file_survives_a_reopen(self, tmp_path: Any) -> None:
        path = str(tmp_path / "lib.db")
        model = SqliteMachineLibrary(path).register(CR10_MODEL)
        assert SqliteMachineLibrary(path).get(model.id).make == "Creality"


@pytest.fixture
def client() -> Any:
    library_routes.init_machine_library(SqliteMachineLibrary())
    app = FastAPI()
    app.include_router(library_routes.router)
    yield TestClient(app)
    library_routes.init_machine_library(None)


class TestRoutes:
    def test_register_list_get_update_limits(self, client: TestClient) -> None:
        model = client.post("/v1/library/machines", json=CR10_MODEL)
        assert model.status_code == 201
        model_id = model.json()["machine"]["id"]
        inst = client.post(
            "/v1/library/machines",
            json=_instance(model_id),
            headers={"X-MetaForge-Agent": "claude-code"},
        ).json()["machine"]
        listing = client.get("/v1/library/machines", params={"layer": "instance"}).json()
        assert [m["id"] for m in listing["machines"]] == [inst["id"]]
        got = client.get(f"/v1/library/machines/{inst['id']}").json()
        assert got["history"][0]["changed_by"] == "claude-code"
        patched = client.patch(
            f"/v1/library/machines/{inst['id']}", json={"changes": {"location": "Bay 2"}}
        )
        assert patched.json()["machine"]["version"] == 2
        limits = client.get(f"/v1/library/machines/{inst['id']}/limits").json()
        assert limits["machine"] == "Workshop CR-10"

    def test_errors_read_as_404_and_422(self, client: TestClient) -> None:
        assert client.get("/v1/library/machines/mi_nope").status_code == 404
        bad = client.post("/v1/library/machines", json={**CR10_MODEL, "kind": "toaster"})
        assert bad.status_code == 422
        assert "kind" in bad.json()["detail"]


class TestMcp:
    def test_reads_run_freely_and_writes_are_held(self) -> None:
        reads = {"library.list_machines", "library.get_machine", "library.machine_limits"}
        writes = {"library.register_machine", "library.update_machine"}
        assert reads <= READ_ONLY
        assert writes <= ADDITIVE
        assert not writes & READ_ONLY
        assert set(LibraryServer(LocalMachineLibrary()).tool_ids) == reads | writes

    def test_the_manufacturing_profile_reads_the_library(self) -> None:
        assert {"library.list_machines", "library.machine_limits"} <= PROFILES["manufacturing"]

    @pytest.mark.asyncio
    async def test_local_and_remote_bindings_answer_alike(self, client: TestClient) -> None:
        local_server = LibraryServer(LocalMachineLibrary())
        model = await local_server.register_machine(CR10_MODEL)
        model_id = model["machine"]["id"]
        inst = await local_server.register_machine(_instance(model_id))
        inst_id = inst["machine"]["id"]

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            response = client.request(request.method, str(request.url.raw_path, "ascii"), json=body)
            return httpx.Response(response.status_code, json=response.json())

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            remote_server = LibraryServer(RemoteMachineLibrary("http://gw", client=http))
            for name, args in (
                ("list_machines", {}),
                ("get_machine", {"machine_id": inst_id}),
                ("machine_limits", {"machine_id": inst_id}),
            ):
                local = await getattr(local_server, name)(args)
                remote = await getattr(remote_server, name)(args)
                assert local == remote, name
            changed = await remote_server.update_machine(
                {"machine_id": inst_id, "changes": {"status": "offline"}}
            )
            assert changed["machine"]["status"] == "offline"
            with pytest.raises(Exception, match="not found"):
                await remote_server.get_machine({"machine_id": "mi_nope"})


@pytest.mark.asyncio
async def test_bootstrap_registers_the_library_when_bound() -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    registry = await bootstrap_tool_registry(
        adapter_ids=["library"], machine_library=LocalMachineLibrary()
    )
    tools = {t.tool_id for t in registry.list_tools()}
    assert "library.machine_limits" in tools


class TestConnection:
    def test_a_credential_name_outside_the_machine_namespace_is_refused(
        self, library: SqliteMachineLibrary
    ) -> None:
        # The gateway sends the named credential to the machine's URL, so a
        # free name would let a caller ship any gateway secret to any host.
        for env in ("ANTHROPIC_API_KEY", "metaforge_machine_x", "METAFORGE_MACHINE_A-B"):
            with pytest.raises(MachineLibraryError, match="credential_env"):
                library.register(
                    _instance(
                        "",
                        connection={
                            "protocol": "octoprint",
                            "url": "http://p.local",
                            "credential_env": env,
                        },
                    )
                )

    def test_protocol_and_url_are_checked(self, library: SqliteMachineLibrary) -> None:
        with pytest.raises(MachineLibraryError, match="protocol"):
            library.register(_instance("", connection={"protocol": "ftp", "url": "http://x"}))
        with pytest.raises(MachineLibraryError, match="url"):
            library.register(_instance("", connection={"protocol": "moonraker", "url": "x"}))
        with pytest.raises(MachineLibraryError, match="no field"):
            library.register(_instance("", connection={"protocol": "none", "headers": {"X": "y"}}))
