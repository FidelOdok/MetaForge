"""A human-authority call an agent makes for a person, over MCP (FORGE-827).

A headless agent run stopped at ``twin.imagine_handover``: human-authority
tools are never approved by a policy, so the call was held on the client's
own approval prompt, which a headless client cannot answer. With the owner's
``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY`` on, and the MCP request naming the
agent (``X-MetaForge-Agent``) and the person (``X-MetaForge-On-Behalf-Of``),
the call is recorded as that agent's decision for the person and runs. With
the flag off, or without the person, it is held exactly as before.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from api_gateway.approvals.service import tool_item
from mcp_core.context import McpCallContext, context_from_headers, with_context
from mcp_core.guardrails import APPROVED_BY_ARG, Caller
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

FLAG = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
PROJECT = "p-lamp"
HANDOVER = "twin.imagine_handover"
TOOLS = (HANDOVER, "twin.imagine_accept", "project.delete")
HEADERS = {"X-MetaForge-Agent": "claude-code-e2e", "X-MetaForge-On-Behalf-Of": "fidel"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(FLAG, raising=False)
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
            for tool in TOOLS:
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


async def _call(server: Any, tool: str, headers: dict[str, str]) -> dict[str, Any]:
    with with_context(context_from_headers(headers)):
        reply: dict[str, Any] = json.loads(
            await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {
                            "name": tool,
                            "arguments": {"project_id": PROJECT, "op_id": "h1"},
                        },
                    }
                )
            )
        )
    return reply


def _runs() -> list[Any]:
    from api_gateway.chat.tool_approvals import get_approval_store

    return list(get_approval_store().list())


def test_the_headers_reach_the_call_context() -> None:
    ctx = context_from_headers({k.lower(): v for k, v in HEADERS.items()})
    assert ctx.agent == "claude-code-e2e"
    assert ctx.on_behalf_of == "fidel"


@pytest.mark.asyncio
async def test_flag_off_the_call_is_held(gateway: httpx.AsyncClient) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        reply = await _call(server, HANDOVER, HEADERS)
    assert ran == [], reply
    assert all(r.request.get("route") != "delegated" for r in _runs())
    assert any(r.request.get("tool") == HANDOVER for r in _runs()), "held on the ledger"


@pytest.mark.asyncio
async def test_flag_on_with_the_headers_runs_and_is_recorded_as_delegated(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        reply = await _call(server, HANDOVER, HEADERS)
    assert [t for t, _ in ran] == [HANDOVER], reply
    # The tool records the person the agent acted for, never a name the model wrote.
    assert ran[0][1][APPROVED_BY_ARG] == "fidel"
    (run,) = [r for r in _runs() if r.request.get("route") == "delegated"]
    assert run.request["tool"] == HANDOVER
    assert run.request["agent"] == "claude-code-e2e"
    assert run.request["on_behalf_of"] == "fidel"
    assert run.request.get("consumed_at"), "used by this call, never by a retry"
    # The Approvals page shows it the way it shows a `forge approvals` agent decision.
    item = tool_item(run)
    assert item.kind == "human_authority"
    assert item.status == "approved"
    assert item.decision is not None
    assert item.decision.surface == "agent"
    assert item.decision.agent == "claude-code-e2e"
    assert item.decision.on_behalf_of == "fidel"
    assert item.decision.approver == "fidel"
    assert item.decision.approver_verified is False


@pytest.mark.parametrize("missing", list(HEADERS))
@pytest.mark.asyncio
async def test_flag_on_without_both_headers_the_call_is_held(
    missing: str, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    headers = {k: v for k, v in HEADERS.items() if k != missing}
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, HANDOVER, headers)
    assert ran == []
    assert all(r.request.get("route") != "delegated" for r in _runs())


@pytest.mark.asyncio
async def test_a_destructive_tool_that_is_not_human_authority_keeps_its_rules(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, "project.delete", HEADERS)
    assert ran == []


@pytest.mark.asyncio
async def test_a_policy_never_approves_a_human_authority_tool(
    gateway: httpx.AsyncClient,
) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="auto", updated_by="user:fidel")
    )
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, HANDOVER, HEADERS)
    assert ran == []
    assert all(r.request.get("route") not in ("policy", "delegated") for r in _runs())


@pytest.mark.asyncio
async def test_the_gateway_records_nothing_when_it_does_not_delegate(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {
        "tool": HANDOVER,
        "agent": "claude-code-e2e",
        "on_behalf_of": "fidel",
        "approver": "fidel",
    }
    async with gateway as http:
        off = await http.post("/v1/chat/tool_approvals/delegated", json=body)
        monkeypatch.setenv(FLAG, "true")
        not_ha = await http.post(
            "/v1/chat/tool_approvals/delegated", json={**body, "tool": "project.delete"}
        )
        claimed = await http.post(
            "/v1/chat/tool_approvals/delegated", json={**body, "approver_verified": True}
        )
    assert off.status_code == 403
    assert not_ha.status_code == 422
    # An unauthenticated request cannot record a verified approver.
    assert claimed.status_code == 200
    assert claimed.json()["approver_verified"] is False


@pytest.mark.asyncio
async def test_a_verified_principal_must_be_the_person_named(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    ran: list[tuple[str, dict[str, Any]]] = []
    ctx = McpCallContext(
        actor_id="user:someone-else",
        actor_verified=True,
        agent="claude-code-e2e",
        on_behalf_of="fidel",
    )
    async with gateway as http:
        server = await _server(http, ran)
        with with_context(ctx):
            await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": HANDOVER, "arguments": {"project_id": PROJECT}},
                    }
                )
            )
    assert ran == []


@pytest.mark.asyncio
async def test_the_in_process_ledger_records_the_same_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.chat.tool_approvals import reset_approval_store
    from api_gateway.mcp_approvals import InProcessApprovalLedger
    from mcp_core.guardrails import ApprovalAsk, ApprovalOutcome, Approver

    reset_approval_store()
    ledger = InProcessApprovalLedger()
    ask = ApprovalAsk(tool_id=HANDOVER, arguments={}, caller=Caller.REMOTE, reason="x")
    person = Approver(actor_id="fidel")
    off = await ledger.delegated_approval(
        ask, agent="claude-code-e2e", on_behalf_of="fidel", approver=person
    )
    assert off is None
    monkeypatch.setenv(FLAG, "true")
    on = await ledger.delegated_approval(
        ask, agent="claude-code-e2e", on_behalf_of="fidel", approver=person
    )
    assert on is not None and on.outcome is ApprovalOutcome.APPROVED
    assert on.approver is not None and on.approver.actor_id == "fidel"
    (run,) = [r for r in _runs() if r.request.get("route") == "delegated"]
    decision = tool_item(run).decision
    assert decision is not None and decision.surface == "agent"
