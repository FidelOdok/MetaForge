"""A task claimed with no signed-in identity names the client (FORGE-746).

The Chair run's design task showed ``claimed_by: unknown``: the plugin session
had no identity, so every client was "unknown", one session could not be told
from another, and "a client never reviews a phase it did" compared "unknown"
with "unknown".
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from mcp_core.context import McpCallContext, current_context, with_context
from mcp_core.guardrails import Caller
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.design_flow.adapter import DesignFlowServer

name = DesignFlowServer._client_name  # noqa: SLF001


def test_a_signed_in_actor_is_the_name() -> None:
    with with_context(McpCallContext(actor_id="user:fidel", client="claude-code 2.1.310")):
        assert name() == "user:fidel"


def test_without_an_identity_the_client_and_session_are_the_name() -> None:
    session = uuid4()
    with with_context(McpCallContext(session_id=session, client="claude-code 2.1.310")):
        assert name() == f"claude-code 2.1.310 (session {str(session)[:8]})"


def test_two_sessions_of_one_client_are_told_apart() -> None:
    names = set()
    for _ in range(2):
        with with_context(McpCallContext(session_id=uuid4(), client="claude-code 2.1.310")):
            names.add(name())
    assert len(names) == 2


def test_with_nothing_known_it_is_still_unknown() -> None:
    with with_context(McpCallContext()):
        assert name() == "unknown"


@pytest.mark.asyncio
async def test_the_server_passes_the_client_to_the_tool() -> None:
    seen: list[str | None] = []

    class _Spy(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="spy", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="phase.list_tasks",
                    adapter_id="spy",
                    name="list",
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

        async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
            seen.append(current_context().client)
            return {"tasks": []}

    server = UnifiedMcpServer([_Spy()], caller=Caller.REMOTE)
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
    response = json.loads(
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "phase.list_tasks", "arguments": {}},
                }
            )
        )
    )
    assert "error" not in response, response
    assert seen == ["claude-code 2.1.310"]
