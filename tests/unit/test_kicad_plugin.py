"""Unit tests for the MetaForge KiCad plugin (MET-94).

Tests cover:
  - context_resolver: component reference -> scope mapping
  - chat_widget: message formatting
  - ws_client: message serialisation and URL construction
  - types: dataclass construction and (de)serialisation
  - plugin metadata validation

~20 tests total.
"""

from __future__ import annotations

import json
import os

import pytest

from ide_assistants.kicad_plugin.types import (
    ChatActor,
    ChatMessage,
    ChatScope,
    ChatThread,
)
from ide_assistants.kicad_plugin.context_resolver import (
    resolve_scope_from_references,
)
from ide_assistants.kicad_plugin.chat_widget import (
    color_for_role,
    format_message_html,
    ROLE_COLORS,
)
from ide_assistants.kicad_plugin.ws_client import (
    build_ws_url,
    serialize_send_message,
    serialize_create_thread,
    deserialize_message,
)


# =========================================================================
# 1. context_resolver — component -> scope mapping
# =========================================================================


class TestContextResolver:
    """Tests for resolve_scope_from_references."""

    def test_no_selection_returns_project_scope(self) -> None:
        scope = resolve_scope_from_references([])
        assert scope.kind == "project"
        assert scope.label == "Project"

    def test_single_component_returns_bom_entry(self) -> None:
        scope = resolve_scope_from_references(["U1"])
        assert scope.kind == "bom-entry"
        assert scope.entity_id == "U1"
        assert "U1" in (scope.label or "")

    def test_multiple_components_returns_bom_entry(self) -> None:
        scope = resolve_scope_from_references(["R1", "R2", "C3"])
        assert scope.kind == "bom-entry"
        assert scope.entity_id == "R1"
        assert "R1" in (scope.label or "")
        assert "R2" in (scope.label or "")

    def test_many_components_truncates_label(self) -> None:
        refs = [f"R{i}" for i in range(1, 9)]
        scope = resolve_scope_from_references(refs)
        assert scope.kind == "bom-entry"
        assert "more" in (scope.label or "")

    def test_five_or_fewer_no_truncation(self) -> None:
        refs = ["U1", "U2", "U3", "U4", "U5"]
        scope = resolve_scope_from_references(refs)
        assert "more" not in (scope.label or "")


# =========================================================================
# 2. chat_widget — message formatting
# =========================================================================


class TestChatWidgetFormatting:
    """Tests for format_message_html and color helpers."""

    def test_color_for_user(self) -> None:
        assert color_for_role("user") == "#264f78"

    def test_color_for_agent(self) -> None:
        assert color_for_role("agent") == "#1b5e20"

    def test_color_for_system(self) -> None:
        assert color_for_role("system") == "#424242"

    def test_color_for_unknown_role(self) -> None:
        assert color_for_role("unknown") == "#424242"

    def test_format_user_message_html(self) -> None:
        actor = ChatActor(kind="user", display_name="Alice")
        msg = ChatMessage.create(thread_id="t1", actor=actor, content="Hello")
        html = format_message_html(msg)
        assert "Alice" in html
        assert "Hello" in html
        assert "right" in html  # User messages align right.

    def test_format_agent_message_html(self) -> None:
        actor = ChatActor(kind="agent", display_name="MechAgent", agent_code="MECH")
        msg = ChatMessage.create(thread_id="t1", actor=actor, content="Done.")
        html = format_message_html(msg)
        assert "MechAgent" in html
        assert "Done." in html
        assert "left" in html  # Agent messages align left.

    def test_format_system_message_html(self) -> None:
        actor = ChatActor(kind="system", display_name="System")
        msg = ChatMessage.create(thread_id="t1", actor=actor, content="Thread started")
        html = format_message_html(msg)
        assert "center" in html
        assert "italic" in html

    def test_html_escaping(self) -> None:
        actor = ChatActor(kind="user", display_name="User")
        msg = ChatMessage.create(thread_id="t1", actor=actor, content="<script>alert(1)</script>")
        html = format_message_html(msg)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# =========================================================================
# 3. ws_client — message serialisation and URL construction
# =========================================================================


