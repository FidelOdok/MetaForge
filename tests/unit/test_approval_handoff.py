"""A held call is handed to a person when the client cannot wait on one (FORGE-741).

The claude.ai connector cannot show an approval prompt and is sent no
progress, so a held write used to sit for 100 s in silence and fail as
``timed_out``: the person never saw an approval. The call now returns at once
with the approval id and where to decide it, the hold stays open, and the same
call made again after the person approves runs once.

Driven against the real gateway app over ASGI, as the inline-ledger tests
are: the entry the dashboard shows is the thing under test. Also FORGE-732: a
held ``phase.claim`` carries its run's project.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from mcp_core.guardrails import Caller
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

LIST = "/v1/chat/tool_approvals"


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(server_mod, "HANDOFF_POLL_SECONDS", 0.01)


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _adapter(ran: list[dict[str, Any]]) -> McpToolServer:
    class _Spy(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="twin", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="twin.commit_geometry",
                    adapter_id="twin",
                    name="commit",
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

        async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
            ran.append(args)
            return {"ok": True}

    return _Spy()


async def _server(http: httpx.AsyncClient, ran: list[dict[str, Any]]) -> UnifiedMcpServer:
    server = UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )
    # The claude.ai connector: no elicitation capability, no progress token.
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "Anthropic/ClaudeAI", "version": "1.0"},
                },
            }
        )
    )
    return server


async def _call(server: UnifiedMcpServer, obj: str = "bracket") -> dict[str, Any]:
    return json.loads(
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "twin.commit_geometry", "arguments": {"obj_id": obj}},
                }
            )
        )
    )


async def _pending(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    return (await http.get(LIST, params={"status": "pending"})).json()["runs"]


@pytest.mark.asyncio
class TestTheCallIsHandedToAPerson:
    async def test_the_call_returns_at_once_with_the_approval(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[dict[str, Any]] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server)
            pending = await _pending(http)

        assert ran == []
        data = response["error"]["data"]
        assert data["code"] == "approval_required"
        assert data["outcome"] == "awaiting_person"
        assert data["retryable"] is True
        assert data["next"] == "call_again_with_same_arguments_after_approval"
        assert data["expires_in_seconds"] > 1700
        message = response["error"]["message"]
        assert "Tell the person" in message and "same arguments" in message
        # The hold is open on the ledger, under the id the client was given.
        assert [p["id"] for p in pending] == [data["approval_id"]]

    async def test_calling_again_while_pending_opens_no_second_hold(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[dict[str, Any]] = []
        async with gateway as http:
            server = await _server(http, ran)
            first = await _call(server)
            second = await _call(server)
            pending = await _pending(http)

        assert ran == []
        assert second["error"]["data"]["approval_id"] == first["error"]["data"]["approval_id"]
        assert "still waiting" in second["error"]["message"]
        assert len(pending) == 1

    async def test_after_the_person_approves_the_same_call_runs_once(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[dict[str, Any]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = (await _call(server))["error"]["data"]["approval_id"]
            decided = await http.post(f"{LIST}/{approval_id}", json={"decision": "approve"})
            assert decided.status_code == 200, decided.text
            again = await _call(server)
            third = await _call(server)
            pending = await _pending(http)

        assert "error" not in again, again
        assert [a["obj_id"] for a in ran] == ["bracket"]
        # The approval was used up: the next call is a new hold, not a free pass.
        assert third["error"]["data"]["approval_id"] != approval_id
        assert len(pending) == 1

    async def test_a_rejection_is_reported_and_nothing_runs(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[dict[str, Any]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = (await _call(server))["error"]["data"]["approval_id"]
            await http.post(f"{LIST}/{approval_id}", json={"decision": "reject"})
            again = await _call(server)

        assert ran == []
        assert again["error"]["data"]["outcome"] == "rejected"
        assert again["error"]["data"]["retryable"] is False

    async def test_an_approval_covers_only_the_arguments_approved(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[dict[str, Any]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = (await _call(server, "bracket"))["error"]["data"]["approval_id"]
            await http.post(f"{LIST}/{approval_id}", json={"decision": "approve"})
            other = await _call(server, "a_different_part")

        assert ran == []
        assert other["error"]["data"]["approval_id"] != approval_id

    async def test_a_person_who_answers_during_the_short_wait_runs_it_at_once(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 5.0)
        ran: list[dict[str, Any]] = []
        async with gateway as http:
            server = await _server(http, ran)
            call = asyncio.ensure_future(_call(server))
            for _ in range(200):
                pending = await _pending(http)
                if pending:
                    break
                await asyncio.sleep(0.01)
            await http.post(f"{LIST}/{pending[0]['id']}", json={"decision": "approve"})
            response = await call

        assert "error" not in response, response
        assert [a["obj_id"] for a in ran] == ["bracket"]


# ── FORGE-732: a held call carries its run's project ─────────────────────────


def test_a_held_task_call_is_scoped_to_its_runs_project() -> None:
    from api_gateway.chat.tool_approvals import project_for_hold
    from api_gateway.runs import routes as run_routes

    run_routes.reset_run_store()
    try:
        run = run_routes.get_run_store().create({"kind": "design_flow", "project_id": "p-chair"})
        assert project_for_hold(None, {"task_id": f"{run.id}:design:3"}) == "p-chair"
        assert project_for_hold(None, {"run_id": run.id}) == "p-chair"
        assert project_for_hold("p-explicit", {"run_id": run.id}) == "p-explicit"
        assert project_for_hold(None, {"task_id": "run_unknown:design:1"}) is None
    finally:
        run_routes.reset_run_store()


def test_a_held_calls_project_is_read_for_the_approvals_page() -> None:
    from api_gateway.runs.schemas import project_of

    assert project_of({"tool": "phase.claim", "project": "p-chair"}) == "p-chair"
