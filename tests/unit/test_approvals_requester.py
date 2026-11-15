"""A person sees and decides their own approvals that have no project yet.

Observed on hosted staging: a person signed in to the MCP sidecar with their
own account called ``project.create``. The write was held for approval, and
the hold had no project (the project does not exist until it is approved).
Account scoping drops every approval with no project, and the hold did not
record who asked, so ``approvals.list`` showed nothing, the dashboard hid it
too, and it expired with nobody able to decide it.

The hold now records the requester the MCP server verified, the gateway takes
that only from the deployment's own service (a person calling directly is
recorded as themselves), and a scoped caller sees and decides an approval with
no project when, and only when, they are the one who asked. Nothing changes
for an unscoped deployment.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals.routes import router as approvals_router
from api_gateway.auth.approver import is_service_caller, requester_from_request
from api_gateway.auth.middleware import AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import InvalidToken
from api_gateway.auth.scope import ProjectScope
from api_gateway.chat import tool_approvals
from api_gateway.runs import routes as run_routes
from mcp_core.context import McpCallContext, is_requester, with_context
from mcp_core.guardrails import ApprovalAsk, Caller
from metaforge.mcp import server as server_mod
from metaforge.mcp.project_scope import (
    McpProjectScoper,
    ProjectScopeError,
    sidecar_owner_lookups,
)
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.design_flow.approvals import list_pending

ALICE = "6f1c2b4e-0000-4000-8000-000000000001"
BOB = "6f1c2b4e-0000-4000-8000-000000000002"
MINE = "11111111-1111-1111-1111-111111111111"
THEIRS = "22222222-2222-2222-2222-222222222222"
SESSION = UUID("11111111-2222-4333-8444-555555555555")

SERVICE = Principal(subject="service:metaforge", role="service")


@pytest.fixture(autouse=True)
def _stores() -> Iterator[None]:
    tool_approvals.reset_approval_store()
    run_routes.reset_run_store()
    yield
    tool_approvals.reset_approval_store()
    run_routes.reset_run_store()


# ── the gateway ──────────────────────────────────────────────────────


class ScopedProvider:
    """Alice and Bob each have one project; the service is unrestricted."""

    name = "fake"

    async def verify(self, token: str) -> Principal:
        if token == "svc":
            return SERVICE
        if token == "alice":
            return Principal(subject=ALICE)
        if token == "bob":
            return Principal(subject=BOB)
        if token == "bob-claims-service":
            # A person's token carrying a ``service`` role claim.
            return Principal(subject=BOB, role="service")
        raise InvalidToken("unknown")

    async def project_scope(self, principal: Principal) -> ProjectScope:
        if principal is SERVICE:
            return ProjectScope(unrestricted=True)
        return ProjectScope(frozenset({MINE if principal.subject == ALICE else THEIRS}))

    async def project_created(self, principal: Principal, project_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class TeamProvider(ScopedProvider):
    """An authenticated single-team gateway: no project scope at all."""

    project_scope = None  # type: ignore[assignment]
    project_created = None  # type: ignore[assignment]


def _gateway(provider: Any | None) -> FastAPI:
    app = FastAPI()
    app.include_router(tool_approvals.router)
    app.include_router(approvals_router)
    if provider is not None:
        app.add_middleware(AuthMiddleware, provider=provider)
    return app


def _as(app: FastAPI, token: str | None) -> TestClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app, headers=headers)


def _hold(
    client: TestClient, requested_by: str | None, *, project: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tool": "project.create",
        "arguments": {"name": "Arm"},
        "reason": "writes; held for approval (remote caller)",
        "caller": "remote",
        "project": project,
    }
    if requested_by is not None:
        body["requested_by"] = requested_by
    reply = client.post("/v1/chat/tool_approvals", json=body)
    assert reply.status_code == 201, reply.text
    result: dict[str, Any] = reply.json()
    return result


def _ids(client: TestClient, **params: str) -> list[str]:
    reply = client.get("/v1/approvals", params=params)
    assert reply.status_code == 200, reply.text
    # Only held tool calls: other kinds live in stores other tests fill.
    return [i["id"] for i in reply.json()["items"] if i["id"].startswith("tool:")]


class TestTheGatewayRecordsWhoAsked:
    def test_the_service_relays_the_person_its_sign_in_verified(self) -> None:
        app = _gateway(ScopedProvider())
        held = _hold(_as(app, "svc"), f"sub:{ALICE}")
        assert held["request"]["requested_by"] == f"sub:{ALICE}"
        assert held["request"]["requested_by_verified"] is True
        item = _as(app, "svc").get(f"/v1/approvals/tool:{held['id']}").json()
        assert item["requester"] == f"sub:{ALICE}"
        assert item["requested_by"] == f"sub:{ALICE}"

    def test_a_person_calling_directly_is_recorded_as_themselves(self) -> None:
        app = _gateway(TeamProvider())
        held = _hold(_as(app, "bob"), f"sub:{ALICE}")
        assert held["request"]["requested_by"] == f"sub:{BOB}"
        assert held["request"]["requested_by_verified"] is True

    def test_the_service_role_alone_is_not_the_service_on_a_shared_gateway(self) -> None:
        def request(principal: Principal, scope: ProjectScope | None) -> Any:
            state = SimpleNamespace(principal=principal)
            if scope is not None:
                state.project_scope = scope
            return SimpleNamespace(state=state, url=SimpleNamespace(path="/v1/x"))

        impostor = Principal(subject=BOB, role="service")
        confined = request(impostor, ProjectScope(frozenset({THEIRS})))
        assert is_service_caller(confined) is False
        assert requester_from_request(confined, f"sub:{ALICE}") == (f"sub:{BOB}", True)
        assert is_service_caller(request(SERVICE, ProjectScope(unrestricted=True))) is True
        assert requester_from_request(request(SERVICE, None), f"sub:{ALICE}") == (
            f"sub:{ALICE}",
            True,
        )

    def test_unauthenticated_the_value_is_only_a_label(self) -> None:
        app = _gateway(None)
        held = _hold(_as(app, None), f"sub:{ALICE}")
        assert held["request"]["requested_by"] == f"sub:{ALICE}"
        assert held["request"]["requested_by_verified"] is False
        item = _as(app, None).get(f"/v1/approvals/tool:{held['id']}").json()
        assert item["requester"] is None
        assert item["requested_by"] == f"sub:{ALICE}"

    def test_without_a_requester_the_trust_level_is_shown_as_before(self) -> None:
        app = _gateway(None)
        held = _hold(_as(app, None), None)
        assert "requested_by" not in held["request"]
        item = _as(app, None).get(f"/v1/approvals/tool:{held['id']}").json()
        assert item["requested_by"] == "remote"
        assert item["requester"] is None

    def test_the_request_field_is_bounded(self) -> None:
        reply = _as(_gateway(None), None).post(
            "/v1/chat/tool_approvals",
            json={"tool": "project.create", "reason": "r", "requested_by": "x" * 201},
        )
        assert reply.status_code == 422


class TestTheRequesterSeesTheirOwnRequest:
    def test_a_scoped_list_shows_my_request_and_hides_it_from_another_person(self) -> None:
        app = _gateway(ScopedProvider())
        mine = _hold(_as(app, "svc"), f"sub:{ALICE}")["id"]
        bare = _hold(_as(app, "svc"), ALICE)["id"]  # a bare subject names her too
        bobs = _hold(_as(app, "svc"), f"sub:{BOB}")["id"]
        nobodys = _hold(_as(app, "svc"), None)["id"]

        assert set(_ids(_as(app, "alice"))) == {f"tool:{mine}", f"tool:{bare}"}
        assert _ids(_as(app, "bob")) == [f"tool:{bobs}"]
        assert f"tool:{nobodys}" not in _ids(_as(app, "alice")) + _ids(_as(app, "bob"))

    def test_a_project_still_decides_and_the_requester_gains_nothing(self) -> None:
        app = _gateway(ScopedProvider())
        in_bobs_project = _hold(_as(app, "svc"), f"sub:{ALICE}", project=THEIRS)["id"]
        in_mine = _hold(_as(app, "svc"), f"sub:{BOB}", project=MINE)["id"]
        alice = _as(app, "alice")
        assert _ids(alice) == [f"tool:{in_mine}"]
        assert alice.get(f"/v1/approvals/tool:{in_bobs_project}").status_code == 404
        assert alice.get(f"/v1/chat/tool_approvals/{in_bobs_project}").status_code == 404

    def test_an_unverified_label_never_counts(self) -> None:
        app = _gateway(ScopedProvider())
        # Written while the gateway ran without authentication.
        held = tool_approvals.create_hold(
            tool="project.create",
            arguments={},
            reason="r",
            requested_by=f"sub:{ALICE}",
            requested_by_verified=False,
        )
        alice = _as(app, "alice")
        assert _ids(alice) == []
        assert alice.get(f"/v1/approvals/tool:{held.id}").status_code == 404

    def test_unscoped_count_counts_my_request_when_a_project_is_asked_for(self) -> None:
        app = _gateway(ScopedProvider())
        _hold(_as(app, "svc"), f"sub:{ALICE}")
        _hold(_as(app, "svc"), f"sub:{BOB}")
        reply = _as(app, "alice").get("/v1/approvals", params={"project_id": MINE}).json()
        assert reply["items"] == []
        assert reply["unscoped_count"] == 1

    def test_the_requester_can_get_and_decide_it_and_another_person_cannot(self) -> None:
        app = _gateway(ScopedProvider())
        run_id = _hold(_as(app, "svc"), f"sub:{ALICE}")["id"]
        approval = f"tool:{run_id}"
        alice, bob = _as(app, "alice"), _as(app, "bob")
        impostor = _as(app, "bob-claims-service")

        for who in (bob, impostor):
            assert who.get(f"/v1/approvals/{approval}").status_code == 404
            assert who.get(f"/v1/chat/tool_approvals/{run_id}").status_code == 404
            refused = who.post(f"/v1/approvals/{approval}/decision", json={"decision": "approve"})
            assert refused.status_code == 404
            inline = who.post(
                f"/v1/approvals/{approval}/inline-decision", json={"decision": "approve"}
            )
            assert inline.status_code == 404
            direct = who.post(f"/v1/chat/tool_approvals/{run_id}", json={"decision": "approve"})
            assert direct.status_code == 404

        assert alice.get(f"/v1/approvals/{approval}").json()["status"] == "pending"
        assert alice.get(f"/v1/chat/tool_approvals/{run_id}").status_code == 200
        decided = alice.post(f"/v1/approvals/{approval}/decision", json={"decision": "approve"})
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        assert decided.json()["decision"]["approver"] == f"user:{ALICE}"

    def test_the_requester_can_decide_through_the_tool_approvals_route(self) -> None:
        app = _gateway(ScopedProvider())
        run_id = _hold(_as(app, "svc"), f"sub:{ALICE}")["id"]
        reply = _as(app, "alice").post(
            f"/v1/chat/tool_approvals/{run_id}", json={"decision": "reject"}
        )
        assert reply.status_code == 200, reply.text

    def test_nothing_changes_when_unscoped(self) -> None:
        for app, token in ((_gateway(None), None), (_gateway(TeamProvider()), "bob")):
            tool_approvals.reset_approval_store()
            alices = _hold(_as(app, "svc" if token else None), f"sub:{ALICE}")["id"]
            nobodys = _hold(_as(app, "svc" if token else None), None)["id"]
            assert set(_ids(_as(app, token))) == {f"tool:{alices}", f"tool:{nobodys}"}
            assert _as(app, token).get(f"/v1/approvals/tool:{alices}").status_code == 200


# ── the MCP hold ─────────────────────────────────────────────────────


def _spy(ran: list[str]) -> McpToolServer:
    class _Spy(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="project", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="project.create",
                    adapter_id="project",
                    name="create",
                    description="stub",
                    capability="test",
                ),
                self._handle,
            )

        async def _handle(self, args: dict[str, Any]) -> dict[str, Any]:
            ran.append("project.create")
            return {"ok": True}

    return _Spy()


def _signed_in(subject: str = ALICE) -> McpCallContext:
    return McpCallContext(actor_id=f"sub:{subject}", actor_verified=True, session_id=SESSION)


async def _create(server: UnifiedMcpServer, ctx: McpCallContext) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "project.create", "arguments": {"name": "Arm"}},
    }
    with with_context(ctx):
        reply: dict[str, Any] = json.loads(await server.handle_request(json.dumps(body)))
    return reply


@pytest.mark.asyncio
class TestTheMcpHoldCarriesTheRequester:
    @pytest.fixture(autouse=True)
    def _fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
        monkeypatch.delenv("METAFORGE_TOOL_APPROVAL_TTL_S", raising=False)

    async def _held_request(self, ctx: McpCallContext) -> dict[str, Any]:
        app = _gateway(None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as http:
            ran: list[str] = []
            server = UnifiedMcpServer(
                [_spy(ran)],
                caller=Caller.REMOTE,
                approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
                approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
            )
            reply = await _create(server, ctx)
            assert ran == []
            assert reply["result"]["isError"] is True, reply
            runs = (await http.get("/v1/chat/tool_approvals")).json()["runs"]
        assert len(runs) == 1
        request: dict[str, Any] = runs[0]["request"]
        return request

    async def test_a_signed_in_persons_hold_names_them(self) -> None:
        request = await self._held_request(_signed_in())
        assert request["tool"] == "project.create"
        assert request["project"] is None
        assert request["requested_by"] == f"sub:{ALICE}"

    async def test_a_typed_in_actor_names_nobody(self) -> None:
        typed = McpCallContext(actor_id=f"sub:{ALICE}", actor_verified=False, session_id=SESSION)
        request = await self._held_request(typed)
        assert "requested_by" not in request

    async def test_the_remote_gate_and_the_in_process_ledger_record_it_too(self) -> None:
        from api_gateway.mcp_approvals import InProcessApprovalLedger

        ask = ApprovalAsk(
            tool_id="project.create",
            arguments={},
            caller=Caller.REMOTE,
            reason="r",
            requested_by=f"sub:{ALICE}",
        )
        held = await InProcessApprovalLedger().open_hold(ask, route="dashboard")
        request = tool_approvals.get_approval_store().get(held).request
        assert request["requested_by"] == f"sub:{ALICE}"
        assert request["requested_by_verified"] is True

        posted: list[dict[str, Any]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            posted.append(json.loads(req.content))
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            gate = build_remote_approval_gate("http://gateway.test", client=http)
            await gate(ask)
        assert posted[0]["requested_by"] == f"sub:{ALICE}"


# ── the sidecar ──────────────────────────────────────────────────────


class SidecarProvider:
    async def project_scope(self, principal: Principal) -> ProjectScope:
        return ProjectScope(frozenset({MINE if principal.subject == ALICE else THEIRS}))

    async def project_created(self, principal: Principal, project_id: str) -> None:
        return None


ITEMS: list[dict[str, Any]] = [
    {"id": "tool:mine", "project_id": MINE, "title": "in my project", "status": "pending"},
    {
        "id": "tool:asked",
        "project_id": None,
        "title": "my project.create",
        "status": "pending",
        "requester": f"sub:{ALICE}",
        "requested_by": f"sub:{ALICE}",
    },
    {
        "id": "tool:bobs",
        "project_id": None,
        "title": "bob's project.create",
        "status": "pending",
        "requester": f"sub:{BOB}",
    },
    {
        # Only a display label (an unverified value): never a match.
        "id": "tool:label",
        "project_id": None,
        "title": "a label",
        "status": "pending",
        "requested_by": f"sub:{ALICE}",
    },
    {
        # Has a project: the project decides, whoever asked.
        "id": "tool:theirs",
        "project_id": THEIRS,
        "title": "in bob's project",
        "status": "pending",
        "requester": f"sub:{ALICE}",
    },
]


async def _lister(project_id: str | None) -> list[dict[str, Any]]:
    return [i for i in ITEMS if project_id is None or i.get("project_id") == project_id]


class TestTheSidecarAppliesTheSameRule:
    def test_the_requester_match(self) -> None:
        assert is_requester(f"sub:{ALICE}", f"sub:{ALICE}")
        assert is_requester(ALICE, f"sub:{ALICE}")
        assert not is_requester(f"sub:{BOB}", f"sub:{ALICE}")
        assert not is_requester(None, f"sub:{ALICE}")
        assert not is_requester("", f"sub:{ALICE}")
        assert not is_requester(f"sub:{ALICE}", ALICE)  # not a signed-in actor
        assert not is_requester("sub:", "sub:")

    @pytest.mark.asyncio
    async def test_approvals_list_shows_my_request_only_to_me(self) -> None:
        alice = _signed_in().model_copy(update={"allowed_projects": frozenset({MINE})})
        with with_context(alice):
            listing = await list_pending(lister=_lister)
        assert [i["id"] for i in listing["items"]] == ["tool:mine", "tool:asked"]

        bob = _signed_in(BOB).model_copy(update={"allowed_projects": frozenset({THEIRS})})
        with with_context(bob):
            listing = await list_pending(lister=_lister)
        assert [i["id"] for i in listing["items"]] == ["tool:bobs", "tool:theirs"]

    @pytest.mark.asyncio
    async def test_unscoped_callers_see_everything_as_before(self) -> None:
        with with_context(McpCallContext()):
            listing = await list_pending(lister=_lister)
        assert len(listing["items"]) == len(ITEMS)

    @pytest.mark.asyncio
    async def test_approvals_decide_admits_only_the_requester(self) -> None:
        scoper = McpProjectScoper(
            SidecarProvider(), sidecar_owner_lookups(approvals_lister=_lister)
        )
        asked = {"approval_id": "tool:asked"}
        admitted = await scoper.admit("approvals.decide", asked, _signed_in())
        assert admitted is not None
        for other in ("tool:bobs", "tool:label", "tool:theirs", "tool:missing"):
            with pytest.raises(ProjectScopeError) as refused:
                await scoper.admit("approvals.decide", {"approval_id": other}, _signed_in())
            assert refused.value.code == "not_found"
        with pytest.raises(ProjectScopeError):
            await scoper.admit("approvals.decide", asked, _signed_in(BOB))

    @pytest.mark.asyncio
    async def test_the_approval_notice_tells_the_requester_and_nobody_else(self) -> None:
        async def notice(ctx: McpCallContext) -> str:
            server = UnifiedMcpServer([_spy([])], caller=Caller.REMOTE)
            server.attach_approval_notices(_lister)
            server.attach_project_scope(SidecarProvider())
            with with_context(ctx):
                told = await server._approval_notice("twin.get_node")  # noqa: SLF001
            return told[0] if told else ""

        mine = await notice(_signed_in())
        assert "my project.create" in mine and "bob's" not in mine and "a label" not in mine
        theirs = await notice(_signed_in(BOB))
        assert "bob's project.create" in theirs and "my project.create" not in theirs
