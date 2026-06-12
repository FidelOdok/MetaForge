"""Server-side MCP auto-capture (MET-496).

Drives ``UnifiedMcpServer._tool_call`` (the single funnel for tools/call +
tool/call) with stub adapters + an InMemoryAgentSessionStore and asserts the
captured action/error timeline. Mirrors the stub harness of
``test_unified_mcp_server.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.sessions.backend import InMemoryAgentSessionStore
from metaforge.mcp.capture import SessionCapture
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolHandlerError, ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str, adapter_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name=tool_id,
        description=f"stub {tool_id}",
        capability="test",
    )


class _ToolServer(McpToolServer):
    """alpha.add (ok), alpha.boom (raises)."""

    def __init__(self) -> None:
        super().__init__(adapter_id="alpha", version="0.1.0")
        self.register_tool(_manifest("alpha.add", "alpha"), self._add)
        self.register_tool(_manifest("alpha.boom", "alpha"), self._boom)

    async def _add(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"sum": args.get("a", 0) + args.get("b", 0)}

    async def _boom(self, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("intentional failure")


class _SessionServer(McpToolServer):
    """Stand-in for the MET-494 session.* adapter — start creates a real
    session in the shared store and returns its id (so takeover routing can
    be verified end to end)."""

    def __init__(self, store: InMemoryAgentSessionStore) -> None:
        super().__init__(adapter_id="session", version="0.1.0")
        self._store = store
        self.register_tool(_manifest("session.start", "session"), self._start)
        self.register_tool(_manifest("session.complete", "session"), self._complete)

    async def _start(self, args: dict[str, Any]) -> dict[str, Any]:
        s = await self._store.create_session(agent_code="cc", task_type="explicit")
        return {"session_id": s.id}

    async def _complete(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


async def _call(server: UnifiedMcpServer, tool_id: str, **args: Any) -> Any:
    return await server._tool_call({"tool_id": tool_id, "arguments": args})


class TestAutoCapture:
    async def test_three_calls_one_session_ordered_actions(self) -> None:
        store = InMemoryAgentSessionStore.create()
        server = UnifiedMcpServer(adapters=[_ToolServer()], session_capture=SessionCapture(store))

        for _ in range(3):
            await _call(server, "alpha.add", a=1, b=2)

        sessions = await store.list_sessions()
        assert len(sessions) == 1
        events = sessions[0].events
        assert [e.type for e in events] == ["action", "action", "action"]
        assert [e.data["tool_id"] for e in events] == ["alpha.add"] * 3
        assert all(e.data["status"] == "ok" for e in events)
        assert "duration_ms" in events[0].data

    async def test_error_path_records_error_event(self) -> None:
        store = InMemoryAgentSessionStore.create()
        server = UnifiedMcpServer(adapters=[_ToolServer()], session_capture=SessionCapture(store))

        with pytest.raises(ToolHandlerError):
            await _call(server, "alpha.boom")

        events = (await store.list_sessions())[0].events
        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].data["tool_id"] == "alpha.boom"

    async def test_session_tools_excluded_and_takeover(self) -> None:
        store = InMemoryAgentSessionStore.create()
        server = UnifiedMcpServer(
            adapters=[_ToolServer(), _SessionServer(store)],
            session_capture=SessionCapture(store),
        )

        # session.start creates the explicit session and binds the takeover;
        # the session.start call itself produces NO action event.
        start_result = await _call(server, "session.start")
        explicit_id = start_result["data"]["session_id"]
        # A regular tool call now attaches to the explicit session, not a new
        # implicit one.
        await _call(server, "alpha.add", a=2, b=3)

        sessions = await store.list_sessions()
        # Exactly one session exists (the explicit one) — no implicit session
        # was lazily created because takeover was already bound.
        assert len(sessions) == 1
        assert sessions[0].id == explicit_id
        events = sessions[0].events
        # session.start left no event; the alpha.add action landed here.
        assert [e.data["tool_id"] for e in events] == ["alpha.add"]
        assert server._capture is not None
        assert server._capture._explicit_id == explicit_id

    async def test_capture_disabled_is_noop(self) -> None:
        server = UnifiedMcpServer(adapters=[_ToolServer()])  # no session_capture
        result = await _call(server, "alpha.add", a=4, b=5)
        assert result["data"]["sum"] == 9

    async def test_store_failure_never_breaks_tool_call(self) -> None:
        class _RaisingStore(InMemoryAgentSessionStore):
            async def append_event(self, *a: Any, **k: Any) -> tuple[str, int]:
                raise RuntimeError("db down")

        server = UnifiedMcpServer(
            adapters=[_ToolServer()], session_capture=SessionCapture(_RaisingStore())
        )
        # Tool call still succeeds despite the store blowing up on capture.
        result = await _call(server, "alpha.add", a=7, b=8)
        assert result["data"]["sum"] == 15
