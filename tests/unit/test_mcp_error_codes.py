"""No legacy-range error codes for 2026-07-28 callers (FORGE-607).

2026-07-28 marks -32000..-32019 as legacy, and every code of this server's
own sits there (tool execution -32001 most of all). Handshake-free callers get
a tool refusal as an ``isError`` result and other errors moved out of the
reserved range; handshake callers keep the codes they have always had.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.guardrails import Caller
from mcp_core.protocol_versions import (
    META_CLIENT_CAPABILITIES,
    META_ERROR,
    META_PROTOCOL_VERSION,
    is_legacy_error_code,
    modern_error_code,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Twin(McpToolServer):
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

    async def _handler(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


def _call(modern: bool) -> dict[str, Any]:
    # A remote caller's write with no approval gate configured: refused with
    # the server's own tool-execution code, -32001.
    server = UnifiedMcpServer([_Twin()], caller=Caller.REMOTE)
    params: dict[str, Any] = {"name": "twin.commit_geometry", "arguments": {}}
    if modern:
        params["_meta"] = {META_PROTOCOL_VERSION: "2026-07-28", META_CLIENT_CAPABILITIES: {}}
    body = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": params}
    return dict(json.loads(asyncio.run(server.handle_request(json.dumps(body)))))


def test_a_handshake_client_keeps_its_json_rpc_error() -> None:
    response = _call(modern=False)
    assert response["error"]["code"] == -32001
    assert response["error"]["data"]["code"] == "approval_required"


def test_a_modern_tool_refusal_is_an_is_error_result() -> None:
    response = _call(modern=True)
    assert "error" not in response
    result = response["result"]
    assert result["isError"] is True
    assert result["resultType"] == "complete"
    assert "needs approval" in result["content"][0]["text"]
    detail = result["_meta"][META_ERROR]
    assert detail["code"] == -31001
    assert detail["legacyCode"] == -32001
    assert detail["data"]["code"] == "approval_required"


def test_nothing_in_the_modern_answer_uses_a_reserved_code() -> None:
    text = json.dumps(_call(modern=True))
    assert '"code": -32001' not in text


def test_another_methods_code_moves_out_of_the_reserved_range() -> None:
    server = UnifiedMcpServer(adapters=[])
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32003, "message": "down", "data": {"a": 1}},
    }
    out = server._modernise(response, "prompts/get")
    assert out["error"]["code"] == -31003
    assert out["error"]["data"] == {"a": 1, "legacyCode": -32003}


def test_resource_not_found_still_becomes_invalid_params() -> None:
    server = UnifiedMcpServer(adapters=[])
    response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32002, "message": "missing"}}
    assert server._modernise(response, "resources/read")["error"]["code"] == -32602


@pytest.mark.parametrize(
    ("code", "legacy", "modern"),
    [
        (-32000, True, -31000),
        (-32001, True, -31001),
        (-32019, True, -31019),
        (-32020, False, -32020),  # the spec's own codes are left alone
        (-32602, False, -32602),
        (-32601, False, -32601),
    ],
)
def test_the_mapping(code: int, legacy: bool, modern: int) -> None:
    assert is_legacy_error_code(code) is legacy
    assert modern_error_code(code) == modern


def test_over_http_the_refusal_is_a_200_result() -> None:
    from fastapi.testclient import TestClient

    from metaforge.mcp.__main__ import build_http_app

    client = TestClient(
        build_http_app(UnifiedMcpServer([_Twin()], caller=Caller.REMOTE), enable_sse=False)
    )
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "twin.commit_geometry",
            "arguments": {},
            "_meta": {META_PROTOCOL_VERSION: "2026-07-28", META_CLIENT_CAPABILITIES: {}},
        },
    }
    response = client.post(
        "/mcp",
        json=body,
        headers={
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "twin.commit_geometry",
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
