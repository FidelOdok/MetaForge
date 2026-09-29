"""Writes are held for approval whoever asked (FORGE-359).

MetaForge held writes on the chat path and nowhere else: HarnessRuntime
pauses on ToolSpec.requires_approval, and the MCP dispatch path consulted
nothing at all. The same twin.commit_geometry was gated when a person asked
in the dashboard and ungated when Claude Code, Codex or ChatGPT asked over
MCP.

The assertion that carries this file is not "an error came back" — it is
that the handler *did not run*. A guardrail that returns an error after
doing the write is worse than no guardrail, because the error makes it look
like it held.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalOutcome,
    Caller,
    decide,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id="twin",
        name=tool_id,
        description=f"stub {tool_id}",
        capability="test",
    )


class _Spy(McpToolServer):
    """Records every handler that actually ran."""

    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.ran: list[str] = []
        for tool_id in ("twin.get_node", "twin.commit_geometry", "project.delete", "twin.query_cypher"):
            self.register_tool(_manifest(tool_id), self._make(tool_id))

    def _make(self, tool_id: str):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            self.ran.append(tool_id)
            return {"ok": True}

        return handler


async def _call(server: UnifiedMcpServer, tool: str) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"a": 1}},
            }
        )
    )
    return json.loads(raw)


async def _approve(ask: ApprovalAsk) -> ApprovalOutcome:
    return ApprovalOutcome.APPROVED


async def _reject(ask: ApprovalAsk) -> ApprovalOutcome:
    return ApprovalOutcome.REJECTED


async def _silence(ask: ApprovalAsk) -> ApprovalOutcome:
    return ApprovalOutcome.TIMED_OUT


class TestDecision:
    def test_reads_are_never_held(self) -> None:
        for caller in Caller:
            assert not decide("twin.get_node", caller=caller).requires_approval

    def test_a_remote_write_is_held_even_with_the_local_exemption_on(self) -> None:
        # The exemption is for stdio on the engineer's own machine. If it
        # ever covered remote, the switch would silently disable the feature.
        d = decide("twin.commit_geometry", caller=Caller.REMOTE, exempt_local_writes=True)
        assert d.requires_approval

    def test_an_unclassified_tool_is_held(self) -> None:
        # Inherits the destructive default from mcp_core.annotations rather
        # than being waved through for lack of an entry.
        assert decide("some.new_tool", caller=Caller.REMOTE).requires_approval

    def test_query_cypher_follows_the_mutation_flag(self) -> None:
        # Ties F1 to C5: the same tool, held or not depending on how the
        # server was started, from one classification rather than two.
        assert not decide("twin.query_cypher", caller=Caller.REMOTE).requires_approval
        assert decide(
            "twin.query_cypher", caller=Caller.REMOTE, twin_mutations_enabled=True
        ).requires_approval


@pytest.mark.asyncio
class TestTheCallDoesNotRun:
    async def test_no_gate_configured_refuses_rather_than_running(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE)
        response = await _call(server, "twin.commit_geometry")

        assert "error" in response
        assert response["error"]["data"]["outcome"] == "not_configured"
        # The point of the whole file.
        assert spy.ran == [], "the write ran despite being refused"

    async def test_a_rejection_does_not_run_it(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE, approval_gate=_reject)
        response = await _call(server, "project.delete")
        assert response["error"]["data"]["outcome"] == "rejected"
        assert spy.ran == []

    async def test_silence_does_not_run_it_either(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE, approval_gate=_silence)
        response = await _call(server, "project.delete")
        # Distinct from "rejected": nobody said no, nobody said anything.
        assert response["error"]["data"]["outcome"] == "timed_out"
        assert spy.ran == []

    async def test_an_approval_lets_it_through(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE, approval_gate=_approve)
        response = await _call(server, "twin.commit_geometry")
        assert "error" not in response, response
        assert spy.ran == ["twin.commit_geometry"]

    async def test_reads_are_not_held_even_with_no_gate(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE)
        response = await _call(server, "twin.get_node")
        assert "error" not in response, response
        assert spy.ran == ["twin.get_node"]


@pytest.mark.asyncio
class TestTheRefusalIsLegible:
    async def test_it_is_a_json_rpc_error_not_an_escaped_exception(self) -> None:
        # The first version of this threw out of handle_request, which a
        # client sees as a dropped connection -- no reason, and an invitation
        # to retry.
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE)
        response = await _call(server, "twin.commit_geometry")
        assert response["jsonrpc"] == "2.0"
        assert response["error"]["data"]["code"] == "approval_required"
        assert response["error"]["data"]["tool_id"] == "twin.commit_geometry"

    async def test_nothing_here_is_marked_retryable(self) -> None:
        spy = _Spy()
        for gate, _ in ((None, "not_configured"), (_reject, "rejected"), (_silence, "timed_out")):
            server = UnifiedMcpServer(adapters=[spy], caller=Caller.REMOTE, approval_gate=gate)
            response = await _call(server, "project.delete")
            assert response["error"]["data"]["retryable"] is False

    async def test_the_reviewer_is_told_why(self) -> None:
        seen: list[ApprovalAsk] = []

        async def capture(ask: ApprovalAsk) -> ApprovalOutcome:
            seen.append(ask)
            return ApprovalOutcome.APPROVED

        server = UnifiedMcpServer(adapters=[_Spy()], caller=Caller.REMOTE, approval_gate=capture)
        await _call(server, "project.delete")
        assert seen[0].tool_id == "project.delete"
        assert seen[0].arguments == {"a": 1}
        assert "remove data" in seen[0].reason


@pytest.mark.asyncio
class TestLocalDefault:
    async def test_stdio_writes_run_by_default(self) -> None:
        # stdio has nowhere to answer an approval until elicitation lands
        # (F2). Holding there with no gate is an outage, not a guardrail.
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy])
        response = await _call(server, "twin.commit_geometry")
        assert "error" not in response, response
        assert spy.ran == ["twin.commit_geometry"]

    async def test_a_deployment_can_turn_the_exemption_off(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], approval_gate=_approve, exempt_local_writes=False)
        await _call(server, "twin.commit_geometry")
        assert spy.ran == ["twin.commit_geometry"]

        spy2 = _Spy()
        held = UnifiedMcpServer(adapters=[spy2], approval_gate=_reject, exempt_local_writes=False)
        response = await _call(held, "twin.commit_geometry")
        assert response["error"]["data"]["outcome"] == "rejected"
        assert spy2.ran == []
