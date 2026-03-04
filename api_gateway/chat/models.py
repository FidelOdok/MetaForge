"""Chat persistence models -- SQLAlchemy/Pydantic models for chat tables.

These models define the PostgreSQL schema for chat_channels, chat_threads,
and chat_messages tables. In production these would use SQLAlchemy ORM;
for now we define Pydantic models that mirror the DB schema.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ChatChannelRecord(BaseModel):
    """Database record for chat_channels table."""

    id: str = Field(description="UUID primary key")
    name: str
    scope_kind: str  # session, approval, bom-entry, digital-twin-node, project
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatThreadRecord(BaseModel):
    """Database record for chat_threads table."""

    id: str = Field(description="UUID primary key")
    channel_id: str = Field(description="FK -> chat_channels.id")
    scope_kind: str
    scope_entity_id: str
    title: str
    archived: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatMessageRecord(BaseModel):
    """Database record for chat_messages table."""

    id: str = Field(description="UUID primary key")
    thread_id: str = Field(description="FK -> chat_threads.id")
    actor_id: str
    actor_kind: str  # user, agent, system
    content: str
    status: str = "sent"  # sending, sent, delivered, error
    graph_ref_node: str | None = None
    graph_ref_type: str | None = None
    graph_ref_label: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
