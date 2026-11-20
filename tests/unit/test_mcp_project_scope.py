"""MCP tool calls confined to the signed-in account's projects (FORGE-859, part B).

Part A bound each MCP token to the person who signed in (``sub:<subject>``).
Here that binding is used: when the deployment's auth provider can say which
projects a subject may touch, every tool call and resource read from that
person is checked against them, with the gateway's rules (FORGE-849): a
project outside the scope reads exactly like one that does not exist, an
entity is checked by the project that owns it, and a tool nobody classified
is refused.

Callers that are not a signed-in person (a local install, an API key, the
shared-secret login, the design-flow service) are untouched.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import AuthUnavailable, InvalidToken
from api_gateway.auth.scope import ProjectScope
from api_gateway.projects.backend import InMemoryProjectBackend
from mcp_core.context import (
    McpCallContext,
    bind_session_project,
    clear_session_project,
    with_context,
)
from mcp_core.guardrails import Caller
from metaforge.mcp.project_scope import (
    TOOL_RULES,
    McpProjectScoper,
    ToolTenancy,
    rule_for,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.design_flow.approvals import list_pending
from tool_registry.tools.project.adapter import ProjectServer

ALICE = "6f1c2b4e-0000-4000-8000-000000000001"
SESSION = UUID("11111111-2222-4333-8444-555555555555")


class Provider:
    """verify_password + project_scope + project_created, as a provider ships them."""

    def __init__(self) -> None:
        self.projects: set[str] = set()
        self.tenant: str | None = "account-a"
        self.down = False
        self.refuse_registration = False
        self.created: list[tuple[str, str]] = []
        self.asked = 0

    async def verify(self, token: str) -> Principal:  # pragma: no cover - not used here
        raise InvalidToken("no tokens in this test")

    async def verify_password(self, email: str, password: str) -> Principal:
        if (email, password) != ("alice@example.com", "pw"):
            raise InvalidToken("wrong credentials")
        return Principal(subject=ALICE, email=email)

    async def project_scope(self, principal: Principal) -> ProjectScope:
        self.asked += 1
        if self.down:
            raise AuthUnavailable("control plane unreachable")
        assert principal.subject == ALICE
        return ProjectScope(project_ids=frozenset(self.projects), tenant=self.tenant)

    async def project_created(self, principal: Principal, project_id: str) -> None:
        if self.refuse_registration:
            raise AuthUnavailable("control plane unreachable")
        self.created.append((principal.subject, project_id))
        self.projects.add(project_id)


class Twin:
    """Owner lookups read ``get_work_product(uuid).project_id``."""

    def __init__(self) -> None:
        self.nodes: dict[UUID, str | None] = {}

    def add(self, project: str | None) -> str:
        node = uuid4()
        self.nodes[node] = project
        return str(node)

    async def get_work_product(self, node_id: UUID) -> Any:
        if node_id not in self.nodes:
            return None
        return SimpleNamespace(project_id=self.nodes[node_id])


def _stub_adapter(ran: list[tuple[str, dict[str, Any]]], approvals: list[dict[str, Any]]) -> Any:
    tools = {
        "twin.get_node": ["node_id", "project_id", "item_key"],
        "twin.thread_for": ["node_id"],
        "twin.query_cypher": ["cypher"],
        "knowledge.search": ["project_id", "query"],
        "session.start": ["project_id", "agent_code", "task_type"],
        "cadquery.describe_faces": ["step_base64"],
        "made.up_tool": ["anything"],
        "approvals.list": ["project_id"],
        "library.register_material": ["name"],
        "library.standard_part": ["name"],
        "flow.capabilities": ["template", "version_id"],
    }

    class _Tools(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="stub", version="0.1.0")
            for tool, props in tools.items():
                self.register_tool(
                    ToolManifest(
                        tool_id=tool,
                        adapter_id="stub",
                        name=tool,
                        description="stub",
                        capability="test",
                        input_schema={
                            "type": "object",
                            "properties": {p: {} for p in props},
                        },
                    ),
                    self._handler(tool),
                )

        def _handler(self, tool: str) -> Any:
            async def handle(args: dict[str, Any]) -> dict[str, Any]:
                ran.append((tool, dict(args)))
                if tool == "approvals.list":

                    async def lister(project_id: str | None) -> list[dict[str, Any]]:
                        return list(approvals)

                    return await list_pending(lister=lister, project_id=args.get("project_id"))
                return {"ok": True}

            return handle

    return _Tools()


class World:
    def __init__(self) -> None:
        self.provider = Provider()
        self.backend = InMemoryProjectBackend()
        self.twin = Twin()
        self.ran: list[tuple[str, dict[str, Any]]] = []
        self.approvals: list[dict[str, Any]] = []
        self.server = UnifiedMcpServer(
            [ProjectServer(self.backend), _stub_adapter(self.ran, self.approvals)],
            caller=Caller.LOCAL,
        )
        self.server.attach_ownership_sources(twin=self.twin, project_backend=self.backend)
        self.mine = ""
        self.theirs = ""

    async def setup(self) -> None:
        self.mine = (
            await self.backend.create_project(name="Alice arm", description="", status="draft")
        ).id
        self.theirs = (
            await self.backend.create_project(name="Bob arm", description="", status="draft")
        ).id
        self.provider.projects = {self.mine}
        self.scoper = self.server.attach_project_scope(self.provider)

    async def call(
        self, tool: str, arguments: dict[str, Any], ctx: McpCallContext | None = None
    ) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        with with_context(ctx or alice()):
            reply: dict[str, Any] = json.loads(await self.server.handle_request(json.dumps(body)))
        return reply

    async def read(self, uri: str, ctx: McpCallContext | None = None) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}}
        with with_context(ctx or alice()):
            reply: dict[str, Any] = json.loads(await self.server.handle_request(json.dumps(body)))
        return reply


def alice(**update: Any) -> McpCallContext:
    """A call from Alice's MCP token: bound to her verified subject."""
    return McpCallContext(
        actor_id=f"sub:{ALICE}", actor_verified=True, session_id=SESSION
    ).model_copy(update=update)


