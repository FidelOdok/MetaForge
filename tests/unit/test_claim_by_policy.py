"""Under ``auto`` a phase claim on the project's own run is approved by the policy (FORGE-745).

The Chair run's reworked design task sat unclaimed for 40 minutes: its
project's policy was ``auto``, the reviewer had just decided the gate, and the
client's ``phase.claim`` was still held for a person nobody was there to be.
Driven against the real gateway app over ASGI, as the hand-off tests are.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from api_gateway.client_tasks import routes as task_routes
from api_gateway.runs import routes as run_routes
from mcp_core.guardrails import Caller
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from orchestrator.design_flow.client_tasks import PhaseTask, SqliteClientTaskStore, task_id_for
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

PROJECT = "p-chair"
LIST = "/v1/chat/tool_approvals"


@pytest.fixture(autouse=True)
def _stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    run_routes.reset_run_store()
    reset_policy_store(PolicyStore())
    task_routes.init_client_task_store(SqliteClientTaskStore())
    yield
    task_routes.init_client_task_store(None)
    reset_policy_store(None)
    run_routes.reset_run_store()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


async def _policy(mode: str, project: str = PROJECT) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=project, mode=mode, updated_by="user:fidel")
    )


def _open_task(project: str = PROJECT, status: str = "open") -> str:
    store = run_routes.get_run_store()
    run = store.create(
        {"kind": "design_flow", "flow": "mech_v1", "project_id": project, "intelligence": "client"}
    )
    store.start(run.id)
    task_id = task_id_for(run.id, "design", 1, 2)
    tasks = task_routes.get_client_task_store()
    tasks.open_task(
        PhaseTask(id=task_id, run_id=run.id, phase_id="design", attempt=1, project_id=project)
    )
    if status == "submitted":
        tasks.submit(task_id, "someone", "done", [])
    return task_id


def _adapter(ran: list[str]) -> McpToolServer:
    class _Phase(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="phase", version="0.1.0")
            for tool in ("phase.claim", "twin.commit_geometry"):
                self.register_tool(
                    ToolManifest(
                        tool_id=tool,
                        adapter_id="phase",
                        name=tool,
                        description="stub",
                        capability="test",
                    ),
                    self._handler(tool),
                )

        def _handler(self, tool: str) -> Any:
            async def handle(args: dict[str, Any]) -> dict[str, Any]:
                ran.append(tool)
                return {"ok": True}

            return handle

    return _Phase()


async def _server(http: httpx.AsyncClient, ran: list[str]) -> UnifiedMcpServer:
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
                    "clientInfo": {"name": "claude-code", "version": "2.1.310"},
                },
            }
        )
    )
    return server


async def _call(server: UnifiedMcpServer, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
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


async def _entries(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    return (await http.get(LIST, params={"status": "all"})).json()["runs"]


@pytest.mark.asyncio
class TestTheClaimIsApprovedByThePolicy:
    async def test_under_auto_the_claim_runs_and_is_on_the_ledger(
        self, gateway: httpx.AsyncClient
    ) -> None:
        await _policy("auto")
        task_id = _open_task()
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server, "phase.claim", {"task_id": task_id})
            entries = await _entries(http)

        assert "error" not in response, response
        assert ran == ["phase.claim"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["request"]["route"] == "policy"
        assert entry["request"]["tool"] == "phase.claim"
        assert entry["approved_by"] == "policy:auto:user:fidel"
        assert entry["approver_verified"] is False

    async def test_under_advise_the_claim_waits_for_a_person(
        self, gateway: httpx.AsyncClient
    ) -> None:
        await _policy("advise")
        task_id = _open_task()
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server, "phase.claim", {"task_id": task_id})

        assert ran == []
        assert response["error"]["data"]["outcome"] == "awaiting_person"

    async def test_a_task_that_is_not_open_waits_for_a_person(
        self, gateway: httpx.AsyncClient
    ) -> None:
        await _policy("auto")
        task_id = _open_task(status="submitted")
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran)
            await _call(server, "phase.claim", {"task_id": task_id})
        assert ran == []

    async def test_an_unknown_task_waits_for_a_person(self, gateway: httpx.AsyncClient) -> None:
        await _policy("auto")
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran)
            await _call(server, "phase.claim", {"task_id": "run_forged:design:1"})
        assert ran == []

    async def test_another_projects_policy_does_not_cover_it(
        self, gateway: httpx.AsyncClient
    ) -> None:
        await _policy("auto", project="p-other")
        task_id = _open_task(project=PROJECT)
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran)
            await _call(server, "phase.claim", {"task_id": task_id})
        assert ran == []

    async def test_any_other_write_still_waits_for_a_person(
        self, gateway: httpx.AsyncClient
    ) -> None:
        await _policy("auto")
        _open_task()
        ran: list[str] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server, "twin.commit_geometry", {"project_id": PROJECT})
        assert ran == []
        assert response["error"]["data"]["outcome"] == "awaiting_person"


@pytest.mark.asyncio
async def test_a_claimed_task_can_be_claimed_again_under_auto(gateway: httpx.AsyncClient) -> None:
    # FORGE-746: a client that lost its session handle claims again; the
    # store allows it and so does the policy, but not a submitted task.
    await _policy("auto")
    task_id = _open_task()
    task_routes.get_client_task_store().claim(task_id, "claude-code")
    ran: list[str] = []
    async with gateway as http:
        server = await _server(http, ran)
        response = await _call(server, "phase.claim", {"task_id": task_id})
    assert "error" not in response, response
    assert ran == ["phase.claim"]
