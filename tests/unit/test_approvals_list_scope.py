"""The approval inbox on a gateway shared by several accounts.

The dashboard's pending badge and notifier ask ``GET /v1/approvals`` with no
project. On a shared gateway that was a 400 every few seconds (the route was
classified as needing a project), so a person never saw a waiting decision.
It is now answered and filtered to the caller's projects.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals import routes, service
from api_gateway.approvals.schemas import ApprovalItem
from api_gateway.auth.middleware import AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.scope import ProjectScope, RouteTenancy, classify_routes

MINE = "11111111-1111-1111-1111-111111111111"
THEIRS = "22222222-2222-2222-2222-222222222222"

ITEMS = [
    ApprovalItem(id="a-mine", kind="gate", status="pending", title="mine", project_id=MINE),
    ApprovalItem(id="a-theirs", kind="gate", status="pending", title="theirs", project_id=THEIRS),
    ApprovalItem(id="a-none", kind="gate", status="pending", title="no project"),
]


class Provider:
    name = "fake"

    def __init__(self, scope: ProjectScope) -> None:
        self.scope = scope

    async def verify(self, token: str) -> Principal:
        return Principal(subject="user-1")

    async def project_scope(self, principal: Principal) -> ProjectScope:
        return self.scope

    async def project_created(self, principal: Principal, project_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _items(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def list_items(app: Any) -> list[ApprovalItem]:
        return list(ITEMS)

    monkeypatch.setattr(service, "list_items", list_items)
    monkeypatch.setattr(service, "for_caller", lambda item, surface: item)
    yield


def _client(scope: ProjectScope) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.add_middleware(AuthMiddleware, provider=Provider(scope))
    return TestClient(app, headers={"Authorization": "Bearer t"})


def test_the_inbox_without_a_project_lists_only_the_callers() -> None:
    r = _client(ProjectScope(frozenset({MINE}))).get("/v1/approvals", params={"status": "pending"})
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == ["a-mine"]


def test_another_accounts_project_is_not_found() -> None:
    r = _client(ProjectScope(frozenset({MINE}))).get("/v1/approvals", params={"project_id": THEIRS})
    assert r.status_code == 404


def test_the_callers_project_is_listed() -> None:
    r = _client(ProjectScope(frozenset({MINE}))).get("/v1/approvals", params={"project_id": MINE})
    assert [i["id"] for i in r.json()["items"]] == ["a-mine"]


def test_the_deployments_services_see_everything() -> None:
    r = _client(ProjectScope(unrestricted=True)).get("/v1/approvals")
    assert {i["id"] for i in r.json()["items"]} == {"a-mine", "a-theirs", "a-none"}


def test_the_list_route_is_an_account_list() -> None:
    app = FastAPI()
    app.include_router(routes.router)
    assert classify_routes(app.routes)[("GET", "/v1/approvals")] is RouteTenancy.ACCOUNT_LIST
