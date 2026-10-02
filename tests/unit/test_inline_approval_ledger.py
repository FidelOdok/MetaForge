"""Inline (elicitation) approvals reach the approval ledger (FORGE-473).

An approval answered in the client's own prompt used to log
``approval_id=None route=elicitation``: no id, invisible on the dashboard, no
recorded approver. FORGE-360 promised one ledger and two ways to answer.

These tests drive the real gateway app over ASGI, as the FORGE-406 tests do,
because a double for the ledger would pass while proving nothing about the
entry the dashboard and an auditor read.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from mcp_core.elicitation import ElicitAction, ElicitResult
from mcp_core.guardrails import Caller
from metaforge.mcp.remote_approvals import RemoteApprovalLedger
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

ELICIT_VERSION = "2025-06-18"
LIST = "/v1/chat/tool_approvals"


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _adapter(ran: list[str]) -> McpToolServer:
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
            ran.append("twin.commit_geometry")
            return {"ok": True}

    return _Spy()


async def _call(server: UnifiedMcpServer) -> dict[str, Any]:
    return json.loads(
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "twin.commit_geometry", "arguments": {"obj_id": "bracket"}},
                }
            )
        )
    )


async def _server(
    http: httpx.AsyncClient, ran: list[str], *, action: ElicitAction, approve: bool = True
) -> UnifiedMcpServer:
    async def elicitor(message: str, schema: dict[str, Any]) -> ElicitResult:
        return ElicitResult(action, {"approve": approve})

    server = UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        elicitor=elicitor,
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": ELICIT_VERSION,
                    "capabilities": {"elicitation": {}},
                    "clientInfo": {"name": "claude-code", "version": "2.1.4"},
                },
            }
        )
    )
    return server


def _text(response: dict[str, Any]) -> str:
    return json.dumps(response)


@pytest.mark.asyncio
class TestAnInlineAnswerIsInTheLedger:
    async def test_an_approved_write_is_listed_as_approved_via_elicitation(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran, action=ElicitAction.ACCEPT)
            response = await _call(server)
            entries = (await http.get(LIST, params={"status": "all"})).json()["runs"]

        assert "error" not in response, response
        assert ran == ["twin.commit_geometry"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["status"] == "running"
        assert entry["request"]["route"] == "elicitation"
        assert entry["request"]["tool"] == "twin.commit_geometry"
        assert entry["request"]["client"] == "claude-code 2.1.4"
        # The id in the result note is the one on the ledger.
        assert entry["id"].startswith("run_")
        assert entry["id"] in _text(response)
        # Open auth: recorded, and honestly unverified.
        assert entry["approved_by"] == "local:elicitation"
        assert entry["approver_verified"] is False

    async def test_an_inline_decline_resolves_as_rejected(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran, action=ElicitAction.DECLINE)
            response = await _call(server)
            entries = (await http.get(LIST, params={"status": "all"})).json()["runs"]

        assert "error" in response
        assert ran == []
        assert [e["status"] for e in entries] == ["rejected"]
        assert entries[0]["request"]["route"] == "elicitation"
        assert entries[0]["id"] in _text(response)

    async def test_a_dismissed_prompt_is_not_a_rejection(self, gateway: httpx.AsyncClient) -> None:
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran, action=ElicitAction.CANCEL)
            await _call(server)
            entries = (await http.get(LIST, params={"status": "all"})).json()["runs"]

        assert ran == []
        assert [e["status"] for e in entries] == ["timed_out"]

    async def test_the_default_listing_is_still_pending_only(
        self, gateway: httpx.AsyncClient
    ) -> None:
        async with gateway as http:
            server = await _server(http, [], action=ElicitAction.ACCEPT)
            await _call(server)
            pending = (await http.get(LIST)).json()["runs"]
        assert pending == []

    async def test_an_authenticated_session_is_recorded_as_the_approver(
        self, gateway: httpx.AsyncClient
    ) -> None:
        from mcp_core.context import McpCallContext, with_context

        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran, action=ElicitAction.ACCEPT)
            ctx = McpCallContext(actor_id="user:ada", actor_verified=True)
            with with_context(ctx):
                await _call(server)
            entry = (await http.get(LIST, params={"status": "all"})).json()["runs"][0]

        # Verified is claimed by the sidecar; an unauthenticated POST to the
        # gateway cannot make it stick (no principal on the request).
        assert entry["approved_by"] == "user:ada"
        assert entry["approver_verified"] is False


@pytest.mark.asyncio
class TestAnUnreachableLedgerRefusesTheWrite:
    async def test_the_write_does_not_run_and_the_error_says_why(self) -> None:
        ran: list[str] = []
        asked: list[str] = []

        async def elicitor(message: str, schema: dict[str, Any]) -> ElicitResult:
            asked.append(message)
            return ElicitResult(ElicitAction.ACCEPT, {"approve": True})

        server = UnifiedMcpServer(
            [_adapter(ran)],
            caller=Caller.REMOTE,
            elicitor=elicitor,
            approval_ledger=RemoteApprovalLedger("http://127.0.0.1:1", timeout=1.0),
        )
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": ELICIT_VERSION,
                        "capabilities": {"elicitation": {}},
                        "clientInfo": {"name": "claude-code", "version": "2.1.4"},
                    },
                }
            )
        )
        response = await _call(server)

        assert ran == [], "a write ran with no ledger entry"
        assert asked == [], "the person was asked a question that could not be recorded"
        assert response["error"]["data"]["code"] == "approval_ledger_unavailable"
        assert "approval ledger" in response["error"]["message"]


@pytest.mark.asyncio
class TestTheDashboardCannotAnswerAnInlineHold:
    async def test_a_click_on_a_live_inline_hold_is_refused(
        self, gateway: httpx.AsyncClient
    ) -> None:
        release = asyncio.Event()

        async def elicitor(message: str, schema: dict[str, Any]) -> ElicitResult:
            await release.wait()
            return ElicitResult(ElicitAction.ACCEPT, {"approve": True})

        ran: list[str] = []
        async with gateway as http:
            server = UnifiedMcpServer(
                [_adapter(ran)],
                caller=Caller.REMOTE,
                elicitor=elicitor,
                approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
            )
            await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": ELICIT_VERSION,
                            "capabilities": {"elicitation": {}},
                            "clientInfo": {"name": "claude-code"},
                        },
                    }
                )
            )
            call = asyncio.create_task(_call(server))
            await asyncio.sleep(0.2)
            pending = (await http.get(LIST)).json()["runs"]
            assert len(pending) == 1
            clicked = await http.post(f"{LIST}/{pending[0]['id']}", json={"decision": "approve"})
            release.set()
            await asyncio.wait_for(call, timeout=5.0)

        assert clicked.status_code == 409
        assert "approval prompt" in clicked.json()["detail"]
