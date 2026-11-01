"""Session handles instead of Mcp-Session-Id for 2026-07-28 clients (FORGE-602).

That revision has no protocol session. ``project.open`` and ``session.start``
mint a handle for a caller without one; passing it back as
``metaforge_session`` gives later calls the scope it was bound to.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from mcp_core.context import bind_current_session_to, bound_project, current_context
from mcp_core.guardrails import Caller
from mcp_core.protocol_versions import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    META_SESSION_HANDLE,
    MODERN_PREVIEW_ENV,
    SESSION_ARGUMENT,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

PROJECT = uuid4()


@pytest.fixture(autouse=True)
def _preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODERN_PREVIEW_ENV, "on")


class _Project(McpToolServer):
    """``project.open`` binds the session as the real one does; ``project.get``
    reports what it was called with."""

    def __init__(self, seen: list[dict[str, Any]]) -> None:
        super().__init__(adapter_id="project", version="0.1.0")
        self.seen = seen
        for tool_id in ("project.open", "project.get"):
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="project",
                    name=tool_id,
                    description="stub",
                    capability="test",
                    input_schema={
                        "type": "object",
                        "properties": {"project_id": {"type": "string"}},
                    },
                ),
                self._open if tool_id == "project.open" else self._get,
            )

    async def _open(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"bound": bind_current_session_to(args.get("project_id"))}

    async def _get(self, args: dict[str, Any]) -> dict[str, Any]:
        ctx = current_context()
        self.seen.append({"args": dict(args), "session": ctx.session_id})
        return {"project": str(bound_project(ctx.session_id))}


def _server(seen: list[dict[str, Any]]) -> UnifiedMcpServer:
    # A read-only stub tool: run it as a local caller so nothing is held.
    return UnifiedMcpServer([_Project(seen)], caller=Caller.LOCAL)


def _meta() -> dict[str, Any]:
    return {META_PROTOCOL_VERSION: "2026-07-28", META_CLIENT_CAPABILITIES: {}}


def _call(server: UnifiedMcpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments, "_meta": _meta()},
    }
    return dict(json.loads(asyncio.run(server.handle_request(json.dumps(body)))))


def test_project_open_mints_a_handle_bound_to_the_project() -> None:
    result = _call(_server([]), "project.open", {"project_id": str(PROJECT)})["result"]
    handle = result["_meta"][META_SESSION_HANDLE]
    assert bound_project(UUID(handle)) == PROJECT
    assert any(handle in c.get("text", "") for c in result["content"])


def test_passing_the_handle_back_restores_the_scope() -> None:
    seen: list[dict[str, Any]] = []
    server = _server(seen)
    opened = _call(server, "project.open", {"project_id": str(PROJECT)})["result"]
    handle = opened["_meta"][META_SESSION_HANDLE]
    got = _call(server, "project.get", {SESSION_ARGUMENT: handle})["result"]
    assert str(PROJECT) in json.dumps(got)
    # The tool never sees the handle as one of its own arguments.
    assert seen[0]["args"] == {}
    assert seen[0]["session"] == UUID(handle)


def test_without_the_handle_a_later_call_has_no_scope() -> None:
    seen: list[dict[str, Any]] = []
    server = _server(seen)
    _call(server, "project.open", {"project_id": str(PROJECT)})
    got = _call(server, "project.get", {})["result"]
    assert str(PROJECT) not in json.dumps(got)


def test_a_supplied_handle_is_used_rather_than_a_new_one() -> None:
    mine = uuid4()
    result = _call(
        _server([]), "project.open", {"project_id": str(PROJECT), SESSION_ARGUMENT: str(mine)}
    )["result"]
    assert META_SESSION_HANDLE not in result["_meta"]
    assert bound_project(mine) == PROJECT


def test_a_malformed_handle_is_refused() -> None:
    response = _call(_server([]), "project.get", {SESSION_ARGUMENT: "not-a-handle"})
    assert response["error"]["code"] == -32602


def test_every_tool_lists_the_handle_for_a_modern_caller_only() -> None:
    server = _server([])
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _meta()}}
    modern = json.loads(asyncio.run(server.handle_request(json.dumps(body))))["result"]["tools"]
    assert all(SESSION_ARGUMENT in t["inputSchema"]["properties"] for t in modern)
    legacy_body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    legacy = json.loads(asyncio.run(server.handle_request(json.dumps(legacy_body))))["result"][
        "tools"
    ]
    assert all(
        SESSION_ARGUMENT not in (t.get("inputSchema") or {}).get("properties", {}) for t in legacy
    )
