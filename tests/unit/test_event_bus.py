"""Tests for the event bus: event types, models, topic config, and chat models."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from api_gateway.chat.models import (
    ChatChannelRecord,
    ChatMessageRecord,
    ChatThreadRecord,
)
from orchestrator.event_bus.bus import (
    ALL_TOPICS,
    TOPIC_AGENT_CHAT,
    TOPIC_AGENT_EVENTS,
    TOPIC_APPROVAL_EVENTS,
    TOPIC_SESSION_EVENTS,
    TOPIC_TWIN_EVENTS,
    TopicConfig,
)
from orchestrator.event_bus.events import (
    ChatMessageEvent,
    ChatThreadEvent,
    ChatTypingEvent,
    Event,
    EventType,
)

# ---------------------------------------------------------------------------
# TestEventType
# ---------------------------------------------------------------------------


class TestEventType:
    """Verify the EventType enum values and membership."""

    def test_event_type_values(self) -> None:
        """All event types should have dotted-string values."""
        for member in EventType:
            assert "." in member.value, f"{member.name} value should be dot-separated"

    def test_chat_event_types_exist(self) -> None:
        """MET-79 chat event types must be present."""
        assert EventType.CHAT_MESSAGE_SENT == "chat.message.sent"
        assert EventType.CHAT_MESSAGE_CHUNK == "chat.message.chunk"
        assert EventType.CHAT_THREAD_CREATED == "chat.thread.created"
        assert EventType.CHAT_AGENT_TYPING == "chat.agent.typing"

    def test_twin_event_types_exist(self) -> None:
        """Digital Twin event types must be present."""
        assert EventType.ARTIFACT_CREATED == "twin.artifact.created"
        assert EventType.ARTIFACT_UPDATED == "twin.artifact.updated"
        assert EventType.ARTIFACT_DELETED == "twin.artifact.deleted"
        assert EventType.CONSTRAINT_VIOLATED == "twin.constraint.violated"
        assert EventType.BRANCH_CREATED == "twin.branch.created"
        assert EventType.BRANCH_MERGED == "twin.branch.merged"

    def test_session_event_types_exist(self) -> None:
        """Session lifecycle event types must be present."""
        assert EventType.SESSION_STARTED == "session.started"
        assert EventType.SESSION_COMPLETED == "session.completed"
        assert EventType.SESSION_FAILED == "session.failed"

    def test_agent_event_types_exist(self) -> None:
        """Agent task event types must be present."""
        assert EventType.AGENT_TASK_STARTED == "agent.task.started"
        assert EventType.AGENT_TASK_COMPLETED == "agent.task.completed"
        assert EventType.AGENT_TASK_FAILED == "agent.task.failed"

    def test_approval_event_types_exist(self) -> None:
        """Approval workflow event types must be present."""
        assert EventType.APPROVAL_REQUESTED == "approval.requested"
        assert EventType.APPROVAL_GRANTED == "approval.granted"
        assert EventType.APPROVAL_REJECTED == "approval.rejected"


# ---------------------------------------------------------------------------
# TestEvent
# ---------------------------------------------------------------------------


class TestEvent:
    """Verify Event and chat-specific event models."""

    def test_event_model(self) -> None:
        """Base Event model should accept all required fields."""
        event = Event(
            id=str(uuid4()),
            type=EventType.ARTIFACT_CREATED,
            timestamp="2026-03-04T12:00:00Z",
            source="twin-core",
        )
        assert event.type == EventType.ARTIFACT_CREATED
        assert event.data == {}
        assert event.metadata == {}

    def test_event_with_data(self) -> None:
        """Event should accept optional data and metadata dicts."""
        event = Event(
            id=str(uuid4()),
            type=EventType.SESSION_STARTED,
            timestamp="2026-03-04T12:00:00Z",
            source="orchestrator",
            data={"session_id": "abc-123"},
            metadata={"trace_id": "trace-456"},
        )
        assert event.data["session_id"] == "abc-123"
        assert event.metadata["trace_id"] == "trace-456"

    def test_chat_message_event(self) -> None:
        """ChatMessageEvent should include thread_id, actor fields, and content."""
        event = ChatMessageEvent(
            id=str(uuid4()),
            type=EventType.CHAT_MESSAGE_SENT,
            timestamp="2026-03-04T12:00:00Z",
            source="mechanical-agent",
            thread_id="thread-001",
            actor_id="agent-mech",
            actor_kind="agent",
            content="Stress analysis complete.",
        )
        assert event.thread_id == "thread-001"
        assert event.actor_kind == "agent"
        assert event.content == "Stress analysis complete."
        assert event.graph_ref is None

    def test_chat_message_event_with_graph_ref(self) -> None:
        """ChatMessageEvent should optionally accept graph_ref."""
        event = ChatMessageEvent(
            id=str(uuid4()),
            type=EventType.CHAT_MESSAGE_SENT,
            timestamp="2026-03-04T12:00:00Z",
            source="mechanical-agent",
            thread_id="thread-001",
            actor_id="agent-mech",
            actor_kind="agent",
            content="Node updated.",
            graph_ref={"node_id": "n-42", "type": "mesh"},
        )
        assert event.graph_ref is not None
        assert event.graph_ref["node_id"] == "n-42"

    def test_chat_thread_event(self) -> None:
        """ChatThreadEvent should include thread metadata fields."""
        event = ChatThreadEvent(
            id=str(uuid4()),
            type=EventType.CHAT_THREAD_CREATED,
            timestamp="2026-03-04T12:00:00Z",
            source="api-gateway",
            thread_id="thread-002",
            scope_kind="session",
            scope_entity_id="session-abc",
            title="Bracket stress discussion",
        )
        assert event.thread_id == "thread-002"
        assert event.scope_kind == "session"
        assert event.title == "Bracket stress discussion"

    def test_chat_typing_event(self) -> None:
        """ChatTypingEvent should include typing indicator fields."""
        event = ChatTypingEvent(
            id=str(uuid4()),
            type=EventType.CHAT_AGENT_TYPING,
            timestamp="2026-03-04T12:00:00Z",
            source="mechanical-agent",
            thread_id="thread-003",
            actor_id="agent-mech",
            agent_code="mechanical",
            is_typing=True,
        )
        assert event.is_typing is True
        assert event.agent_code == "mechanical"


# ---------------------------------------------------------------------------
# TestTopicConfig
# ---------------------------------------------------------------------------


class TestTopicConfig:
    """Verify Kafka topic configuration models."""

    def test_topic_defaults(self) -> None:
        """TopicConfig should have sensible defaults."""
        topic = TopicConfig(name="test.topic")
        assert topic.partitions == 6
        assert topic.replication_factor == 1
        assert topic.retention_ms == 604800000  # 7 days
        assert topic.cleanup_policy == "delete"

    def test_chat_topic_retention_90_days(self) -> None:
        """MET-79: agent.chat topic must have 90-day retention."""
        assert TOPIC_AGENT_CHAT.name == "agent.chat"
        assert TOPIC_AGENT_CHAT.retention_ms == 7776000000  # 90 days

    def test_twin_topic_config(self) -> None:
        """Twin events topic should have 12 partitions and 7-day retention."""
        assert TOPIC_TWIN_EVENTS.name == "twin.events"
        assert TOPIC_TWIN_EVENTS.partitions == 12
        assert TOPIC_TWIN_EVENTS.retention_ms == 604800000

    def test_session_topic_config(self) -> None:
        """Session events topic should have 30-day retention."""
        assert TOPIC_SESSION_EVENTS.name == "session.events"
        assert TOPIC_SESSION_EVENTS.retention_ms == 2592000000

    def test_agent_topic_config(self) -> None:
        """Agent events topic should have 30-day retention."""
        assert TOPIC_AGENT_EVENTS.name == "agent.events"
        assert TOPIC_AGENT_EVENTS.retention_ms == 2592000000

    def test_approval_topic_config(self) -> None:
        """Approval events topic should have 90-day retention."""
        assert TOPIC_APPROVAL_EVENTS.name == "approval.events"
        assert TOPIC_APPROVAL_EVENTS.retention_ms == 7776000000

    def test_all_topics_list(self) -> None:
        """ALL_TOPICS should contain exactly five topic configs."""
        assert len(ALL_TOPICS) == 5
        names = {t.name for t in ALL_TOPICS}
        assert names == {
            "twin.events",
            "session.events",
            "agent.events",
            "approval.events",
            "agent.chat",
        }


# ---------------------------------------------------------------------------
# TestChatModels (MET-80)
# ---------------------------------------------------------------------------


class TestChatModels:
    """Verify chat persistence Pydantic models (MET-80)."""

    def test_channel_record(self) -> None:
        """ChatChannelRecord should accept required fields and default created_at."""
        record = ChatChannelRecord(
            id=str(uuid4()),
            name="general",
            scope_kind="project",
        )
        assert record.name == "general"
        assert record.scope_kind == "project"
        assert isinstance(record.created_at, datetime)

    def test_thread_record(self) -> None:
        """ChatThreadRecord should accept all fields with defaults."""
        record = ChatThreadRecord(
            id=str(uuid4()),
            channel_id=str(uuid4()),
            scope_kind="session",
            scope_entity_id="session-abc",
            title="Design review thread",
        )
        assert record.archived is False
        assert isinstance(record.last_message_at, datetime)
        assert record.title == "Design review thread"

    def test_thread_record_archived(self) -> None:
        """ChatThreadRecord should accept archived=True."""
        record = ChatThreadRecord(
            id=str(uuid4()),
            channel_id=str(uuid4()),
            scope_kind="approval",
            scope_entity_id="approval-123",
            title="Archived thread",
            archived=True,
        )
        assert record.archived is True

    def test_message_record(self) -> None:
        """ChatMessageRecord should accept required fields with defaults."""
        record = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=str(uuid4()),
            actor_id="user-1",
            actor_kind="user",
            content="Hello, reviewing the bracket design.",
        )
        assert record.status == "sent"
        assert record.graph_ref_node is None
        assert record.graph_ref_type is None
        assert record.graph_ref_label is None
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.updated_at, datetime)

    def test_message_record_with_graph_ref(self) -> None:
        """ChatMessageRecord should accept optional graph reference fields."""
        record = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=str(uuid4()),
            actor_id="agent-mech",
            actor_kind="agent",
            content="Linked to mesh node.",
            graph_ref_node="node-42",
            graph_ref_type="mesh",
            graph_ref_label="Bracket FEA Mesh",
        )
        assert record.graph_ref_node == "node-42"
        assert record.graph_ref_type == "mesh"
        assert record.graph_ref_label == "Bracket FEA Mesh"

    def test_message_record_status_values(self) -> None:
        """ChatMessageRecord should accept all valid status values."""
        for status in ("sending", "sent", "delivered", "error"):
            record = ChatMessageRecord(
                id=str(uuid4()),
                thread_id=str(uuid4()),
                actor_id="user-1",
                actor_kind="user",
                content="Test",
                status=status,
            )
            assert record.status == status

    def test_channel_created_at_is_utc(self) -> None:
        """ChatChannelRecord created_at should be timezone-aware UTC."""
        record = ChatChannelRecord(
            id=str(uuid4()),
            name="test",
            scope_kind="session",
        )
        assert record.created_at.tzinfo is not None
