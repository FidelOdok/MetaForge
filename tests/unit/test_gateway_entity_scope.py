"""Routes that name an entity are confined by the project that owns it
(FORGE-872).

Driven through the real :class:`AuthMiddleware` with fake owner lookups, on a
small app built from the real route shapes. The last test reads every API call
the dashboard makes and fails when one reaches a route a shared gateway still
refuses, unless that route is on the reviewed list of known gaps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api_gateway.auth import ownership
from api_gateway.auth.middleware import AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import InvalidToken
from api_gateway.auth.scope import ProjectScope, RouteTenancy, classify_routes

MINE = "11111111-1111-1111-1111-111111111111"
THEIRS = "22222222-2222-2222-2222-222222222222"
MY_NODE = "aaaaaaaa-0000-0000-0000-000000000001"
THEIR_NODE = "bbbbbbbb-0000-0000-0000-000000000002"
ORPHAN_NODE = "cccccccc-0000-0000-0000-000000000003"  # no project at all
MY_RUN, THEIR_RUN = "run-mine", "run-theirs"

OWNERS = {
    "twin_node": {MY_NODE: MINE, THEIR_NODE: THEIRS, ORPHAN_NODE: None},
    "run": {MY_RUN: MINE, THEIR_RUN: THEIRS},
}


class Provider:
    name = "fake"

    async def verify(self, token: str) -> Principal:
        if token != "alice":
            raise InvalidToken("unknown")
        return Principal(subject=token)

    async def project_scope(self, principal: Principal) -> ProjectScope:
        return ProjectScope(frozenset({MINE}))

    async def project_created(self, principal: Principal, project_id: str) -> None:
        pass

    async def aclose(self) -> None:
        pass


class RunBody(BaseModel):
    request: dict = {}
    start: bool = True


class ExportBody(BaseModel):
    node_id: str
    link_name: str = "base"


class JobBody(BaseModel):
    machine_id: str
    work_product_id: str


class ThreadBody(BaseModel):
    scope_kind: str
    scope_entity_id: str
    title: str = ""


def build() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/twin/nodes/{node_id}")
    async def node(node_id: str) -> dict:
        return {"node": node_id}

    @app.get("/v1/twin/projects/{project_id}/nodes/{node_id}")
    async def nested(project_id: str, node_id: str) -> dict:
        return {"node": node_id}

    @app.get("/v1/runs/{run_id}")
    async def run(run_id: str) -> dict:
        return {"run": run_id}

    @app.post("/v1/runs")
    async def start_run(body: RunBody) -> dict:
        return {"request": body.request}

    @app.get("/v1/twin/interference-check")
    async def interference(work_product_id_a: str, work_product_id_b: str) -> dict:
        return {}

    @app.post("/v1/cad-export/urdf")
    async def urdf(body: ExportBody) -> dict:
        return {"node": body.node_id}

    @app.post("/v1/library/jobs")
    async def job(body: JobBody) -> dict:
        return {}

    @app.get("/v1/chat/threads")
    async def threads(scope_kind: str | None = None, entity_id: str | None = None) -> dict:
        return {"threads": []}

    @app.post("/v1/chat/threads")
    async def new_thread(body: ThreadBody) -> dict:
        return {"scope": body.scope_entity_id}

    @app.post("/v1/robot/joint-loads")
    async def joint_loads() -> dict:
        return {"loads": []}

    provider = Provider()
    app.state.auth_provider = provider
    app.add_middleware(AuthMiddleware, provider=provider)
    return app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def fake(kind: str):  # type: ignore[no-untyped-def]
        async def lookup(app: object, entity_id: str) -> str | None:
            return OWNERS[kind].get(entity_id)

        return lookup

    for kind in OWNERS:
        monkeypatch.setitem(ownership.OWNER_LOOKUPS, kind, fake(kind))
    return TestClient(build())


H = {"Authorization": "Bearer alice"}


def test_an_entity_in_my_project_is_answered(client: TestClient) -> None:
    assert client.get(f"/v1/twin/nodes/{MY_NODE}", headers=H).status_code == 200
    assert client.get(f"/v1/runs/{MY_RUN}", headers=H).status_code == 200


@pytest.mark.parametrize("node", [THEIR_NODE, ORPHAN_NODE, "dddddddd-0000-0000-0000-00000000000d"])
def test_another_accounts_orphaned_or_unknown_entity_is_not_found(
    client: TestClient, node: str
) -> None:
    assert client.get(f"/v1/twin/nodes/{node}", headers=H).status_code == 404


def test_an_entity_nested_under_my_project_must_also_be_mine(client: TestClient) -> None:
    ok = client.get(f"/v1/twin/projects/{MINE}/nodes/{MY_NODE}", headers=H)
    assert ok.status_code == 200
    smuggled = client.get(f"/v1/twin/projects/{MINE}/nodes/{THEIR_NODE}", headers=H)
    assert smuggled.status_code == 404


def test_every_entity_a_request_names_is_checked(client: TestClient) -> None:
    url = "/v1/twin/interference-check?work_product_id_a={}&work_product_id_b={}"
    assert client.get(url.format(MY_NODE, MY_NODE), headers=H).status_code == 200
    assert client.get(url.format(MY_NODE, THEIR_NODE), headers=H).status_code == 404


def test_an_entity_in_the_body_is_checked_and_the_body_still_arrives(client: TestClient) -> None:
    r = client.post("/v1/cad-export/urdf", json={"node_id": MY_NODE}, headers=H)
    assert r.status_code == 200 and r.json() == {"node": MY_NODE}
    r = client.post("/v1/cad-export/urdf", json={"node_id": THEIR_NODE}, headers=H)
    assert r.status_code == 404


def test_a_run_request_is_checked_by_the_project_it_carries(client: TestClient) -> None:
    for request in ({"project_id": MINE}, {"project": MINE}, {"arguments": {"project_id": MINE}}):
        assert client.post("/v1/runs", json={"request": request}, headers=H).status_code == 200
    assert (
        client.post("/v1/runs", json={"request": {"project_id": THEIRS}}, headers=H).status_code
        == 404
    )
    assert client.post("/v1/runs", json={"request": {}}, headers=H).status_code == 400


def test_writes_under_shared_libraries_stay_refused_even_naming_my_work(
    client: TestClient,
) -> None:
    body = {"machine_id": "printer-1", "work_product_id": MY_NODE}
    assert client.post("/v1/library/jobs", json=body, headers=H).status_code == 403


def test_chat_threads_are_scoped_by_what_they_are_about(client: TestClient) -> None:
    q = "/v1/chat/threads?scope_kind={}&entity_id={}"
    assert client.get(q.format("project", MINE), headers=H).status_code == 200
    assert client.get(q.format("project", THEIRS), headers=H).status_code == 404
    assert client.get(q.format("digital-twin-node", MY_NODE), headers=H).status_code == 200
    assert client.get(q.format("digital-twin-node", THEIR_NODE), headers=H).status_code == 404
    # Scopes with no owner, and listing every thread, are refused.
    assert client.get(q.format("session", "s-1"), headers=H).status_code == 404
    assert client.get("/v1/chat/threads", headers=H).status_code == 400
    body = {"scope_kind": "project", "scope_entity_id": THEIRS}
    assert client.post("/v1/chat/threads", json=body, headers=H).status_code == 404


def test_stateless_computation_needs_no_project(client: TestClient) -> None:
    assert client.post("/v1/robot/joint-loads", headers=H).status_code == 200


def test_a_lookup_that_fails_is_503_not_a_leak(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(app: object, entity_id: str) -> str | None:
        raise RuntimeError("twin unavailable")

    monkeypatch.setitem(ownership.OWNER_LOOKUPS, "twin_node", broken)
    assert client.get(f"/v1/twin/nodes/{MY_NODE}", headers=H).status_code == 503


# ── The real lookups ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_scopes_resolve_only_when_they_have_an_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def node_owner(app: object, node_id: str) -> str | None:
        return MINE if node_id == MY_NODE else None

    monkeypatch.setitem(ownership.OWNER_LOOKUPS, "twin_node", node_owner)
    assert await ownership.chat_scope_project(None, "project", MINE) == MINE
    assert await ownership.chat_scope_project(None, "digital-twin-node", MY_NODE) == MINE
    assert await ownership.chat_scope_project(None, "session", "x") is None
    assert await ownership.chat_scope_project(None, "project", "") is None


@pytest.mark.asyncio
async def test_agent_sessions_and_twin_nodes_resolve_from_their_stores() -> None:
    class Session:
        project_id = MINE

    class Store:
        async def get_session(self, session_id: str) -> Session | None:
            return Session() if session_id == "s-1" else None

    class App:
        class state:  # noqa: N801
            agent_session_store = Store()

    assert await ownership.agent_session_project(App(), "s-1") == MINE
    assert await ownership.agent_session_project(App(), "s-2") is None
    assert await ownership.twin_node_project(None, "not-a-uuid") is None


# ── What the dashboard reaches ───────────────────────────────────────

#: Routes the dashboard calls that a shared gateway still refuses, each for a
#: stated reason. Shrink this list; never grow it without one.
REVIEWED_GAPS = {
    # Per-account settings are stored per gateway today; pilot accounts use
    # the deployment's own model keys.
    ("POST", "/v1/harness/credentials"),
    ("DELETE", "/v1/harness/credentials/{provider}"),
    ("PUT", "/v1/harness/selection"),
    # Stores that do not record a project yet.
    ("POST", "/v1/assistant/proposals/{change_id}/decide"),
    ("POST", "/v1/cad-export/ros2-launch"),
    ("GET", "/v1/cad-export/sessions/{session_id}"),
    ("GET", "/v1/cad-export/sessions/{session_id}/joints"),
    ("GET", "/v1/twin/baselines/diff"),
    ("GET", "/v1/twin/baselines/{baseline_id}"),
    ("GET", "/v1/simulation/results/{result_id}/field"),
    ("POST", "/v1/simulation/named-faces"),
    ("POST", "/v1/constraint/synthesize"),
    ("POST", "/v1/requirements/{requirement_id}/fix"),
    ("GET", "/v1/design-loop/{loop_id}"),
    ("POST", "/v1/design-loop/{loop_id}/approve"),
    ("POST", "/v1/design-flows/versions"),
    ("GET", "/v1/design-flows/versions/{version_id}"),
    ("GET", "/v1/knowledge/{entry_id}"),
    ("GET", "/v1/evals"),
}

_CALL = re.compile(r"apiClient\.(get|post|put|patch|delete)[^(]*\(\s*[`'\"](/[^`'\"?]*)")


def _dashboard_calls() -> set[tuple[str, str]]:
    root = Path(__file__).resolve().parents[2] / "dashboard" / "src"
    calls: set[tuple[str, str]] = set()
    for f in root.rglob("*.ts*"):
        if "__tests__" in f.parts or ".test." in f.name:
            continue
        for m in _CALL.finditer(f.read_text(encoding="utf-8")):
            path = re.sub(r"\$\{[^}]+\}", "X", m.group(2)).rstrip("/")
            calls.add((m.group(1).upper(), path.removeprefix("/v1")))
    return calls


def test_every_route_the_dashboard_calls_is_scoped_or_a_reviewed_gap() -> None:
    from api_gateway.server import create_app

    table = classify_routes(create_app().router.routes)
    blocked = {k for k, v in table.items() if v is RouteTenancy.BLOCKED}
    patterns = [
        (m, p, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p.removeprefix("/v1")) + "$"))
        for m, p in blocked
    ]
    calls = _dashboard_calls()
    assert len(calls) > 100, "the dashboard call scan found almost nothing; is the regex stale?"
    reached = {(m, p) for m, p, rx in patterns for cm, cp in calls if cm == m and rx.match(cp)}
    unreviewed = sorted(reached - REVIEWED_GAPS)
    assert not unreviewed, (
        "the dashboard calls routes a shared gateway refuses; scope them "
        f"(api_gateway/auth/scope.py) or review them into REVIEWED_GAPS: {unreviewed}"
    )
    stale = sorted(REVIEWED_GAPS - reached)
    assert not stale, f"no longer refused or no longer called; drop from REVIEWED_GAPS: {stale}"
