"""Event types and models for the MetaForge event bus."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """All event types published on the event bus."""

    # Digital Twin events
    ARTIFACT_CREATED = "twin.artifact.created"
    ARTIFACT_UPDATED = "twin.artifact.updated"
    ARTIFACT_DELETED = "twin.artifact.deleted"
    CONSTRAINT_VIOLATED = "twin.constraint.violated"
    BRANCH_CREATED = "twin.branch.created"
    BRANCH_MERGED = "twin.branch.merged"

    # Session events
    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"
    SESSION_FAILED = "session.failed"

    # Agent events
    AGENT_TASK_STARTED = "agent.task.started"
    AGENT_TASK_COMPLETED = "agent.task.completed"
    AGENT_TASK_FAILED = "agent.task.failed"

    # Approval events
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"

    # Chat events (MET-79)
    CHAT_MESSAGE_SENT = "chat.message.sent"
    CHAT_MESSAGE_CHUNK = "chat.message.chunk"
    CHAT_THREAD_CREATED = "chat.thread.created"
    CHAT_AGENT_TYPING = "chat.agent.typing"


class Event(BaseModel):
    """Base event model for the event bus."""

    id: str = Field(description="Unique event ID (UUID)")
    type: EventType
    timestamp: str = Field(description="ISO 8601 timestamp")
    source: str = Field(description="Source service/agent ID")
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessageEvent(Event):
    """Event payload for chat message events."""

    thread_id: str
    actor_id: str
    actor_kind: str  # "user", "agent", "system"
    content: str
    graph_ref: dict[str, str] | None = None


class ChatThreadEvent(Event):
    """Event payload for chat thread lifecycle events."""

    thread_id: str
    scope_kind: str
    scope_entity_id: str
    title: str


class ChatTypingEvent(Event):
    """Event payload for agent typing indicator events."""

    thread_id: str
    actor_id: str
    agent_code: str
    is_typing: bool
