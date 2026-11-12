"""A gateway shared by several accounts confines each caller to its projects
(FORGE-849).

Driven through the real :class:`AuthMiddleware` with a fake provider that
implements both halves of the seam, against a small app built from real route
shapes plus the real projects router. No network, no identity provider.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api_gateway.auth.middleware import AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import AuthUnavailable, InvalidToken
from api_gateway.auth.scope import (
    ProjectScope,
    RouteTenancy,
    classify_routes,
    supports_project_scope,
)

ALICE_P = "11111111-1111-1111-1111-111111111111"
BOB_P = "22222222-2222-2222-2222-222222222222"


class FakeProvider:
    name = "fake"

    def __init__(self, *, scoped: bool = True, down: bool = False) -> None:
        self.scopes: dict[str, set[str]] = {"alice": {ALICE_P}, "bob": {BOB_P}, "svc": set()}
        self.created: list[tuple[str, str]] = []
        self.down = down
        self.fail_create = False
        if not scoped:
            self.project_scope = None  # type: ignore[assignment]
            self.project_created = None  # type: ignore[assignment]

    async def verify(self, token: str) -> Principal:
        if token not in self.scopes:
            raise InvalidToken("unknown")
        return Principal(subject=token)

    async def project_scope(self, principal: Principal) -> ProjectScope:
        if self.down:
            raise AuthUnavailable("control plane down")
        if principal.subject == "svc":
            return ProjectScope(unrestricted=True)
        return ProjectScope(frozenset(self.scopes[principal.subject]))

    async def project_created(self, principal: Principal, project_id: str) -> None:
        if self.fail_create:
            raise RuntimeError("control plane write failed")
        self.scopes[principal.subject].add(project_id)
        self.created.append((principal.subject, project_id))

    async def aclose(self) -> None:
        pass


class Body(BaseModel):
    project_id: str | None = None
    text: str = ""


def build(provider: FakeProvider) -> FastAPI:
    from api_gateway.projects import routes as projects_routes
    from api_gateway.projects.backend import InMemoryProjectBackend

    projects_routes.init_project_backend(InMemoryProjectBackend.create())

    app = FastAPI()

    @app.get("/v1/twin/projects/{project_id}/nodes")
    async def by_path(project_id: str) -> dict:
        return {"project": project_id}

    @app.get("/v1/knowledge/search")
    async def by_query(project_id: str | None = None) -> dict:
        return {"project": project_id}

    @app.post("/v1/runs")
    async def by_body(body: Body) -> dict:
        return {"project": body.project_id, "text": body.text}

    @app.get("/v1/library/materials")
    async def shared() -> dict:
        return {"materials": []}

    @app.post("/v1/library/materials")
    async def shared_write() -> dict:
        return {}

    @app.get("/v1/memory/insights")
    async def unscoped() -> dict:
        return {"everyone": "s data"}

    app.include_router(projects_routes.router)
    app.state.auth_provider = provider
    app.add_middleware(AuthMiddleware, provider=provider)
    return app


def h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {who}"}


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(provider: FakeProvider) -> TestClient:
    return TestClient(build(provider))


def test_path_project_inside_scope_is_answered(client: TestClient) -> None:
    r = client.get(f"/v1/twin/projects/{ALICE_P}/nodes", headers=h("alice"))
    assert r.status_code == 200


def test_another_accounts_project_is_not_found(client: TestClient) -> None:
    r = client.get(f"/v1/twin/projects/{BOB_P}/nodes", headers=h("alice"))
    assert r.status_code == 404


def test_query_project_is_required_and_checked(client: TestClient) -> None:
    assert client.get("/v1/knowledge/search", headers=h("alice")).status_code == 400
    assert (
        client.get(f"/v1/knowledge/search?project_id={BOB_P}", headers=h("alice")).status_code
        == 404
    )
    r = client.get(f"/v1/knowledge/search?projectId={ALICE_P}", headers=h("alice"))
    assert r.status_code == 200


def test_body_project_is_checked_and_the_body_still_reaches_the_route(
    client: TestClient,
) -> None:
    assert (
        client.post("/v1/runs", json={"project_id": BOB_P}, headers=h("alice")).status_code == 404
    )
    assert client.post("/v1/runs", json={"text": "x"}, headers=h("alice")).status_code == 400
    r = client.post("/v1/runs", json={"project_id": ALICE_P, "text": "hi"}, headers=h("alice"))
    assert r.status_code == 200
    assert r.json() == {"project": ALICE_P, "text": "hi"}


def test_shared_reference_data_is_readable_not_writable(client: TestClient) -> None:
    assert client.get("/v1/library/materials", headers=h("alice")).status_code == 200
    assert client.post("/v1/library/materials", headers=h("alice")).status_code == 403


def test_a_route_that_names_no_project_is_refused(client: TestClient) -> None:
    r = client.get("/v1/memory/insights", headers=h("alice"))
    assert r.status_code == 403
    assert "not yet confined to a project" in r.json()["detail"]


def test_project_list_and_create_are_per_account(
    client: TestClient, provider: FakeProvider
) -> None:
    r = client.post("/v1/projects", json={"name": "drone"}, headers=h("alice"))
    assert r.status_code == 201, r.text
    new = r.json()["id"]
    assert provider.created == [("alice", new)]
    client.post("/v1/projects", json={"name": "rover"}, headers=h("bob"))

    alice = {p["id"] for p in client.get("/v1/projects", headers=h("alice")).json()["projects"]}
    bob = {p["id"] for p in client.get("/v1/projects", headers=h("bob")).json()["projects"]}
    assert new in alice
    assert new not in bob
    assert client.get(f"/v1/projects/{new}", headers=h("bob")).status_code == 404


def test_a_project_that_cannot_be_registered_is_not_left_behind(
    client: TestClient, provider: FakeProvider
) -> None:
    provider.fail_create = True
    r = client.post("/v1/projects", json={"name": "orphan"}, headers=h("alice"))
    assert r.status_code == 503
    import asyncio

    from api_gateway.projects import routes as projects_routes

    names = [p.name for p in asyncio.run(projects_routes.get_project_backend().list_projects())]
    assert "orphan" not in names


def test_an_unreachable_control_plane_is_503_not_an_empty_scope() -> None:
    client = TestClient(build(FakeProvider(down=True)))
    r = client.get(f"/v1/twin/projects/{ALICE_P}/nodes", headers=h("alice"))
    assert r.status_code == 503


def test_a_provider_without_project_scope_changes_nothing() -> None:
    provider = FakeProvider(scoped=False)
    assert not supports_project_scope(provider)
    client = TestClient(build(provider))
    assert client.get("/v1/memory/insights", headers=h("alice")).status_code == 200
    assert client.get(f"/v1/twin/projects/{BOB_P}/nodes", headers=h("alice")).status_code == 200


def test_every_real_gateway_route_has_a_tenancy() -> None:
    """New routes are classified automatically; nothing is left unknown."""
    from api_gateway.server import create_app

    table = classify_routes(create_app().router.routes)
    assert table, "no routes found"
    assert all(isinstance(v, RouteTenancy) for v in table.values())
    assert table[("GET", "/v1/projects")] is RouteTenancy.ACCOUNT_LIST
    assert table[("POST", "/v1/projects")] is RouteTenancy.ACCOUNT_CREATE
    assert table[("GET", "/v1/projects/{project_id}")] is RouteTenancy.PROJECT_PATH
    # Writes to shared reference data are never open to every account.
    assert not any(
        t is RouteTenancy.SHARED_READ and m not in ("GET", "HEAD") for (m, _), t in table.items()
    )


def test_the_deployments_own_services_are_unrestricted(client: TestClient) -> None:
    assert client.get("/v1/memory/insights", headers=h("svc")).status_code == 200
    assert client.get(f"/v1/twin/projects/{BOB_P}/nodes", headers=h("svc")).status_code == 200