def _payload(reply: dict[str, Any]) -> Any:
    assert "result" in reply, reply
    envelope = json.loads(reply["result"]["content"][0]["text"])
    return envelope["data"]


def _refusal(reply: dict[str, Any]) -> dict[str, Any]:
    assert "error" in reply, reply
    data: dict[str, Any] = reply["error"]["data"]
    return data


@pytest.fixture
async def world() -> World:
    w = World()
    await w.setup()
    return w


@pytest.fixture(autouse=True)
def _no_binding() -> Iterator[None]:
    clear_session_project(SESSION)
    yield
    clear_session_project(SESSION)


# ── the project argument ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_call_in_my_project_runs(world: World) -> None:
    reply = await world.call("knowledge.search", {"project_id": world.mine, "query": "motor"})
    assert _payload(reply) == {"ok": True}
    assert world.ran == [("knowledge.search", {"project_id": world.mine, "query": "motor"})]


@pytest.mark.asyncio
async def test_another_accounts_project_reads_like_one_that_does_not_exist(world: World) -> None:
    theirs = _refusal(await world.call("knowledge.search", {"project_id": world.theirs}))
    missing_id = str(uuid4())
    missing = _refusal(await world.call("knowledge.search", {"project_id": missing_id}))
    assert theirs["code"] == missing["code"] == "not_found"
    assert theirs["reason"].replace(world.theirs, "X") == missing["reason"].replace(missing_id, "X")
    assert theirs["retryable"] is False
    assert world.ran == []


@pytest.mark.asyncio
async def test_a_project_tool_needs_a_project(world: World) -> None:
    refusal = _refusal(await world.call("knowledge.search", {"query": "motor"}))
    assert refusal["code"] == "project_required"
    assert world.ran == []


