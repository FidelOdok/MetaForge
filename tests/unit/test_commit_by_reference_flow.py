"""End-to-end commit-by-reference through the real UnifiedMcpServer dispatch (MET-10).

No LLM: drives the actual JSON-RPC dispatch (export_model -> stash ->
commit_geometry fill) that the fix wires into ``_dispatch_tool_call``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str, adapter_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id, adapter_id=adapter_id, name=tool_id, description="stub", capability="test"
    )


class _FreecadStub(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="freecad", version="0.1.0")
        self.register_tool(_manifest("freecad.export_model", "freecad"), self._export)

    async def _export(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"obj_id": args["obj_id"], "format": "step", "step_base64": "STEP-BLOB-XYZ"}


class _TwinStub(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.received: dict[str, Any] = {}
        self.register_tool(_manifest("twin.commit_geometry", "twin"), self._commit)

    async def _commit(self, args: dict[str, Any]) -> dict[str, Any]:
        self.received = dict(args)
        if not args.get("step_base64"):
            raise ValueError("no geometry to commit")
        return {"node_id": "node-1", "content_hash": "deadbeef"}


def _call(tool_id: str, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tool/call",
            "params": {"tool_id": tool_id, "arguments": arguments},
        }
    )


@pytest.mark.asyncio
async def test_commit_by_reference_fills_step_from_prior_export() -> None:
    twin = _TwinStub()
    server = UnifiedMcpServer(adapters=[_FreecadStub(), twin])

    # 1) export the assembly — the stash remembers its STEP by (session_id, obj_id)
    await server.handle_request(
        _call("freecad.export_model", {"session_id": "s1", "obj_id": "assembly_4"})
    )

    # 2) commit BY REFERENCE — no step_base64, just the small ids + name
    resp = json.loads(
        await server.handle_request(
            _call(
                "twin.commit_geometry",
                {"session_id": "s1", "obj_id": "assembly_4", "name": "Gimbal Base"},
            )
        )
    )

    # the server filled the blob server-side and the commit succeeded
    assert twin.received.get("step_base64") == "STEP-BLOB-XYZ"
    assert resp["result"]["data"]["node_id"] == "node-1"


@pytest.mark.asyncio
async def test_commit_without_export_still_fails_cleanly() -> None:
    twin = _TwinStub()
    server = UnifiedMcpServer(adapters=[_FreecadStub(), twin])
    resp = json.loads(
        await server.handle_request(
            _call("twin.commit_geometry", {"session_id": "s1", "obj_id": "never", "name": "x"})
        )
    )
    # nothing to fill -> the twin handler rejects it (error envelope), no crash
    assert "error" in resp
