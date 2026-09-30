"""One error-code table, matching the spec where it assigns one (FORGE-388).

`-32002` meant three different things in this repo: `TOOL_TIMEOUT` in
`mcp_core.protocol`, `_AUTH_DENIED` in `metaforge.mcp.server`, and the
literal in the stdio auth-error response. The MCP spec assigns it to
"Resource not found", which was none of them -- `protocol.py` had that at
`-32004`.

A client branching on `-32002` could not tell a missing resource from a
timeout from a rejected credential, which is the entire purpose of a
numeric code. Two tables is one table that can disagree, and this pair
did.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core import protocol


def _codes() -> dict[str, int]:
    return {
        name: value
        for name, value in vars(protocol).items()
        if name.isupper() and isinstance(value, int)
    }


def test_no_two_names_share_a_code() -> None:
    codes = _codes()
    seen: dict[int, str] = {}
    clashes = []
    for name, value in codes.items():
        if value in seen:
            clashes.append(f"{name} and {seen[value]} are both {value}")
        seen[value] = name
    assert clashes == [], "; ".join(clashes)


def test_resource_not_found_is_the_code_the_spec_assigns() -> None:
    """The MCP spec pins this one. A client that knows the spec reads
    -32002 as "resource not found" whatever we intended by it."""
    assert protocol.RESOURCE_NOT_FOUND == -32002


def test_every_code_is_in_the_implementation_defined_range_or_standard() -> None:
    """JSON-RPC 2.0 reserves -32000..-32099 for server-defined errors; the
    rest must be the standard ones."""
    standard = {-32600, -32601, -32602, -32603, -32700}
    for name, value in _codes().items():
        assert value in standard or -32099 <= value <= -32000, f"{name} = {value}"


def test_the_server_uses_the_shared_table_rather_than_its_own() -> None:
    """The defect was a second copy. Importing means it cannot drift."""
    from metaforge.mcp import server

    assert server._AUTH_DENIED == protocol.AUTH_DENIED
    assert server._TOOL_EXECUTION_ERROR == protocol.TOOL_EXECUTION_ERROR
    assert server._RESOURCE_NOT_FOUND == protocol.RESOURCE_NOT_FOUND
    assert server._METHOD_NOT_FOUND == protocol.METHOD_NOT_FOUND


def test_auth_denied_does_not_collide_with_a_missing_resource() -> None:
    """The specific confusion this closes: a rejected credential and a
    missing resource must not arrive as the same number."""
    assert protocol.AUTH_DENIED != protocol.RESOURCE_NOT_FOUND


@pytest.mark.asyncio
async def test_a_missing_resource_answers_with_the_resource_code() -> None:
    """It used to answer with METHOD_NOT_FOUND, which says the *method*
    does not exist -- a different thing from the resource not existing,
    and the one a client uses to decide whether to retry elsewhere."""
    from metaforge.mcp.server import UnifiedMcpServer
    from tool_registry.mcp_server.handlers import ToolManifest
    from tool_registry.mcp_server.server import McpToolServer

    class _Twin(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="twin", version="0.1.0")
            self.register_tool(
                ToolManifest(
                    tool_id="twin.get_node",
                    adapter_id="twin",
                    name="twin.get_node",
                    description="stub",
                    capability="test",
                ),
                self._ok,
            )

        async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
            return {}

    server = UnifiedMcpServer([_Twin()])
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "resources/read",
                "params": {"uri": "metaforge://twin/brief/nope"},
            }
        )
    )
    assert json.loads(raw)["error"]["code"] == protocol.RESOURCE_NOT_FOUND


def test_there_is_only_one_table() -> None:
    """Three modules defined these codes and the three disagreed. Each
    now imports, so a fourth copy is the only way back."""
    from metaforge.mcp import server as unified
    from tool_registry.mcp_server import server as adapter

    assert adapter._RESOURCE_NOT_FOUND == protocol.RESOURCE_NOT_FOUND
    assert adapter._TOOL_EXECUTION_ERROR == protocol.TOOL_EXECUTION_ERROR
    assert unified._RESOURCE_NOT_FOUND == adapter._RESOURCE_NOT_FOUND


@pytest.mark.asyncio
async def test_an_adapters_missing_resource_stays_missing() -> None:
    """The unified server re-raised every adapter error as a read failure,
    so "this resource does not exist" reached the client as an execution
    error whose message said "Resource not found" -- the code
    contradicting its own text."""
    from metaforge.mcp.server import UnifiedMcpServer
    from tool_registry.mcp_server.server import McpToolServer

    class _Twin(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="twin", version="0.1.0")

    server = UnifiedMcpServer([_Twin()])
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "resources/read",
                "params": {"uri": "metaforge://twin/brief/nope"},
            }
        )
    )
    assert json.loads(raw)["error"]["code"] == protocol.RESOURCE_NOT_FOUND