# ── entities ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_entity_is_checked_by_the_project_that_owns_it(world: World) -> None:
    mine = world.twin.add(world.mine)
    theirs = world.twin.add(world.theirs)
    orphan = world.twin.add(None)
    assert _payload(await world.call("twin.thread_for", {"node_id": mine})) == {"ok": True}
    for node in (theirs, orphan, str(uuid4())):
        assert _refusal(await world.call("twin.thread_for", {"node_id": node}))["code"] == (
            "not_found"
        )
    # A node of someone else's under my own project is refused too.
    nested = await world.call("twin.get_node", {"project_id": world.mine, "node_id": theirs})
    assert _refusal(nested)["code"] == "not_found"
    assert [t for t, _ in world.ran] == ["twin.thread_for"]


@pytest.mark.asyncio
async def test_an_entity_tool_without_its_entity_is_refused(world: World) -> None:
    assert _refusal(await world.call("twin.thread_for", {}))["code"] == "entity_required"


@pytest.mark.asyncio
async def test_a_failed_lookup_is_retryable_and_not_a_leak(world: World) -> None:
    async def broken(node_id: str) -> str | None:
        raise RuntimeError("neo4j down")

    world.scoper._lookups["twin_node"] = broken  # noqa: SLF001
    refusal = _refusal(await world.call("twin.thread_for", {"node_id": str(uuid4())}))
    assert refusal["code"] == "scope_unavailable" and refusal["retryable"] is True


# ── account-level lists ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_list_open_and_get_show_only_my_projects(world: World) -> None:
    listing = _payload(await world.call("project.list", {}))
    assert [p["id"] for p in listing["projects"]] == [world.mine]
    assert listing["total"] == 1
    # "arm" matches both names; only mine is a candidate, so it opens.
    opened = _payload(await world.call("project.open", {"query": "arm"}))
    assert opened["project"]["id"] == world.mine
    assert (await world.call("project.open", {"query": "Bob"}))["result"]["isError"] is True
    assert _payload(await world.call("project.get", {"id": world.theirs})) is None
    assert _payload(await world.call("project.get", {"name": "Bob arm"})) is None


@pytest.mark.asyncio
async def test_approvals_list_shows_only_my_projects(world: World) -> None:
    world.approvals.extend(
        [
            {"id": "a1", "project_id": world.mine, "title": "mine"},
            {"id": "a2", "project_id": world.theirs, "title": "theirs"},
            {"id": "a3", "project_id": None, "title": "nobody's"},
        ]
    )
    listing = _payload(await world.call("approvals.list", {}))
    assert [i["id"] for i in listing["items"]] == ["a1"]
    asked = _refusal(await world.call("approvals.list", {"project_id": world.theirs}))
    assert asked["code"] == "not_found"


# ── what needs no project, and what is refused ───────────────────────


@pytest.mark.asyncio
async def test_a_stateless_tool_needs_no_project(world: World) -> None:
    assert _payload(await world.call("cadquery.describe_faces", {"step_base64": "x"})) == {
        "ok": True
    }


@pytest.mark.asyncio
async def test_unclassified_and_blocked_tools_are_refused_by_name(world: World) -> None:
    unknown = _refusal(await world.call("made.up_tool", {}))
    assert unknown["code"] == "tool_not_scoped"
    assert unknown["tool_id"] == "made.up_tool"
    blocked = _refusal(await world.call("twin.query_cypher", {"cypher": "MATCH (n) RETURN n"}))
    assert blocked["code"] == "tool_not_scoped"
    assert "whole twin" in blocked["reason"]
    assert world.ran == []


@pytest.mark.asyncio
async def test_an_argument_that_cannot_be_traced_is_refused(world: World) -> None:
    assert _payload(await world.call("flow.capabilities", {"template": "lamp"})) == {"ok": True}
    refusal = _refusal(await world.call("flow.capabilities", {"version_id": "v1"}))
    assert refusal["code"] == "tool_not_scoped"


@pytest.mark.asyncio
async def test_library_writes_need_a_tenant_wherever_the_library_is(world: World) -> None:
    # A library served by the gateway is admitted like an in-process one: the
    # account's view travels with each call (remote_library.library_view_headers).
    remote = McpProjectScoper(world.provider, {}, library_in_process=False)
    world.server._project_scoper = remote  # noqa: SLF001
    assert _payload(await world.call("library.register_material", {"name": "x"})) == {"ok": True}
    assert _payload(await world.call("library.standard_part", {"name": "x"})) == {"ok": True}
    world.provider.tenant = None
    remote.forget(ALICE)
    assert _refusal(await world.call("library.register_material", {"name": "x"}))["code"] == (
        "library_read_only"
    )


