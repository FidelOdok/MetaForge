"""An MCP write lands in the queue a human is already watching (FORGE-359).

The unit tests for the gate use a stub that answers instantly. This one
drives the real gateway gate: fire a write from a remote caller, watch it
appear in the same store the dashboard's Approvals page reads, answer it,
and check the tool ran only then.

That ordering is the claim. A guardrail that runs the write and reports an
approval afterwards would pass any test that only looks at the final result.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from api_gateway.chat.tool_approvals import get_approval_store, reset_approval_store
from api_gateway.mcp_approvals import build_mcp_approval_gate
from mcp_core.guardrails import Caller
from metaforge.mcp.server import UnifiedMcpServer
from orchestrator.harness.runs import ApprovalDecision, RunStatus
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Spy(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.ran: list[str] = []
        for tool_id in ("twin.get_node", "twin.commit_geometry"):
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="twin",
                    name=tool_id,
                    description="stub",
                    capability="test",
                ),
                self._make(tool_id),
            )

    def _make(self, tool_id: str):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            self.ran.append(tool_id)
            return {"ok": True}

        return handler


@pytest.fixture(autouse=True)
def _clean_store():
    reset_approval_store()
    yield
    reset_approval_store()


def _request(tool: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"node_id": "n-1"}},
        }
    )


async def _await_pending(timeout: float = 5.0) -> str:
    """Wait for a run to show up awaiting a decision, and return its id."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for run in get_approval_store().list():
            if run.status is RunStatus.AWAITING_APPROVAL:
                return run.id
        await asyncio.sleep(0.02)
    raise AssertionError("no run ever reached AWAITING_APPROVAL")


@pytest.mark.asyncio
class TestTheQueue:
    async def test_a_remote_write_waits_in_the_queue_then_runs_when_approved(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=build_mcp_approval_gate(timeout_seconds=5.0, poll_interval=0.02),
        )

        call = asyncio.create_task(server.handle_request(_request("twin.commit_geometry")))
        run_id = await _await_pending()

        # The ordering that matters: held, and not yet run.
        assert spy.ran == [], "the write ran before anyone approved it"

        run = get_approval_store().get(run_id)
        assert run.request["tool"] == "twin.commit_geometry"
        # A reviewer needs to know it came from a remote harness, not the
        # dashboard -- the same write is a different request depending.
        assert run.request["caller"] == "remote"
        assert run.request["source"] == "mcp"

        get_approval_store().submit_approval(run_id, ApprovalDecision.APPROVE)
        response = json.loads(await asyncio.wait_for(call, timeout=5.0))

        assert "error" not in response, response
        assert spy.ran == ["twin.commit_geometry"]

    async def test_a_rejection_in_the_queue_stops_it(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=build_mcp_approval_gate(timeout_seconds=5.0, poll_interval=0.02),
        )

        call = asyncio.create_task(server.handle_request(_request("twin.commit_geometry")))
        run_id = await _await_pending()
        get_approval_store().submit_approval(run_id, ApprovalDecision.REJECT)

        response = json.loads(await asyncio.wait_for(call, timeout=5.0))
        assert response["error"]["data"]["outcome"] == "rejected"
        assert spy.ran == []

    async def test_nobody_answering_denies_by_default(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            # Short enough to actually elapse in a test.
            approval_gate=build_mcp_approval_gate(timeout_seconds=0.2, poll_interval=0.02),
        )
        response = json.loads(
            await asyncio.wait_for(server.handle_request(_request("twin.commit_geometry")), 5.0)
        )
        assert response["error"]["data"]["outcome"] == "timed_out"
        assert spy.ran == []

    async def test_a_read_never_reaches_the_queue(self) -> None:
        # Otherwise the queue fills with things nobody needs to decide, and
        # the ones that matter get lost in it.
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=build_mcp_approval_gate(timeout_seconds=5.0, poll_interval=0.02),
        )
        response = json.loads(
            await asyncio.wait_for(server.handle_request(_request("twin.get_node")), 5.0)
        )
        assert "error" not in response, response
        assert spy.ran == ["twin.get_node"]
        assert get_approval_store().list() == []
