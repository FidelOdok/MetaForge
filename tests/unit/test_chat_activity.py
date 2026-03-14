"""Tests for the handle_chat_message Temporal activity (MET-81)."""

from __future__ import annotations

import pytest

from api_gateway.chat.activity import (
    ChatContextAssembler,
    HandleChatMessageInput,
    HandleChatMessageOutput,
    _build_typing_event,
    _generate_response,
    handle_chat_message,
)
from orchestrator.event_bus.events import EventType

# ---------------------------------------------------------------------------
# ChatContextAssembler
# ---------------------------------------------------------------------------


class TestChatContextAssembler:
    """Verify context assembly for each supported scope_kind."""

    def setup_method(self) -> None:
        self.assembler = ChatContextAssembler()

    def test_session_scope(self) -> None:
        """Session scope should return traces, work_products, recent_mutations."""
        ctx = self.assembler.assemble("session", "sess-001")
        assert ctx["scope_kind"] == "session"
        assert ctx["session_id"] == "sess-001"
        assert "traces" in ctx
        assert "work_products" in ctx
        assert "recent_mutations" in ctx

    def test_approval_scope(self) -> None:
        """Approval scope should return diffs, description, session_context."""
        ctx = self.assembler.assemble("approval", "appr-002")
        assert ctx["scope_kind"] == "approval"
        assert ctx["approval_id"] == "appr-002"
        assert "diffs" in ctx
        assert "description" in ctx
        assert "session_context" in ctx

    def test_bom_entry_scope(self) -> None:
        """BOM-entry scope should return properties, alternates, risk, neighbors."""
        ctx = self.assembler.assemble("bom-entry", "bom-003")
        assert ctx["scope_kind"] == "bom-entry"
        assert ctx["bom_entry_id"] == "bom-003"
        assert "properties" in ctx
        assert "alternates" in ctx
        assert "supply_chain_risk" in ctx
        assert "graph_neighbors" in ctx

    def test_digital_twin_node_scope(self) -> None:
        """Digital-twin-node scope should return node properties, requirements, sim."""
        ctx = self.assembler.assemble("digital-twin-node", "node-004")
        assert ctx["scope_kind"] == "digital-twin-node"
        assert ctx["node_id"] == "node-004"
        assert "properties" in ctx
        assert "connected_requirements" in ctx
        assert "simulation_results" in ctx

    def test_project_scope(self) -> None:
        """Project scope should return sessions, gate readiness, approvals."""
        ctx = self.assembler.assemble("project", "proj-005")
        assert ctx["scope_kind"] == "project"
        assert ctx["project_id"] == "proj-005"
        assert "recent_sessions" in ctx
        assert "gate_readiness" in ctx
        assert "open_approvals" in ctx

    def test_unsupported_scope_raises(self) -> None:
        """Unknown scope_kind should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported scope_kind"):
            self.assembler.assemble("nonexistent", "id-999")

    def test_supported_scopes_constant(self) -> None:
        """SUPPORTED_SCOPES should contain exactly the five known scope kinds."""
        expected = {"session", "approval", "bom-entry", "digital-twin-node", "project"}
        assert ChatContextAssembler.SUPPORTED_SCOPES == expected


# ---------------------------------------------------------------------------
# HandleChatMessageInput
# ---------------------------------------------------------------------------


class TestHandleChatMessageInput:
    """Verify the input Pydantic model."""

    def test_valid_input(self) -> None:
        """Input model should accept all required fields."""
        inp = HandleChatMessageInput(
            thread_id="t-1",
            actor_id="user-1",
            actor_kind="user",
            content="Hello",
            scope_kind="session",
            scope_entity_id="sess-abc",
        )
        assert inp.thread_id == "t-1"
        assert inp.actor_id == "user-1"
        assert inp.actor_kind == "user"
        assert inp.content == "Hello"
        assert inp.scope_kind == "session"
        assert inp.scope_entity_id == "sess-abc"

    def test_empty_content_allowed(self) -> None:
        """An empty string for content should still be valid Pydantic input."""
        inp = HandleChatMessageInput(
            thread_id="t-2",
            actor_id="agent-1",
            actor_kind="agent",
            content="",
            scope_kind="project",
            scope_entity_id="proj-1",
        )
        assert inp.content == ""

    def test_missing_field_raises(self) -> None:
        """Omitting a required field should raise a ValidationError."""
        with pytest.raises(Exception):  # noqa: B017
            HandleChatMessageInput(  # type: ignore[call-arg]
                thread_id="t-3",
                actor_id="user-1",
                # actor_kind is missing
                content="Hi",
                scope_kind="session",
                scope_entity_id="sess-1",
            )


# ---------------------------------------------------------------------------
# HandleChatMessageOutput
# ---------------------------------------------------------------------------


class TestHandleChatMessageOutput:
    """Verify the output Pydantic model."""

    def test_valid_output(self) -> None:
        """Output model should accept all required fields."""
        out = HandleChatMessageOutput(
            message_id="msg-1",
            response_content="Acknowledged.",
            context_used={"scope_kind": "session"},
            duration_ms=42.5,
        )
        assert out.message_id == "msg-1"
        assert out.response_content == "Acknowledged."
        assert out.context_used == {"scope_kind": "session"}
        assert out.duration_ms == 42.5

    def test_empty_context(self) -> None:
        """An empty context_used dict should be valid."""
        out = HandleChatMessageOutput(
            message_id="msg-2",
            response_content="OK",
            context_used={},
            duration_ms=0.0,
        )
        assert out.context_used == {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestBuildTypingEvent:
    """Verify the _build_typing_event helper."""

    def test_typing_start(self) -> None:
        """Typing-start event should have is_typing=True."""
        event = _build_typing_event("t-1", "agent-1", is_typing=True)
        assert event.is_typing is True
        assert event.thread_id == "t-1"
        assert event.actor_id == "agent-1"
        assert event.type == EventType.CHAT_AGENT_TYPING
        assert event.source == "chat-activity"

    def test_typing_stop(self) -> None:
        """Typing-stop event should have is_typing=False."""
        event = _build_typing_event("t-2", "agent-2", is_typing=False)
        assert event.is_typing is False


class TestGenerateResponse:
    """Verify the stub _generate_response helper."""

    def test_includes_scope_kind(self) -> None:
        """Response should mention the scope kind."""
        ctx = {"scope_kind": "session", "session_id": "s-1"}
        result = _generate_response("Hello", ctx)
        assert "session" in result

    def test_includes_user_content(self) -> None:
        """Response should echo the user content."""
        ctx = {"scope_kind": "project", "project_id": "p-1"}
        result = _generate_response("What is the status?", ctx)
        assert "What is the status?" in result

    def test_includes_context_keys(self) -> None:
        """Response should list context keys (excluding scope_kind)."""
        ctx = {"scope_kind": "bom-entry", "bom_entry_id": "b-1", "alternates": []}
        result = _generate_response("Info?", ctx)
        assert "alternates" in result
        assert "bom_entry_id" in result


# ---------------------------------------------------------------------------
# handle_chat_message (end-to-end)
# ---------------------------------------------------------------------------


class TestHandleChatMessage:
    """End-to-end tests for the Temporal activity function."""

    async def test_basic_session_scope(self) -> None:
        """Activity should return a valid output for session scope."""
        inp = HandleChatMessageInput(
            thread_id="t-100",
            actor_id="user-1",
            actor_kind="user",
            content="Show me the latest traces.",
            scope_kind="session",
            scope_entity_id="sess-100",
        )
        out = await handle_chat_message(inp)

        assert isinstance(out, HandleChatMessageOutput)
        assert out.message_id  # non-empty UUID
        assert "session" in out.response_content
        assert out.context_used["scope_kind"] == "session"
        assert out.context_used["session_id"] == "sess-100"
        assert out.duration_ms >= 0.0

    async def test_approval_scope(self) -> None:
        """Activity should work with approval scope."""
        inp = HandleChatMessageInput(
            thread_id="t-101",
            actor_id="user-2",
            actor_kind="user",
            content="Explain the diff.",
            scope_kind="approval",
            scope_entity_id="appr-200",
        )
        out = await handle_chat_message(inp)

        assert out.context_used["scope_kind"] == "approval"
        assert out.context_used["approval_id"] == "appr-200"
        assert "Explain the diff." in out.response_content

    async def test_project_scope(self) -> None:
        """Activity should work with project scope."""
        inp = HandleChatMessageInput(
            thread_id="t-102",
            actor_id="agent-3",
            actor_kind="agent",
            content="Gate readiness?",
            scope_kind="project",
            scope_entity_id="proj-300",
        )
        out = await handle_chat_message(inp)

        assert out.context_used["scope_kind"] == "project"
        assert "project" in out.response_content

    async def test_bom_entry_scope(self) -> None:
        """Activity should work with bom-entry scope."""
        inp = HandleChatMessageInput(
            thread_id="t-103",
            actor_id="user-1",
            actor_kind="user",
            content="Alternates?",
            scope_kind="bom-entry",
            scope_entity_id="bom-400",
        )
        out = await handle_chat_message(inp)

        assert out.context_used["scope_kind"] == "bom-entry"
        assert out.context_used["bom_entry_id"] == "bom-400"

    async def test_digital_twin_node_scope(self) -> None:
        """Activity should work with digital-twin-node scope."""
        inp = HandleChatMessageInput(
            thread_id="t-104",
            actor_id="user-1",
            actor_kind="user",
            content="Simulation results?",
            scope_kind="digital-twin-node",
            scope_entity_id="node-500",
        )
        out = await handle_chat_message(inp)

        assert out.context_used["scope_kind"] == "digital-twin-node"
        assert out.context_used["node_id"] == "node-500"

    async def test_unsupported_scope_raises(self) -> None:
        """Activity should propagate ValueError for unknown scope_kind."""
        inp = HandleChatMessageInput(
            thread_id="t-105",
            actor_id="user-1",
            actor_kind="user",
            content="Hello",
            scope_kind="nonexistent",
            scope_entity_id="id-999",
        )
        with pytest.raises(ValueError, match="Unsupported scope_kind"):
            await handle_chat_message(inp)

    async def test_empty_content(self) -> None:
        """Activity should handle empty content without error."""
        inp = HandleChatMessageInput(
            thread_id="t-106",
            actor_id="user-1",
            actor_kind="user",
            content="",
            scope_kind="session",
            scope_entity_id="sess-600",
        )
        out = await handle_chat_message(inp)

        assert isinstance(out, HandleChatMessageOutput)
        assert out.response_content  # stub always produces output

    async def test_custom_assembler(self) -> None:
        """Activity should use a custom assembler when provided."""

        class CustomAssembler(ChatContextAssembler):
            def _assemble_session(self, entity_id: str) -> dict:
                return {"scope_kind": "session", "custom": True, "session_id": entity_id}

        inp = HandleChatMessageInput(
            thread_id="t-107",
            actor_id="user-1",
            actor_kind="user",
            content="Custom test",
            scope_kind="session",
            scope_entity_id="sess-700",
        )
        out = await handle_chat_message(inp, assembler=CustomAssembler())

        assert out.context_used.get("custom") is True

    async def test_duration_is_positive(self) -> None:
        """Activity duration_ms should be non-negative."""
        inp = HandleChatMessageInput(
            thread_id="t-108",
            actor_id="user-1",
            actor_kind="user",
            content="Timing test",
            scope_kind="session",
            scope_entity_id="sess-800",
        )
        out = await handle_chat_message(inp)

        assert out.duration_ms >= 0.0
