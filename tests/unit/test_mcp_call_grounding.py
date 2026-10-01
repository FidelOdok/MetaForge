"""Every tool call carries a reference a reply can cite (FORGE-362).

F4 asks that a claimed action link to a tool result. The server cannot read
a model's reply, but it can make the claim checkable: `tools/call` returns
`_meta.callId`, and the session capture records the same id against the
action it took.

So "I committed the geometry" can name a call id, and that id either appears
in the session timeline or the claim is unsupported. Without it a grounded
answer and an invented one read identically — both are prose, and a reviewer
has no way to tell which is which.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from api_gateway.sessions.backend import InMemoryAgentSessionStore

# FORGE-387: these exercise tool dispatch, not the write gate. The
# server used to default to Caller.LOCAL, which exempted their writes
# by accident; the default is now conservative, so a local session is
# declared explicitly -- the same thing the stdio transport does.
from mcp_core.guardrails import Caller
from metaforge.mcp.capture import SessionCapture
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.seen_params: list[dict[str, Any]] = []
        self.register_tool(_manifest("twin.get_node"), self._ok)
        self.register_tool(_manifest("twin.boom"), self._boom)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        self.seen_params.append(args)
        return {"node": "n-1"}

    async def _boom(self, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("handler exploded")


def _manifest(tool_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id="twin",
        name=tool_id,
        description="stub",
        capability="test",
    )


def _req(tool: str, rid: int = 1) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"node_id": "n-1"}},
        }
    )


async def _events(store: InMemoryAgentSessionStore) -> list[Any]:
    sessions = await store.list_sessions()
    if not sessions:
        return []
    full = await store.get_session(sessions[0].id)
    return list(full.events or []) if full else []


@pytest.mark.asyncio
class TestTheReference:
    async def test_every_call_comes_back_with_one(self) -> None:
        server = UnifiedMcpServer(adapters=[_Adapter()], caller=Caller.LOCAL)
        result = json.loads(await server.handle_request(_req("twin.get_node")))["result"]
        assert result["_meta"]["callId"]

    async def test_two_calls_get_different_ones(self) -> None:
        # A shared id would make the timeline ambiguous exactly where it
        # matters: two commits, one cited, no way to tell which.
        server = UnifiedMcpServer(adapters=[_Adapter()], caller=Caller.LOCAL)
        first = json.loads(await server.handle_request(_req("twin.get_node", 1)))
        second = json.loads(await server.handle_request(_req("twin.get_node", 2)))
        assert first["result"]["_meta"]["callId"] != second["result"]["_meta"]["callId"]

    async def test_the_session_records_the_same_id(self) -> None:
        # The claim that carries the feature.
        store = InMemoryAgentSessionStore()
        server = UnifiedMcpServer(
            adapters=[_Adapter()], caller=Caller.LOCAL, session_capture=SessionCapture(store)
        )
        result = json.loads(await server.handle_request(_req("twin.get_node")))["result"]
        call_id = result["_meta"]["callId"]

        recorded = [e.data.get("call_id") for e in await _events(store)]
        assert call_id in recorded, f"{call_id} is not in the session timeline {recorded}"

    async def test_a_failed_call_is_still_traceable(self) -> None:
        # An agent claiming it tried something is as worth checking as one
        # claiming it succeeded.
        store = InMemoryAgentSessionStore()
        server = UnifiedMcpServer(
            adapters=[_Adapter()], caller=Caller.LOCAL, session_capture=SessionCapture(store)
        )
        response = json.loads(await server.handle_request(_req("twin.boom")))
        # FORGE-419: the refusal comes back as an isError result now, so the
        # reason reaches the model. The reference still has to be there --
        # that is this test's point, and the conversion carries `callId`
        # through precisely so a failed call stays citable.
        result = response.get("result", {})
        assert "error" in response or result.get("isError") is True, response
        if result:
            assert result["_meta"]["callId"]

        recorded = [e.data.get("call_id") for e in await _events(store)]
        assert [r for r in recorded if r], "a failed call left no reference in the timeline"


@pytest.mark.asyncio
class TestItStaysOutOfTheWay:
    async def test_the_adapter_never_sees_it_as_an_argument(self) -> None:
        # `_call_id` is protocol bookkeeping. Adapters validate what they are
        # given, and an unexpected key is what a strict schema rejects.
        adapter = _Adapter()
        server = UnifiedMcpServer(adapters=[adapter], caller=Caller.LOCAL)
        await server.handle_request(_req("twin.get_node"))
        assert adapter.seen_params, "handler never ran"
        for args in adapter.seen_params:
            assert "_call_id" not in args

    async def test_it_is_not_buried_in_the_tool_output(self) -> None:
        # The text payload is the tool's own output. Putting a protocol id
        # inside it would make every adapter's result schema wrong.
        server = UnifiedMcpServer(adapters=[_Adapter()], caller=Caller.LOCAL)
        result = json.loads(await server.handle_request(_req("twin.get_node")))["result"]
        payload = json.loads(result["content"][0]["text"])
        assert "callId" not in payload
        assert "_call_id" not in payload