class TestWSClient:
    """Tests for ws_client pure functions."""

    def test_build_ws_url(self) -> None:
        url = build_ws_url("ws://localhost:8000", "sess-123")
        assert url == "ws://localhost:8000/api/v1/assistant/ws/sess-123"

    def test_build_ws_url_strips_trailing_slash(self) -> None:
        url = build_ws_url("ws://localhost:8000/", "sess-abc")
        assert url == "ws://localhost:8000/api/v1/assistant/ws/sess-abc"

    def test_serialize_send_message(self) -> None:
        raw = serialize_send_message("t1", "Hello world")
        data = json.loads(raw)
        assert data["type"] == "send_message"
        assert data["threadId"] == "t1"
        assert data["content"] == "Hello world"

    def test_serialize_create_thread(self) -> None:
        raw = serialize_create_thread("My Thread", "session", "sess-1")
        data = json.loads(raw)
        assert data["type"] == "create_thread"
        assert data["title"] == "My Thread"
        assert data["scope"]["kind"] == "session"
        assert data["scope"]["entityId"] == "sess-1"

    def test_serialize_create_thread_no_entity(self) -> None:
        raw = serialize_create_thread("Thread", "project")
        data = json.loads(raw)
        assert "entityId" not in data["scope"]

    def test_deserialize_message_payload(self) -> None:
        raw = json.dumps({
            "type": "message",
            "message": {
                "id": "m1",
                "threadId": "t1",
                "actor": {"kind": "agent", "displayName": "Agent"},
                "content": "Reply",
                "createdAt": "2024-01-01T00:00:00Z",
            },
        })
        msg = deserialize_message(raw)
        assert msg is not None
        assert msg.content == "Reply"
        assert msg.actor.kind == "agent"

    def test_deserialize_message_top_level(self) -> None:
        raw = json.dumps({
            "id": "m2",
            "threadId": "t1",
            "actor": {"kind": "user", "displayName": "Bob"},
            "content": "Direct",
            "createdAt": "2024-01-01T00:00:00Z",
        })
        msg = deserialize_message(raw)
        assert msg is not None
        assert msg.content == "Direct"

    def test_deserialize_invalid_json(self) -> None:
        assert deserialize_message("not json") is None

    def test_deserialize_unrelated_frame(self) -> None:
        raw = json.dumps({"type": "heartbeat"})
        assert deserialize_message(raw) is None


# =========================================================================
# 4. types — dataclass construction and serialisation
# =========================================================================


class TestTypes:
    """Tests for the plugin data types."""

    def test_chat_actor_to_dict(self) -> None:
        actor = ChatActor(kind="agent", display_name="MECH", agent_code="M")
        d = actor.to_dict()
        assert d["kind"] == "agent"
        assert d["displayName"] == "MECH"
        assert d["agentCode"] == "M"

    def test_chat_actor_from_dict(self) -> None:
        actor = ChatActor.from_dict({"kind": "user", "displayName": "Alice"})
        assert actor.kind == "user"
        assert actor.display_name == "Alice"
        assert actor.agent_code is None

    def test_chat_scope_round_trip(self) -> None:
        scope = ChatScope(kind="bom-entry", entity_id="U1", label="BOM: U1")
        d = scope.to_dict()
        restored = ChatScope.from_dict(d)
        assert restored.kind == scope.kind
        assert restored.entity_id == scope.entity_id
        assert restored.label == scope.label

    def test_chat_message_create_generates_id(self) -> None:
        actor = ChatActor(kind="user", display_name="Test")
        msg = ChatMessage.create(thread_id="t1", actor=actor, content="Hi")
        assert msg.id  # Non-empty UUID
        assert msg.thread_id == "t1"
        assert msg.created_at  # Non-empty ISO timestamp

    def test_chat_message_json_round_trip(self) -> None:
        actor = ChatActor(kind="agent", display_name="Agent", agent_code="A")
        msg = ChatMessage.create(thread_id="t1", actor=actor, content="Test")
        raw = msg.to_json()
        restored = ChatMessage.from_json(raw)
        assert restored.content == msg.content
        assert restored.actor.kind == "agent"

    def test_chat_thread_create(self) -> None:
        scope = ChatScope(kind="project")
        thread = ChatThread.create(title="Test Thread", scope=scope)
        assert thread.id
        assert thread.title == "Test Thread"
        assert thread.scope.kind == "project"
        assert thread.created_at
        assert thread.messages == []

    def test_chat_thread_to_dict(self) -> None:
        scope = ChatScope(kind="session", entity_id="s1")
        thread = ChatThread.create(title="Thread", scope=scope)
        d = thread.to_dict()
        assert d["title"] == "Thread"
        assert d["scope"]["kind"] == "session"
        assert isinstance(d["messages"], list)


# =========================================================================
# 5. Plugin metadata
# =========================================================================


class TestPluginMetadata:
    """Validate the metadata.json content."""

    @pytest.fixture()
    def metadata(self) -> dict:
        metadata_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "ide_assistants",
            "kicad_plugin",
            "metadata.json",
        )
        with open(metadata_path) as f:
            return json.load(f)

    def test_name_is_set(self, metadata: dict) -> None:
        assert metadata["name"] == "MetaForge Chat"

    def test_version_is_semver(self, metadata: dict) -> None:
        import re

        assert re.match(r"\d+\.\d+\.\d+", metadata["version"])

    def test_author_is_metaforge(self, metadata: dict) -> None:
        assert metadata["author"] == "MetaForge"

    def test_entry_point_defined(self, metadata: dict) -> None:
        assert metadata["entry_point"] == "__init__.py"

    def test_kicad_version_specified(self, metadata: dict) -> None:
        assert "kicad_version" in metadata
