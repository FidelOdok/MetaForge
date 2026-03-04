"""Chat REST endpoints for the MetaForge Gateway.

Provides CRUD operations on chat channels, threads, and messages.
All state is held in an in-memory ``ChatStore`` for now; production
will swap in a PostgreSQL-backed implementation.

Endpoints live under ``/api/v1/chat``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from api_gateway.chat.models import (
    ChatChannelRecord,
    ChatMessageRecord,
    ChatThreadRecord,
)
from api_gateway.chat.schemas import (
    ChannelListResponse,
    ChannelResponse,
    CreateThreadRequest,
    MessageResponse,
    SendMessageRequest,
    ThreadListResponse,
    ThreadResponse,
    ThreadSummaryResponse,
)

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"name": "Session Chat", "scope_kind": "session"},
    {"name": "Approval Chat", "scope_kind": "approval"},
    {"name": "BOM Discussion", "scope_kind": "bom-entry"},
    {"name": "Digital Twin", "scope_kind": "digital-twin-node"},
    {"name": "Project Chat", "scope_kind": "project"},
]


class ChatStore:
    """Dict-backed chat storage (channels, threads, messages).

    This is intentionally simple -- production code will use PostgreSQL
    via SQLAlchemy / asyncpg.
    """

    def __init__(self) -> None:
        self.channels: dict[str, ChatChannelRecord] = {}
        self.threads: dict[str, ChatThreadRecord] = {}
        self.messages: dict[str, list[ChatMessageRecord]] = {}

    @classmethod
    def create(cls) -> ChatStore:
        """Return a new store pre-populated with the default channels."""
        store = cls()
        for ch in _DEFAULT_CHANNELS:
            channel = ChatChannelRecord(
                id=str(uuid4()),
                name=ch["name"],
                scope_kind=ch["scope_kind"],
            )
            store.channels[channel.id] = channel
        return store

    # -- helpers ----------------------------------------------------------

    def channel_for_scope(self, scope_kind: str) -> ChatChannelRecord | None:
        """Return the first channel matching *scope_kind*, or ``None``."""
        for ch in self.channels.values():
            if ch.scope_kind == scope_kind:
                return ch
        return None

    def message_count(self, thread_id: str) -> int:
        return len(self.messages.get(thread_id, []))


# ---------------------------------------------------------------------------
# Module-level store & router
# ---------------------------------------------------------------------------

store = ChatStore.create()

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Channel endpoints
# ---------------------------------------------------------------------------


@router.get("/channels", response_model=ChannelListResponse)
def list_channels() -> ChannelListResponse:
    """Return all available chat channels."""
    channels = [
        ChannelResponse(
            id=ch.id,
            name=ch.name,
            scope_kind=ch.scope_kind,
            created_at=ch.created_at,
        )
        for ch in store.channels.values()
    ]
    return ChannelListResponse(channels=channels)


# ---------------------------------------------------------------------------
# Thread endpoints
# ---------------------------------------------------------------------------


@router.get("/threads", response_model=ThreadListResponse)
def list_threads(
    channel_id: str | None = Query(default=None, description="Filter by channel ID"),
    scope_kind: str | None = Query(default=None, description="Filter by scope kind"),
    entity_id: str | None = Query(default=None, description="Filter by scope entity ID"),
    include_archived: bool = Query(default=False, description="Include archived threads"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(default=20, ge=1, le=100, description="Results per page"),
) -> ThreadListResponse:
    """List threads with optional filtering and pagination."""
    threads = list(store.threads.values())

    # -- filtering --------------------------------------------------------
    if not include_archived:
        threads = [t for t in threads if not t.archived]
    if channel_id is not None:
        threads = [t for t in threads if t.channel_id == channel_id]
    if scope_kind is not None:
        threads = [t for t in threads if t.scope_kind == scope_kind]
    if entity_id is not None:
        threads = [t for t in threads if t.scope_entity_id == entity_id]

    # -- sort by last_message_at descending -------------------------------
    threads.sort(key=lambda t: t.last_message_at, reverse=True)

    total = len(threads)

    # -- pagination -------------------------------------------------------
    start = (page - 1) * per_page
    page_threads = threads[start : start + per_page]

    summaries = [
        ThreadSummaryResponse(
            id=t.id,
            channel_id=t.channel_id,
            scope_kind=t.scope_kind,
            scope_entity_id=t.scope_entity_id,
            title=t.title,
            archived=t.archived,
            created_at=t.created_at,
            last_message_at=t.last_message_at,
            message_count=store.message_count(t.id),
        )
        for t in page_threads
    ]

    return ThreadListResponse(
        threads=summaries,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
def get_thread(thread_id: str) -> ThreadResponse:
    """Return a single thread with all its messages."""
    thread = store.threads.get(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    msgs = store.messages.get(thread_id, [])
    return ThreadResponse(
        id=thread.id,
        channel_id=thread.channel_id,
        scope_kind=thread.scope_kind,
        scope_entity_id=thread.scope_entity_id,
        title=thread.title,
        archived=thread.archived,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        messages=[
            MessageResponse(
                id=m.id,
                thread_id=m.thread_id,
                actor_id=m.actor_id,
                actor_kind=m.actor_kind,
                content=m.content,
                status=m.status,
                graph_ref_node=m.graph_ref_node,
                graph_ref_type=m.graph_ref_type,
                graph_ref_label=m.graph_ref_label,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in msgs
        ],
    )


@router.post("/threads", response_model=ThreadResponse, status_code=201)
def create_thread(body: CreateThreadRequest) -> ThreadResponse:
    """Create a new thread, optionally with an initial message."""
    # Resolve channel by scope_kind
    channel = store.channel_for_scope(body.scope_kind)
    if channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"No channel found for scope_kind={body.scope_kind!r}",
        )

    now = datetime.now(UTC)
    thread_id = str(uuid4())
    title = body.title or f"Thread {thread_id[:8]}"

    thread = ChatThreadRecord(
        id=thread_id,
        channel_id=channel.id,
        scope_kind=body.scope_kind,
        scope_entity_id=body.scope_entity_id,
        title=title,
        created_at=now,
        last_message_at=now,
    )
    store.threads[thread_id] = thread
    store.messages[thread_id] = []

    messages: list[MessageResponse] = []

    if body.initial_message:
        msg = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=thread_id,
            actor_id="system",
            actor_kind="system",
            content=body.initial_message,
            created_at=now,
            updated_at=now,
        )
        store.messages[thread_id].append(msg)
        messages.append(
            MessageResponse(
                id=msg.id,
                thread_id=msg.thread_id,
                actor_id=msg.actor_id,
                actor_kind=msg.actor_kind,
                content=msg.content,
                status=msg.status,
                graph_ref_node=msg.graph_ref_node,
                graph_ref_type=msg.graph_ref_type,
                graph_ref_label=msg.graph_ref_label,
                created_at=msg.created_at,
                updated_at=msg.updated_at,
            )
        )

    return ThreadResponse(
        id=thread.id,
        channel_id=thread.channel_id,
        scope_kind=thread.scope_kind,
        scope_entity_id=thread.scope_entity_id,
        title=thread.title,
        archived=thread.archived,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Message endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageResponse,
    status_code=201,
)
def send_message(thread_id: str, body: SendMessageRequest) -> MessageResponse:
    """Append a message to an existing thread."""
    thread = store.threads.get(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    now = datetime.now(UTC)
    msg = ChatMessageRecord(
        id=str(uuid4()),
        thread_id=thread_id,
        actor_id=body.actor_id,
        actor_kind=body.actor_kind,
        content=body.content,
        graph_ref_node=body.graph_ref_node,
        graph_ref_type=body.graph_ref_type,
        graph_ref_label=body.graph_ref_label,
        created_at=now,
        updated_at=now,
    )
    store.messages.setdefault(thread_id, []).append(msg)

    # Update thread timestamp
    thread.last_message_at = now

    return MessageResponse(
        id=msg.id,
        thread_id=msg.thread_id,
        actor_id=msg.actor_id,
        actor_kind=msg.actor_kind,
        content=msg.content,
        status=msg.status,
        graph_ref_node=msg.graph_ref_node,
        graph_ref_type=msg.graph_ref_type,
        graph_ref_label=msg.graph_ref_label,
        created_at=msg.created_at,
        updated_at=msg.updated_at,
    )
