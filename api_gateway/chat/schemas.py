"""Request/response schemas for chat REST endpoints.

Pydantic v2 models used by FastAPI for request validation and response
serialization.  These are distinct from the persistence *Record* models
in ``models.py`` -- schemas here represent the HTTP contract.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreateThreadRequest(BaseModel):
    """Body for ``POST /api/v1/chat/threads``."""

    scope_kind: str = Field(description="Scope type (session, approval, bom-entry, ...)")
    scope_entity_id: str = Field(description="ID of the scoped entity")
    title: str | None = Field(default=None, description="Optional thread title")
    initial_message: str | None = Field(
        default=None,
        description="If provided, a first message is created automatically",
    )


class SendMessageRequest(BaseModel):
    """Body for ``POST /api/v1/chat/threads/{thread_id}/messages``."""

    content: str = Field(min_length=1, description="Message content")
    actor_id: str = Field(description="ID of the actor sending the message")
    actor_kind: str = Field(description="Actor type: user | agent | system")
    graph_ref_node: str | None = Field(default=None, description="Digital-twin node reference")
    graph_ref_type: str | None = Field(default=None, description="Digital-twin ref type")
    graph_ref_label: str | None = Field(default=None, description="Digital-twin ref label")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    """Single message inside a thread response."""

    id: str
    thread_id: str
    actor_id: str
    actor_kind: str
    content: str
    status: str
    graph_ref_node: str | None = None
    graph_ref_type: str | None = None
    graph_ref_label: str | None = None
    created_at: datetime
    updated_at: datetime


class ThreadResponse(BaseModel):
    """Full thread with its messages."""

    id: str
    channel_id: str
    scope_kind: str
    scope_entity_id: str
    title: str
    archived: bool
    created_at: datetime
    last_message_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)


class ThreadSummaryResponse(BaseModel):
    """Thread without messages -- used in list views."""

    id: str
    channel_id: str
    scope_kind: str
    scope_entity_id: str
    title: str
    archived: bool
    created_at: datetime
    last_message_at: datetime
    message_count: int = 0


class ThreadListResponse(BaseModel):
    """Paginated list of thread summaries."""

    threads: list[ThreadSummaryResponse]
    total: int
    page: int
    per_page: int


class ChannelResponse(BaseModel):
    """Single channel."""

    id: str
    name: str
    scope_kind: str
    created_at: datetime


class ChannelListResponse(BaseModel):
    """List of all channels."""

    channels: list[ChannelResponse]
