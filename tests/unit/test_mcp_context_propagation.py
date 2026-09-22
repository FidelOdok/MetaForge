"""Chat turns establish an McpCallContext so tool calls carry project scope
over the wire (FORGE-76).

Before this, HttpTransport never attached the X-MetaForge-* headers the
receiving server (metaforge/mcp/__main__.py's POST /mcp, MET-387) already
parses — so twin.find_by_property and FORGE-74's constraint_violations
scoping ran completely unscoped in every real chat session regardless of
--project. See test_mcp_call_context.py (context_to_headers) and
test_mcp_transport_auth.py (HttpTransport attaching them) for the lower
layers; this file covers the chat harness actually establishing the context
per turn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from api_gateway.chat import routes as chat_routes
from api_gateway.chat.models import ChatThreadRecord
from mcp_core.context import current_context


def _thread(
    scope_kind: str, scope_entity_id: str, thread_id: str | None = None
) -> ChatThreadRecord:
    now = datetime.now(UTC)
    return ChatThreadRecord(
        id=thread_id or str(uuid4()),
        title="t",
        scope_kind=scope_kind,
        scope_entity_id=scope_entity_id,
        channel_id="c",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def harness_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_routes, "chat_harness_enabled", lambda: True)


# ---------------------------------------------------------------------------
# _mcp_call_context_for_thread — pure helper
# ---------------------------------------------------------------------------


class TestMcpCallContextForThread:
    def test_project_scoped_thread_carries_project_id(self) -> None:
        project_id = uuid4()
        thread = _thread("project", str(project_id))
        ctx = chat_routes._mcp_call_context_for_thread(thread)
        assert ctx.project_id == project_id

    def test_non_project_thread_has_no_project_id(self) -> None:
        thread = _thread("assistant", "s")
        ctx = chat_routes._mcp_call_context_for_thread(thread)
        assert ctx.project_id is None

    def test_uuid_thread_id_becomes_session_id(self) -> None:
        thread_id = uuid4()
        thread = _thread("assistant", "s", thread_id=str(thread_id))
        ctx = chat_routes._mcp_call_context_for_thread(thread)
        assert ctx.session_id == thread_id

    def test_non_uuid_thread_id_does_not_raise(self) -> None:
        """Test/dev threads can use arbitrary string ids -- falling back to
        a fresh auto-generated session_id must not crash the turn."""
        thread = _thread("assistant", "s", thread_id="not-a-uuid")
        ctx = chat_routes._mcp_call_context_for_thread(thread)
        assert isinstance(ctx.session_id, UUID)

    def test_actor_id_identifies_the_harness(self) -> None:
        thread = _thread("assistant", "s")
        ctx = chat_routes._mcp_call_context_for_thread(thread)
        assert ctx.actor_id == "agent:harness-agent"


# ---------------------------------------------------------------------------
# _invoke_agent — the context is actually live during the turn
# ---------------------------------------------------------------------------


class TestInvokeAgentEstablishesContext:
    async def test_run_chat_turn_streaming_sees_the_project_context(
        self, harness_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = uuid4()
        thread = _thread("project", str(project_id))
        captured: dict[str, object] = {}

        async def _fake_turn(*args: object, **kwargs: object) -> str:
            captured["project_id"] = current_context().project_id
            return "ok"

        monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _fake_turn)
        await chat_routes._invoke_agent(thread, "hello")

        assert captured["project_id"] == project_id

    async def test_context_does_not_leak_after_the_turn(
        self, harness_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """with_context() must reset when the turn ends -- a later,
        unrelated call must not inherit a previous turn's project."""
        project_id = uuid4()
        thread = _thread("project", str(project_id))

        async def _fake_turn(*args: object, **kwargs: object) -> str:
            return "ok"

        monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _fake_turn)
        await chat_routes._invoke_agent(thread, "hello")

        assert current_context().project_id is None

    async def test_two_projects_dont_bleed_into_each_other(
        self, harness_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_a, project_b = uuid4(), uuid4()
        thread_a = _thread("project", str(project_a))
        thread_b = _thread("project", str(project_b))
        seen: list[object] = []

        async def _fake_turn(*args: object, **kwargs: object) -> str:
            seen.append(current_context().project_id)
            return "ok"

        monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _fake_turn)
        await chat_routes._invoke_agent(thread_a, "hello")
        await chat_routes._invoke_agent(thread_b, "hello")

        assert seen == [project_a, project_b]
