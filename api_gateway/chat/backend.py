"""Chat storage backends — in-memory and PostgreSQL.

Provides a ``ChatBackend`` protocol that the chat routes call.
Two implementations:

- ``InMemoryChatBackend`` — dict-backed, used when ``DATABASE_URL`` is unset
- ``PgChatBackend`` — delegates to ``PgChatRepository`` via SQLAlchemy sessions

The module-level :func:`create_backend` factory selects the right one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import uuid4

import structlog

from api_gateway.chat.models import (
    ChatChannelRecord,
    ChatMessageRecord,
    ChatThreadRecord,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.chat.backend")


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class ChatBackend(ABC):
    """Async interface for chat storage operations."""

    @abstractmethod
    async def list_channels(self) -> list[ChatChannelRecord]: ...

    @abstractmethod
    async def channel_for_scope(self, scope_kind: str) -> ChatChannelRecord | None: ...

    @abstractmethod
    async def list_threads(
        self,
        *,
        channel_id: str | None = None,
        scope_kind: str | None = None,
        entity_id: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ChatThreadRecord], int]: ...

    @abstractmethod
    async def get_thread(self, thread_id: str) -> ChatThreadRecord | None: ...

    @abstractmethod
    async def create_thread(
        self,
        *,
        channel_id: str,
        scope_kind: str,
        scope_entity_id: str,
        title: str,
    ) -> ChatThreadRecord: ...

    @abstractmethod
    async def get_messages(self, thread_id: str) -> list[ChatMessageRecord]: ...

    @abstractmethod
    async def add_message(
        self,
        *,
        thread_id: str,
        actor_id: str,
        actor_kind: str,
        content: str,
        status: str = "sent",
        graph_ref_node: str | None = None,
        graph_ref_type: str | None = None,
        graph_ref_label: str | None = None,
    ) -> ChatMessageRecord: ...

    @abstractmethod
    async def message_count(self, thread_id: str) -> int: ...

    @abstractmethod
    async def update_thread_timestamp(
        self, thread_id: str, timestamp: datetime
    ) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

_DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"name": "Session Chat", "scope_kind": "session"},
    {"name": "Approval Chat", "scope_kind": "approval"},
    {"name": "BOM Discussion", "scope_kind": "bom-entry"},
    {"name": "Digital Twin", "scope_kind": "digital-twin-node"},
    {"name": "Project Chat", "scope_kind": "project"},
]


class InMemoryChatBackend(ChatBackend):
    """Dict-backed chat storage (development / tests)."""

    def __init__(self) -> None:
        self.channels: dict[str, ChatChannelRecord] = {}
        self.threads: dict[str, ChatThreadRecord] = {}
        self.messages: dict[str, list[ChatMessageRecord]] = {}

    @classmethod
    def create(cls) -> InMemoryChatBackend:
        backend = cls()
        for ch in _DEFAULT_CHANNELS:
            channel = ChatChannelRecord(
                id=str(uuid4()),
                name=ch["name"],
                scope_kind=ch["scope_kind"],
            )
            backend.channels[channel.id] = channel
        return backend

    async def list_channels(self) -> list[ChatChannelRecord]:
        return list(self.channels.values())

    async def channel_for_scope(self, scope_kind: str) -> ChatChannelRecord | None:
        for ch in self.channels.values():
            if ch.scope_kind == scope_kind:
                return ch
        return None

    async def list_threads(
        self,
        *,
        channel_id: str | None = None,
        scope_kind: str | None = None,
        entity_id: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ChatThreadRecord], int]:
        threads = list(self.threads.values())
        if not include_archived:
            threads = [t for t in threads if not t.archived]
        if channel_id is not None:
            threads = [t for t in threads if t.channel_id == channel_id]
        if scope_kind is not None:
            threads = [t for t in threads if t.scope_kind == scope_kind]
        if entity_id is not None:
            threads = [t for t in threads if t.scope_entity_id == entity_id]
        threads.sort(key=lambda t: t.last_message_at, reverse=True)
        total = len(threads)
        start = (page - 1) * per_page
        return threads[start : start + per_page], total

    async def get_thread(self, thread_id: str) -> ChatThreadRecord | None:
        return self.threads.get(thread_id)

    async def create_thread(
        self,
        *,
        channel_id: str,
        scope_kind: str,
        scope_entity_id: str,
        title: str,
    ) -> ChatThreadRecord:
        now = datetime.now(UTC)
        thread_id = str(uuid4())
        thread = ChatThreadRecord(
            id=thread_id,
            channel_id=channel_id,
            scope_kind=scope_kind,
            scope_entity_id=scope_entity_id,
            title=title,
            created_at=now,
            last_message_at=now,
        )
        self.threads[thread_id] = thread
        self.messages[thread_id] = []
        return thread

    async def get_messages(self, thread_id: str) -> list[ChatMessageRecord]:
        return self.messages.get(thread_id, [])

    async def add_message(
        self,
        *,
        thread_id: str,
        actor_id: str,
        actor_kind: str,
        content: str,
        status: str = "sent",
        graph_ref_node: str | None = None,
        graph_ref_type: str | None = None,
        graph_ref_label: str | None = None,
    ) -> ChatMessageRecord:
        now = datetime.now(UTC)
        msg = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=thread_id,
            actor_id=actor_id,
            actor_kind=actor_kind,
            content=content,
            status=status,
            graph_ref_node=graph_ref_node,
            graph_ref_type=graph_ref_type,
            graph_ref_label=graph_ref_label,
            created_at=now,
            updated_at=now,
        )
        self.messages.setdefault(thread_id, []).append(msg)
        return msg

    async def message_count(self, thread_id: str) -> int:
        return len(self.messages.get(thread_id, []))

    async def update_thread_timestamp(
        self, thread_id: str, timestamp: datetime
    ) -> None:
        thread = self.threads.get(thread_id)
        if thread is not None:
            thread.last_message_at = timestamp


# ---------------------------------------------------------------------------
# PostgreSQL implementation
# ---------------------------------------------------------------------------


class PgChatBackend(ChatBackend):
    """PostgreSQL-backed chat storage via SQLAlchemy async sessions."""

    def __init__(self) -> None:
        from api_gateway.db.repository import PgChatRepository

        self._repo = PgChatRepository()

    async def _get_session(self):  # noqa: ANN202
        from api_gateway.db.engine import get_session

        return get_session()

    async def list_channels(self) -> list[ChatChannelRecord]:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            return await self._repo.list_channels(session)

    async def channel_for_scope(self, scope_kind: str) -> ChatChannelRecord | None:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            return await self._repo.channel_for_scope(session, scope_kind)

    async def list_threads(
        self,
        *,
        channel_id: str | None = None,
        scope_kind: str | None = None,
        entity_id: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ChatThreadRecord], int]:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            return await self._repo.list_threads(
                session,
                channel_id=channel_id,
                scope_kind=scope_kind,
                entity_id=entity_id,
                include_archived=include_archived,
                page=page,
                per_page=per_page,
            )

    async def get_thread(self, thread_id: str) -> ChatThreadRecord | None:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            return await self._repo.get_thread(session, thread_id)

    async def create_thread(
        self,
        *,
        channel_id: str,
        scope_kind: str,
        scope_entity_id: str,
        title: str,
    ) -> ChatThreadRecord:
        from api_gateway.db.engine import get_session

        thread_id = str(uuid4())
        async with get_session() as session:
            return await self._repo.create_thread(
                session,
                thread_id=thread_id,
                channel_id=channel_id,
                scope_kind=scope_kind,
                scope_entity_id=scope_entity_id,
                title=title,
            )

    async def get_messages(self, thread_id: str) -> list[ChatMessageRecord]:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            return await self._repo.list_messages(session, thread_id)

    async def add_message(
        self,
        *,
        thread_id: str,
        actor_id: str,
        actor_kind: str,
        content: str,
        status: str = "sent",
        graph_ref_node: str | None = None,
        graph_ref_type: str | None = None,
        graph_ref_label: str | None = None,
    ) -> ChatMessageRecord:
        from api_gateway.db.engine import get_session

        message_id = str(uuid4())
        async with get_session() as session:
            msg = await self._repo.add_message(
                session,
                message_id=message_id,
                thread_id=thread_id,
                actor_id=actor_id,
                actor_kind=actor_kind,
                content=content,
                status=status,
                graph_ref_node=graph_ref_node,
                graph_ref_type=graph_ref_type,
                graph_ref_label=graph_ref_label,
            )
            await self._repo.update_thread_timestamp(
                session, thread_id, msg.created_at
            )
            return msg

    async def message_count(self, thread_id: str) -> int:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            return await self._repo.message_count(session, thread_id)

    async def update_thread_timestamp(
        self, thread_id: str, timestamp: datetime
    ) -> None:
        from api_gateway.db.engine import get_session

        async with get_session() as session:
            await self._repo.update_thread_timestamp(session, thread_id, timestamp)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def create_backend() -> ChatBackend:
    """Create the appropriate chat backend based on environment.

    Returns ``PgChatBackend`` when ``DATABASE_URL`` is set and SQLAlchemy
    is installed; otherwise returns ``InMemoryChatBackend``.
    """
    try:
        from api_gateway.db import HAS_SQLALCHEMY
        from api_gateway.db.engine import get_engine

        if HAS_SQLALCHEMY and get_engine() is not None:
            backend = PgChatBackend()
            # Seed default channels on first startup
            from api_gateway.db.engine import get_session

            async with get_session() as session:
                await backend._repo.seed_default_channels(session)
            logger.info("chat_backend_pg_initialized")
            return backend
    except Exception as exc:
        logger.warning("chat_backend_pg_failed_fallback", error=str(exc))

    logger.info("chat_backend_in_memory_initialized")
    return InMemoryChatBackend.create()
