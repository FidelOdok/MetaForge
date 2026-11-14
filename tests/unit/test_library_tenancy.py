"""The library on a gateway shared by several accounts (FORGE-876).

Shared entries are read by every account and changed by none; an account's
own entries are seen and changed only by that account. The real library routes
and stores run behind the real auth middleware, with a fake provider.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.auth.middleware import AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import InvalidToken
from api_gateway.auth.scope import ProjectScope, RouteTenancy, classify_routes
from api_gateway.library import catalog_routes, routes
from twin_core.library.catalog import CatalogPermissionError, SqliteCatalog
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.library.viewer import library_viewer

SCOPES = {
    "alice": ProjectScope(tenant="acct-alice"),
    "bob": ProjectScope(tenant="acct-bob"),
    "nobody": ProjectScope(),  # signed in, no account to own entries
    "service": ProjectScope(unrestricted=True),
}


def _material(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "title": name,
        "family": "polymer",
        "properties": {
            "density_kg_m3": {"value": 1270, "unit": "kg/m3", "source_kind": "user"},
        },
    }


INSTANCE = {
    "layer": "instance",
    "kind": "cnc_mill",
    "name": "Shop mill",
    "processes": ["cnc_milling"],
    "capabilities": {"min_tool_diameter_mm": {"value": 3, "unit": "mm", "source_kind": "user"}},
}


class Provider:
    name = "fake"

    async def verify(self, token: str) -> Principal:
        if token not in SCOPES:
            raise InvalidToken("no")
        return Principal(subject=token)

    async def project_scope(self, principal: Principal) -> ProjectScope:
        return SCOPES[principal.subject]

    async def project_created(self, principal: Principal, project_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client() -> Iterator[TestClient]:
    catalog, machines = SqliteCatalog(), SqliteMachineLibrary()
    catalog_routes.init_catalog(catalog)
    routes.init_machine_library(machines)
    # The curated, shared layer: written unscoped, as an operator or seed would.
    with library_viewer(None):
        catalog.register("material", _material("pla"), by="seed")
    app = FastAPI()
    app.include_router(catalog_routes.router)
    app.include_router(routes.router)
    app.add_middleware(AuthMiddleware, provider=Provider())
    yield TestClient(app)
    catalog_routes.init_catalog(None)
    routes.init_machine_library(None)


def _as(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {who}"}


def _names(resp: Any) -> set[str]:
    return {e["name"] for e in resp.json()["entries"]}


def test_shared_entries_are_read_by_every_account(client: TestClient) -> None:
    for who in ("alice", "bob", "nobody"):
        r = client.get("/v1/library/materials", headers=_as(who))
        assert r.status_code == 200 and _names(r) == {"pla"}
        assert r.json()["entries"][0]["shared"] is True


def test_an_accounts_own_entry_is_invisible_to_others(client: TestClient) -> None:
    r = client.post("/v1/library/materials", json=_material("my_petg"), headers=_as("alice"))
    assert r.status_code == 201 and r.json()["entry"]["shared"] is False
    entry_id = r.json()["entry"]["id"]

    assert _names(client.get("/v1/library/materials", headers=_as("alice"))) == {"pla", "my_petg"}
    assert _names(client.get("/v1/library/materials", headers=_as("bob"))) == {"pla"}
    for ref in ("my_petg", entry_id):
        assert client.get(f"/v1/library/materials/{ref}", headers=_as("bob")).status_code == 404
        assert client.get(f"/v1/library/materials/{ref}", headers=_as("alice")).status_code == 200


def test_two_accounts_may_use_the_same_name(client: TestClient) -> None:
    for who in ("alice", "bob"):
        r = client.post("/v1/library/materials", json=_material("my_petg"), headers=_as(who))
        assert r.status_code == 201, r.text
    a = client.get("/v1/library/materials/my_petg", headers=_as("alice")).json()["entry"]
    b = client.get("/v1/library/materials/my_petg", headers=_as("bob")).json()["entry"]
    assert a["id"] != b["id"]


def test_an_account_cannot_take_a_shared_name(client: TestClient) -> None:
    r = client.post("/v1/library/materials", json=_material("pla"), headers=_as("alice"))
    assert r.status_code == 422 and "already exists" in r.text


def test_shared_entries_cannot_be_changed_by_an_account(client: TestClient) -> None:
    body = {"changes": {"notes": "mine now"}}
    r = client.patch("/v1/library/materials/pla", json=body, headers=_as("alice"))
    assert r.status_code == 403 and "shared library data" in r.text


def test_an_account_changes_its_own_entry_and_nobody_elses(client: TestClient) -> None:
    client.post("/v1/library/materials", json=_material("my_petg"), headers=_as("alice"))
    body = {"changes": {"notes": "dried 4h at 65 C"}}
    assert (
        client.patch("/v1/library/materials/my_petg", json=body, headers=_as("bob")).status_code
        == 404
    )
    r = client.patch("/v1/library/materials/my_petg", json=body, headers=_as("alice"))
    assert r.status_code == 200 and r.json()["entry"]["version"] == 2


def test_a_caller_without_an_account_only_reads(client: TestClient) -> None:
    r = client.post("/v1/library/materials", json=_material("x"), headers=_as("nobody"))
    assert r.status_code == 403
    assert client.post(
        "/v1/library/machines", json=INSTANCE, headers=_as("nobody")
    ).status_code == (403)


def test_the_deployments_services_see_every_entry(client: TestClient) -> None:
    client.post("/v1/library/materials", json=_material("my_petg"), headers=_as("alice"))
    assert _names(client.get("/v1/library/materials", headers=_as("service"))) == {
        "pla",
        "my_petg",
    }


def test_machines_are_per_account_too(client: TestClient) -> None:
    r = client.post("/v1/library/machines", json=INSTANCE, headers=_as("alice"))
    assert r.status_code == 201
    machine_id = r.json()["machine"]["id"]
    assert client.get("/v1/library/machines", headers=_as("bob")).json()["machines"] == []
    assert client.get(f"/v1/library/machines/{machine_id}", headers=_as("bob")).status_code == 404
    assert (
        client.get(f"/v1/library/machines/{machine_id}/limits", headers=_as("bob")).status_code
        == 404
    )
    body = {"changes": {"location": "bay 2"}}
    patch = client.patch(f"/v1/library/machines/{machine_id}", json=body, headers=_as("bob"))
    assert patch.status_code == 404
    patch = client.patch(f"/v1/library/machines/{machine_id}", json=body, headers=_as("alice"))
    assert patch.status_code == 200 and patch.json()["machine"]["shared"] is False


def test_an_export_holds_shared_entries_and_the_callers_own(client: TestClient) -> None:
    client.post("/v1/library/materials", json=_material("alices"), headers=_as("alice"))
    client.post("/v1/library/materials", json=_material("bobs"), headers=_as("bob"))
    doc = client.get("/v1/library/export", headers=_as("alice")).json()
    text = repr(doc)
    assert "alices" in text and "pla" in text and "bobs" not in text


def test_library_writes_are_tenant_routes_and_import_stays_blocked() -> None:
    app = FastAPI()
    app.include_router(catalog_routes.router)
    app.include_router(routes.router)
    tenancy = classify_routes(app.routes)
    assert tenancy[("POST", "/v1/library/materials")] is RouteTenancy.TENANT_LIBRARY
    assert tenancy[("PATCH", "/v1/library/machines/{machine_id}")] is RouteTenancy.TENANT_LIBRARY
    assert tenancy[("POST", "/v1/library/import")] is RouteTenancy.BLOCKED
    assert tenancy[("POST", "/v1/library/jobs")] is RouteTenancy.BLOCKED


# -- the stores ------------------------------------------------------------


def test_an_unscoped_name_lookup_prefers_the_shared_entry() -> None:
    catalog = SqliteCatalog()
    catalog.register("material", _material("abs"), owner="acct-alice")
    shared = catalog.register("material", _material("abs"), owner=None)
    assert catalog.get("abs", "material", viewer=None).id == shared.id
    assert catalog.get("abs", "material", viewer="acct-alice").id != shared.id
    with pytest.raises(CatalogPermissionError):
        catalog.update(shared.id, {"notes": "x"}, editor="acct-alice")


def test_an_older_catalog_file_gains_owners_and_keeps_its_entries(tmp_path: Path) -> None:
    import json

    seeded = SqliteCatalog().register("material", _material("nylon"), owner=None)
    path = tmp_path / "library.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE catalog_entries (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            body TEXT NOT NULL, version INTEGER NOT NULL, updated_at REAL NOT NULL,
            UNIQUE (kind, name)
        );
        """
    )
    conn.execute(
        "INSERT INTO catalog_entries (id, kind, name, body, version, updated_at)"
        " VALUES (?, 'material', 'nylon', ?, 1, 0)",
        (seeded.id, json.dumps(seeded.as_dict())),
    )
    conn.commit()
    conn.close()

    reopened = SqliteCatalog(str(path))
    assert reopened.owner_of(seeded.id) is None  # an existing entry is shared
    assert reopened.get("nylon", "material", viewer="acct-bob").id == seeded.id
    # The rebuilt table's uniqueness is per owner.
    reopened.register("material", _material("mine"), owner="acct-bob")
    reopened.register("material", _material("mine"), owner="acct-alice")


def test_a_pre_owner_machine_file_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "machines.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE machines (id TEXT PRIMARY KEY, layer TEXT NOT NULL, body TEXT NOT NULL,"
        " version INTEGER NOT NULL, updated_at REAL NOT NULL)"
    )
    conn.commit()
    conn.close()
    lib = SqliteMachineLibrary(str(path))
    m = lib.register(INSTANCE, owner="acct-alice")
    assert lib.owner_of(m.id) == "acct-alice"
    lib.update(m.id, {"location": "bay 3"}, editor="acct-alice")
    assert lib.owner_of(m.id) == "acct-alice"  # an update keeps the owner
