"""An owner can let a delegated agent baseline and approve a release (FORGE-834).

An autonomous agent could take a product to a validated design and still not
release it: ``twin.create_baseline`` is destructive, so it was held for a
person nobody could be, and the manufacturing release approval was not a
human-authority step, so ``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY`` did not
reach it. ``METAFORGE_ALLOW_AGENT_RELEASE`` (off by default) does: with it on
and the MCP request naming the agent and the person, the baseline runs and
``twin.approve_release`` approves the release, each recorded as the agent's
decision on the person's behalf, never as the person's. With it off nothing
changes, and no approval policy ever approves either step.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from api_gateway.approvals.service import AGENT_RELEASE_REASON, for_caller, tool_item
from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin.baseline import make_baseline_creator
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from api_gateway.twin.release_path import ReleasePathError, make_release_path_service
from mcp_core.context import McpCallContext, context_from_headers, with_context
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    DELEGATED_AGENT_ARG,
    Caller,
    ReleaseNotDelegatedError,
    never_auto_approved,
    policy_may_approve,
    release_delegated,
    requires_human_authority,
    requires_release_delegation,
    strip_reserved_arguments,
)
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tests.unit.test_dfm_exact import PID, PROJECT, _project, _run, measured
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.consistency.record_pins import unregister_head_moved_listener
from twin_core.dfm.release_checklist import manufacturing_checklist
from twin_core.library.machines import SqliteMachineLibrary

RELEASE_FLAG = "METAFORGE_ALLOW_AGENT_RELEASE"
HUMAN_FLAG = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
BASELINE = "twin.create_baseline"
APPROVE = "twin.approve_release"
AGENT = "claude-code-e2e"
PERSON = "fidel"
HEADERS = {"X-MetaForge-Agent": AGENT, "X-MetaForge-On-Behalf-Of": PERSON}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(RELEASE_FLAG, raising=False)
    monkeypatch.delenv(HUMAN_FLAG, raising=False)
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    reset_policy_store(PolicyStore())
    yield
    reset_policy_store(None)


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _adapter(ran: list[tuple[str, dict[str, Any]]]) -> McpToolServer:
    class _Twin(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="twin", version="0.1.0")
            for tool in (BASELINE, APPROVE):
                self.register_tool(
                    ToolManifest(
                        tool_id=tool, adapter_id="twin", name=tool, description="stub",
                        capability="test",
                    ),
                    self._handler(tool),
                )  # fmt: skip

        def _handler(self, tool: str) -> Any:
            async def handle(args: dict[str, Any]) -> dict[str, Any]:
                ran.append((tool, dict(args)))
                return {"ok": True}

            return handle

    return _Twin()


async def _server(http: httpx.AsyncClient, ran: list[tuple[str, dict[str, Any]]]) -> Any:
    server = UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "2.1.316"},
                },
            }
        )
    )
    return server


async def _call(
    server: Any,
    tool: str,
    ctx: McpCallContext,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = arguments or {"project_id": PROJECT, "reason": "release candidate"}
    with with_context(ctx):
        reply: dict[str, Any] = json.loads(
            await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": args},
                    }
                )
            )
        )
    return reply


def _headers(headers: dict[str, str]) -> McpCallContext:
    return context_from_headers({k.lower(): v for k, v in headers.items()})


def _runs() -> list[Any]:
    from api_gateway.chat.tool_approvals import get_approval_store

    return list(get_approval_store().list())


def _delegated() -> list[Any]:
    return [r for r in _runs() if r.request.get("route") == "delegated"]


# ── classification ───────────────────────────────────────────────────────


def test_the_release_steps_are_never_policy_approved() -> None:
    for tool in (BASELINE, APPROVE):
        assert requires_release_delegation(tool)
        assert not requires_human_authority(tool)
        assert never_auto_approved(tool)
        for mode in ("auto_clean", "auto"):
            assert not policy_may_approve(tool, mode)


def test_the_flag_is_off_by_default_and_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    assert release_delegated() is False
    monkeypatch.setenv(HUMAN_FLAG, "true")
    assert release_delegated() is False
    monkeypatch.setenv(RELEASE_FLAG, "true")
    assert release_delegated() is True


def test_a_client_cannot_supply_the_delegated_agent() -> None:
    assert strip_reserved_arguments({DELEGATED_AGENT_ARG: "me", "x": 1}) == {"x": 1}


# ── over MCP ─────────────────────────────────────────────────────────────


async def test_flag_off_the_baseline_is_held_and_the_approval_refused(
    gateway: httpx.AsyncClient,
) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, BASELINE, _headers(HEADERS))
        refused = await _call(server, APPROVE, _headers(HEADERS))
    assert ran == []
    assert _delegated() == []
    assert any(r.request.get("tool") == BASELINE for r in _runs()), "held on the ledger"
    # Refused outright, never held for a person whose approval would not help.
    assert "error" in refused and RELEASE_FLAG in refused["error"]["message"]
    assert all(r.request.get("tool") != APPROVE for r in _runs())


@pytest.mark.parametrize("missing", list(HEADERS))
async def test_flag_on_without_both_headers_nothing_is_delegated(
    missing: str, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RELEASE_FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    headers = {k: v for k, v in HEADERS.items() if k != missing}
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, BASELINE, _headers(headers))
        refused = await _call(server, APPROVE, _headers(headers))
    assert ran == []
    assert _delegated() == []
    assert "error" in refused


async def test_flag_on_with_the_headers_both_run_as_the_agent_for_the_person(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RELEASE_FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        # A model-written agent name never survives: the dispatcher sets it.
        await _call(
            server,
            BASELINE,
            _headers(HEADERS),
            {"project_id": PROJECT, "reason": "rc1", DELEGATED_AGENT_ARG: "forged"},
        )
        await _call(server, APPROVE, _headers(HEADERS))
    assert [t for t, _ in ran] == [BASELINE, APPROVE]
    for _tool, args in ran:
        assert args[APPROVED_BY_ARG] == PERSON
        assert args[DELEGATED_AGENT_ARG] == AGENT
    runs = _delegated()
    assert {r.request["tool"] for r in runs} == {BASELINE, APPROVE}
    for run in runs:
        assert run.request["agent"] == AGENT and run.request["on_behalf_of"] == PERSON
        assert run.request.get("consumed_at"), "used by this call, never by a retry"
        item = tool_item(run)
        assert item.status == "approved" and item.decision is not None
        assert item.decision.surface == "agent"
        assert item.decision.agent == AGENT and item.decision.on_behalf_of == PERSON
        assert RELEASE_FLAG in item.decision.reason


async def test_a_verified_principal_must_be_the_person_named(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RELEASE_FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    ctx = McpCallContext(
        actor_id="user:someone-else", actor_verified=True, agent=AGENT, on_behalf_of=PERSON
    )
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, BASELINE, ctx)
        refused = await _call(server, APPROVE, ctx)
    assert ran == []
    assert _delegated() == []
    assert "error" in refused


async def test_the_human_authority_flag_alone_does_not_delegate_a_release(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HUMAN_FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, BASELINE, _headers(HEADERS))
        refused = await _call(server, APPROVE, _headers(HEADERS))
    assert ran == []
    assert _delegated() == []
    assert "error" in refused


async def test_a_policy_never_approves_the_baseline(gateway: httpx.AsyncClient) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="auto", updated_by="user:fidel")
    )
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, BASELINE, _headers(HEADERS))
    assert ran == []
    assert all(r.request.get("route") not in ("policy", "delegated") for r in _runs())


# ── the gateway's record ─────────────────────────────────────────────────


async def test_the_gateway_records_a_release_only_under_its_own_flag(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"tool": BASELINE, "agent": AGENT, "on_behalf_of": PERSON, "approver": PERSON}
    url = "/v1/chat/tool_approvals/delegated"
    async with gateway as http:
        off = await http.post(url, json=body)
        monkeypatch.setenv(HUMAN_FLAG, "true")
        human_only = await http.post(url, json=body)
        monkeypatch.setenv(RELEASE_FLAG, "true")
        no_agent = await http.post(url, json={**body, "agent": ""})
        on = await http.post(url, json=body)
    assert off.status_code == 403 and RELEASE_FLAG in off.json()["detail"]
    assert human_only.status_code == 403
    assert no_agent.status_code == 422
    assert on.status_code == 200
    (run,) = _delegated()
    assert run.request["agent"] == AGENT and run.request["on_behalf_of"] == PERSON


async def test_the_outcome_is_counted_as_delegated_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.chat import tool_approvals

    tool_approvals.reset_approval_store()
    seen: list[str] = []
    monkeypatch.setattr(tool_approvals, "_record_outcome", seen.append)
    monkeypatch.setenv(RELEASE_FLAG, "true")
    tool_approvals.approve_delegated(
        tool_approvals.DelegatedApprovalRequest(
            tool=APPROVE, agent=AGENT, on_behalf_of=PERSON, approver=PERSON
        ),
        verified=False,
    )
    assert seen == ["delegated_release"]


async def test_an_agent_decides_a_held_baseline_only_under_the_release_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.chat.tool_approvals import create_hold, reset_approval_store

    reset_approval_store()
    run = create_hold(
        tool=BASELINE,
        arguments={"project_id": PROJECT},
        reason="destructive",
        caller="remote",
        source="mcp",
    )
    item = tool_item(run)
    monkeypatch.setenv(HUMAN_FLAG, "true")
    held = for_caller(item, "agent")
    assert held.decidable is False and held.not_decidable_reason == AGENT_RELEASE_REASON
    assert for_caller(item, "dashboard").decidable is True
    monkeypatch.setenv(RELEASE_FLAG, "true")
    assert for_caller(item, "agent").decidable is True


# ── what the tools record ────────────────────────────────────────────────


@pytest.fixture
def twin() -> Any:
    from twin_core.api import InMemoryTwinAPI

    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture(autouse=True)
def listener() -> Iterator[None]:
    dfm.register()
    yield
    unregister_head_moved_listener(dfm.on_head_moved)


@pytest.fixture(autouse=True)
def machine_library() -> Iterator[SqliteMachineLibrary]:
    library = SqliteMachineLibrary()
    library_routes.init_machine_library(library)
    yield library
    library_routes.init_machine_library(None)


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setenv("METAFORGE_DFM_SLICER_CHECK", "off")
    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


def _tools(twin: Any) -> TwinServer:
    return TwinServer(
        twin=twin,
        release_path_service=make_release_path_service(twin),
        baseline_creator=make_baseline_creator(twin),
    )


def _delegation() -> dict[str, Any]:
    return {APPROVED_BY_ARG: PERSON, DELEGATED_AGENT_ARG: AGENT}


async def _request(twin: Any) -> str:
    recorded = await make_engineering_entity_recorder(twin)(
        entity_type="release_approval", statement="Release rev A.", project_id=PROJECT
    )
    return str(recorded["node_id"])


async def test_a_delegated_baseline_names_the_agent_and_the_person(twin: Any) -> None:
    await _request(twin)
    out = await _tools(twin).create_baseline(
        {
            "project_id": PROJECT,
            "reason": "release candidate",
            "approved_by": ["Someone the model named"],
            **_delegation(),
        }
    )
    assert out["approved_by"] == [f"agent:{AGENT} on behalf of {PERSON}"]
    assert AGENT in out["reason"] and PERSON in out["reason"] and RELEASE_FLAG in out["reason"]
    (baseline,) = await twin.list_baselines(project_id=PID)
    assert baseline.approved_by == [f"agent:{AGENT} on behalf of {PERSON}"]


async def test_an_undelegated_baseline_still_needs_approved_by(twin: Any) -> None:
    await _request(twin)
    with pytest.raises(ValueError, match="approved_by"):
        await _tools(twin).create_baseline({"project_id": PROJECT, "reason": "rc"})


async def test_the_release_approval_passes_the_checklist_as_the_agents(
    twin: Any, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    await _run(twin, node_id, measured())
    entity_id = await _request(twin)
    before = await manufacturing_checklist(twin, PID)
    assert before is not None and before.item("approvals").status == "fail"

    out = await _tools(twin).approve_release(
        {"project_id": PROJECT, "reason": "every check passed", **_delegation()}
    )
    assert out["node_id"] == entity_id and out["authority"] == "approved"
    assert out["approved_by"] == f"agent:{AGENT}" and out["on_behalf_of"] == PERSON
    item = out["checklist_item"]
    assert item["status"] == "pass"
    assert f"approved by agent {AGENT} on behalf of {PERSON}" in item["detail"]

    after = await manufacturing_checklist(twin, PID)
    assert after is not None and after.releasable, after.blockers
    entity = await twin.get_engineering_entity(UUID(entity_id))
    # Never recorded as the person's own approval.
    assert entity.metadata["approved_by"] == f"agent:{AGENT}"
    assert entity.metadata["on_behalf_of"] == PERSON


async def test_the_release_is_not_approved_while_another_item_blocks(
    twin: Any, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    await _run(twin, node_id, measured(wall=0.5))
    entity_id = await _request(twin)
    with pytest.raises(ValueError, match="open items"):
        await _tools(twin).approve_release(
            {"project_id": PROJECT, "reason": "ship it", **_delegation()}
        )
    entity = await twin.get_engineering_entity(UUID(entity_id))
    assert entity.authority.value == "proposed"


async def test_the_release_approval_needs_a_request_to_approve(twin: Any) -> None:
    service = make_release_path_service(twin)
    with pytest.raises(ReleasePathError, match="record one first"):
        await service.approve_release(PROJECT, agent=AGENT, on_behalf_of=PERSON, reason="r")


async def test_the_tool_refuses_an_undelegated_call(twin: Any) -> None:
    await _request(twin)
    with pytest.raises(ReleaseNotDelegatedError, match=RELEASE_FLAG):
        await _tools(twin).approve_release({"project_id": PROJECT, "reason": "r"})
    with pytest.raises(ReleaseNotDelegatedError):
        await _tools(twin).approve_release(
            {"project_id": PROJECT, "reason": "r", APPROVED_BY_ARG: PERSON}
        )
