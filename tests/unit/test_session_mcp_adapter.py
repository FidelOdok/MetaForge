"""Session MCP adapter (MET-494).

Exercises SessionServer handlers against an InMemoryAgentSessionStore and
verifies the bootstrap gating (registers only when a store is supplied).
"""

from __future__ import annotations

import pytest

from api_gateway.sessions.backend import (
    InMemoryAgentSessionStore,
    SessionClosedError,
    SessionNotFoundError,
)
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.tools.session.adapter import SessionServer


def _server() -> tuple[SessionServer, InMemoryAgentSessionStore]:
    store = InMemoryAgentSessionStore.create()
    return SessionServer(store=store), store


class TestHandlers:
    async def test_start_log_complete_happy_path(self) -> None:
        server, store = _server()

        started = await server.handle_start(
            {"agent_code": "claude-code", "task_type": "design", "title": "T"}
        )
        sid = started["session_id"]

        for i, etype in enumerate(["thought", "action", "decision"], start=1):
            ack = await server.handle_log_event(
                {"session_id": sid, "type": etype, "message": f"e{i}", "data": {"i": i}}
            )
            assert ack["seq"] == i
            assert ack["event_id"]

        done = await server.handle_complete(
            {"session_id": sid, "status": "completed", "summary": "ok"}
        )
        assert done["ok"] is True

        session = await store.get_session(sid)
        assert session.status == "completed"
        assert [e.type for e in session.events] == ["thought", "action", "decision"]

    async def test_start_requires_agent_code(self) -> None:
        server, _ = _server()
        with pytest.raises(ValueError, match="agent_code"):
            await server.handle_start({"task_type": "design"})

    async def test_log_event_invalid_type_rejected(self) -> None:
        server, _ = _server()
        started = await server.handle_start({"agent_code": "a", "task_type": "t"})
        with pytest.raises(ValueError, match="type"):
            await server.handle_log_event(
                {"session_id": started["session_id"], "type": "bogus", "message": "x"}
            )

    async def test_log_event_unknown_session_raises(self) -> None:
        server, _ = _server()
        with pytest.raises(SessionNotFoundError):
            await server.handle_log_event(
                {"session_id": "missing", "type": "thought", "message": "x"}
            )

    async def test_log_event_after_complete_raises(self) -> None:
        server, _ = _server()
        started = await server.handle_start({"agent_code": "a", "task_type": "t"})
        sid = started["session_id"]
        await server.handle_complete({"session_id": sid, "status": "completed"})
        with pytest.raises(SessionClosedError):
            await server.handle_log_event({"session_id": sid, "type": "thought", "message": "late"})

    async def test_complete_invalid_status_rejected(self) -> None:
        server, _ = _server()
        started = await server.handle_start({"agent_code": "a", "task_type": "t"})
        with pytest.raises(ValueError, match="status"):
            await server.handle_complete({"session_id": started["session_id"], "status": "weird"})

    def test_store_unbound_raises(self) -> None:
        server = SessionServer()  # no store
        with pytest.raises(RuntimeError, match="set_store"):
            _ = server.store

    def test_tool_ids(self) -> None:
        server, _ = _server()
        assert set(server.tool_ids) == {
            "session.start",
            "session.log_event",
            "session.complete",
        }


class TestBootstrapGating:
    async def test_registered_when_store_supplied(self) -> None:
        registry = await bootstrap_tool_registry(
            adapter_ids=[],  # skip the static-factory adapters
            agent_session_store=InMemoryAgentSessionStore.create(),
        )
        ids = [a.adapter_id for a in registry.list_adapter_servers()]
        assert "session" in ids

    async def test_skipped_when_store_absent(self) -> None:
        registry = await bootstrap_tool_registry(adapter_ids=[], agent_session_store=None)
        ids = [a.adapter_id for a in registry.list_adapter_servers()]
        assert "session" not in ids