@pytest.mark.asyncio
async def test_library_calls_see_the_callers_library_view(world: World) -> None:
    from twin_core.library.viewer import current_viewer

    seen: list[str | None] = []

    async def record(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(current_viewer())
        return {"ok": True}

    adapter = world.server._tool_index["library.register_material"]  # noqa: SLF001
    adapter._tools["library.register_material"].handler = record  # noqa: SLF001
    await world.call("library.register_material", {"name": "x"})
    assert seen == ["account-a"]


# ── the session's bound project ──────────────────────────────────────


@pytest.mark.asyncio
async def test_binding_a_session_to_another_accounts_project_is_refused(world: World) -> None:
    refusal = _refusal(
        await world.call(
            "session.start", {"project_id": world.theirs, "agent_code": "x", "task_type": "y"}
        )
    )
    assert refusal["code"] == "not_found"
    assert world.ran == []


@pytest.mark.asyncio
async def test_a_bound_project_is_checked_and_named_to_the_tool(world: World) -> None:
    bind_session_project(SESSION, UUID(world.mine))
    await world.call("knowledge.search", {"query": "motor"})
    # The handler is told the project rather than left to treat none as all.
    assert world.ran == [("knowledge.search", {"query": "motor", "project_id": world.mine})]

    # A session bound elsewhere (a header, another person's session id)
    # reaches nothing, whatever the tool.
    bind_session_project(SESSION, UUID(world.theirs))
    for tool, args in (
        ("knowledge.search", {"query": "motor"}),
        ("cadquery.describe_faces", {"step_base64": "x"}),
    ):
        assert _refusal(await world.call(tool, args))["code"] == "not_found"


# ── project.create ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_project_is_registered_to_its_creator(world: World) -> None:
    created = _payload(await world.call("project.create", {"name": "Alice drone"}))
    assert world.provider.created == [(ALICE, created["id"])]
    listing = _payload(await world.call("project.list", {}))
    assert {p["id"] for p in listing["projects"]} == {world.mine, created["id"]}


@pytest.mark.asyncio
async def test_a_project_that_cannot_be_registered_is_not_left_behind(world: World) -> None:
    world.provider.refuse_registration = True
    refusal = _refusal(await world.call("project.create", {"name": "Orphan"}))
    assert refusal["code"] == "registration_failed" and refusal["retryable"] is True
    names = {p.name for p in await world.backend.list_projects()}
    assert "Orphan" not in names


# ── resources ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resources_of_another_accounts_project_are_not_found(world: World) -> None:
    reply = await world.read(f"metaforge://twin/brief/{world.theirs}")
    assert _refusal(reply)["code"] == "not_found"
    unknown = await world.read("metaforge://knowledge/sources")
    assert _refusal(unknown)["code"] == "tool_not_scoped"


# ── the provider ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unreachable_provider_refuses_retryably(world: World) -> None:
    world.provider.down = True
    refusal = _refusal(await world.call("cadquery.describe_faces", {"step_base64": "x"}))
    assert refusal["code"] == "scope_unavailable" and refusal["retryable"] is True


@pytest.mark.asyncio
async def test_the_scope_is_cached_briefly(world: World) -> None:
    for _ in range(3):
        await world.call("cadquery.describe_faces", {"step_base64": "x"})
    assert world.provider.asked == 1


@pytest.mark.asyncio
async def test_a_token_not_bound_to_a_person_reaches_nothing(world: World) -> None:
    # A token issued before the identity login and persisted across a restart
    # carries a label, not a subject.
    reply = await world.call(
        "cadquery.describe_faces", {"step_base64": "x"}, alice(actor_id="user:alice")
    )
    assert _refusal(reply)["code"] == "not_signed_in"


@pytest.mark.asyncio
async def test_an_unrestricted_scope_changes_nothing(world: World) -> None:
    async def everything(principal: Principal) -> ProjectScope:
        return ProjectScope(unrestricted=True)

    world.provider.project_scope = everything  # type: ignore[method-assign]
    assert _payload(await world.call("twin.query_cypher", {"cypher": "x"})) == {"ok": True}


# ── callers that are not a signed-in person ──────────────────────────


@pytest.mark.parametrize("caller", ["shared-secret", "api-key", "service"])
@pytest.mark.asyncio
async def test_callers_that_are_not_a_signed_in_person_are_unaffected(
    world: World, caller: str
) -> None:
    ctx = {
        # The shared-secret login and the X-MetaForge-Actor header: a label.
        "shared-secret": McpCallContext(actor_id="user:bob", session_id=SESSION),
        # An API key: authorised, attributable to nobody.
        "api-key": McpCallContext(session_id=SESSION),
        # The design-flow service caller (FORGE-487), in its run's project.
        "service": McpCallContext(
            actor_id="service:design-flow:run-1",
            service_verified=True,
            run_id="run-1",
            project_id=UUID(world.theirs),
            session_id=SESSION,
        ),
    }[caller]
    reply = await world.call("knowledge.search", {"project_id": world.theirs}, ctx)
    assert _payload(reply) == {"ok": True}
    listing = _payload(await world.call("project.list", {}, ctx))
    # The service caller's list is its run's project (MET-441), as before.
    expected = {world.theirs} if caller == "service" else {world.mine, world.theirs}
    assert {p["id"] for p in listing["projects"]} == expected
    assert world.provider.asked == 0


# ── wiring ───────────────────────────────────────────────────────────


def test_scope_is_on_only_for_an_identity_login_with_a_scoping_provider() -> None:
    starlette_testclient = pytest.importorskip("starlette.testclient")
    del starlette_testclient
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.oauth import OAuthConfig, OAuthProvider

    def build(login: Any, identity: bool) -> UnifiedMcpServer:
        server = UnifiedMcpServer([], caller=Caller.UNTRUSTED)
        config = (
            OAuthConfig.from_env({}, identity_login=True)
            if identity
            else OAuthConfig.from_env({"METAFORGE_OAUTH_LOGIN_SECRET": "s"})
        )
        assert config is not None
        build_http_app(server, enable_sse=False, oauth=OAuthProvider(config), login=login)
        return server

    class PasswordOnly:
        async def verify_password(self, email: str, password: str) -> Principal:
            return Principal(subject=ALICE)

    assert build(Provider(), identity=True).project_scoper is not None
    assert build(PasswordOnly(), identity=True).project_scoper is None
    assert build(None, identity=False).project_scoper is None


# ── every tool has a tenancy ─────────────────────────────────────────


def _declared_tool_ids() -> set[str]:
    sys.path.insert(0, str(Path(__file__).parent))
    from test_mcp_tool_annotations import declared_tool_ids

    return declared_tool_ids()


def test_every_declared_tool_has_an_explicit_tenancy() -> None:
    declared = _declared_tool_ids()
    missing = sorted(declared - set(TOOL_RULES))
    assert not missing, (
        "these tools have no tenancy, so a signed-in account is refused them. Classify "
        f"each in metaforge/mcp/project_scope.py TOOL_RULES: {missing}"
    )
    stale = sorted(set(TOOL_RULES) - declared)
    assert not stale, f"no longer declared; drop from TOOL_RULES: {stale}"


def test_a_tool_with_no_rule_is_blocked() -> None:
    assert rule_for("brand.new_tool").tenancy is ToolTenancy.BLOCKED


def test_blocked_tools_say_why() -> None:
    for tool_id, rule in TOOL_RULES.items():
        if rule.tenancy is ToolTenancy.BLOCKED:
            assert rule.reason, tool_id
        if rule.tenancy in (ToolTenancy.PROJECT, ToolTenancy.ENTITY):
            assert rule.anchor != "none", tool_id
        if rule.tenancy is ToolTenancy.ENTITY and rule.anchor == "entity":
            assert rule.entities, tool_id
