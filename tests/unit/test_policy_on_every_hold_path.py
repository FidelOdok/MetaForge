"""A project's policy decides held writes on every hold path (FORGE-781).

Right after the durable holds landed, a project set to ``auto_clean`` still had
``twin.generate_concept_mesh`` held for a person: Claude Code calls through the
multi-round request flow, which takes the older hold path, and that path only
asked the policy about ``phase.claim``. Here the durable path is switched off
(a ledger without ``find_hold``), so the call goes the older way, and the
policy must still decide it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from mcp_core.guardrails import Caller
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

PROJECT = "p-microwave"
TOOLS = ("twin.render_concept_image", "project.delete")


class _NonDurableLedger(RemoteApprovalLedger):
    """The ledger as the older hold path sees it: no durable holds."""

    find_hold = None  # type: ignore[assignment]
    consume_hold = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


async def _policy(mode: str) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode=mode, updated_by="user:fidel")
    )


def _adapter(ran: list[str]) -> McpToolServer:
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
                ran.append(tool)
                return {"ok": True}

            return handle

    return _Twin()


async def _server(http: httpx.AsyncClient, ran: list[str]) -> UnifiedMcpServer:
    server = UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=_NonDurableLedger("http://gateway.test", client=http),
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


async def _call(server: UnifiedMcpServer, tool: str) -> dict[str, Any]:
    reply: dict[str, Any] = json.loads(
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": {"project_id": PROJECT}},
                }
            )
        )
    )
    return reply


@pytest.mark.asyncio
async def test_auto_clean_approves_an_illustrative_write_on_the_older_path(
    gateway: httpx.AsyncClient,
) -> None:
    await _policy("auto_clean")
    ran: list[str] = []
    async with gateway as http:
        server = await _server(http, ran)
        reply = await _call(server, "twin.render_concept_image")
        entries = (await http.get("/v1/chat/tool_approvals", params={"status": "all"})).json()
    assert ran == ["twin.render_concept_image"], reply
    policy_rows = [e for e in entries["runs"] if "policy" in json.dumps(e)]
    assert policy_rows, "the policy approval is on the ledger"


@pytest.mark.asyncio
async def test_a_never_list_tool_is_still_held_on_the_older_path(
    gateway: httpx.AsyncClient,
) -> None:
    await _policy("auto")
    ran: list[str] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, "project.delete")
    assert ran == []


@pytest.mark.asyncio
async def test_off_still_holds_on_the_older_path(gateway: httpx.AsyncClient) -> None:
    await _policy("off")
    ran: list[str] = []
    async with gateway as http:
        server = await _server(http, ran)
        await _call(server, "twin.render_concept_image")
    assert ran == []
