"""PostgreSQL-backed repository implementing the ChatStore interface.

This module provides :class:`PgChatRepository` which mirrors the
``ChatStore`` methods in ``api_gateway.chat.routes`` but persists data
to PostgreSQL via SQLAlchemy async sessions.

All public methods accept an ``AsyncSession`` so the caller controls
the transaction boundary (unit-of-work pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from api_gateway.chat.models import (
    ChatChannelRecord,
    ChatMessageRecord,
    ChatThreadRecord,
)
from observability.tracing import get_tracer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.db.repository")

# Default channels seeded on first use (same as in-memory store)
_DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"name": "Session Chat", "scope_kind": "session"},
    {"name": "Approval Chat", "scope_kind": "approval"},
    {"name": "BOM Discussion", "scope_kind": "bom-entry"},
    {"name": "Digital Twin", "scope_kind": "digital-twin-node"},
    {"name": "Project Chat", "scope_kind": "project"},
]


class PgChatRepository:
    """PostgreSQL-backed chat storage.

    Every method maps 1-to-1 to a ``ChatStore`` method so the rest of
    the gateway can swap backends without changing call sites.
    """

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def seed_default_channels(self, session: AsyncSession) -> None:
        """Insert default channels if the table is empty."""
        from api_gateway.db.models import ChatChannelRow

        with tracer.start_as_current_span("db.seed_channels") as span:
            from sqlalchemy import func, select

            count_stmt = select(func.count()).select_from(ChatChannelRow)
            result = await session.execute(count_stmt)
            count: int = result.scalar_one()
            span.set_attribute("existing_channels", count)

            if count > 0:
                logger.debug("channels_already_seeded", count=count)
                return

            for ch_def in _DEFAULT_CHANNELS:
                row = ChatChannelRow(
                    id=str(uuid4()),
                    name=ch_def["name"],
                    scope_kind=ch_def["scope_kind"],
                    created_at=datetime.now(UTC),
                )
                session.add(row)

            await session.flush()
            logger.info("default_channels_seeded", count=len(_DEFAULT_CHANNELS))

    async def list_channels(self, session: AsyncSession) -> list[ChatChannelRecord]:
        """Return all channels."""
        from api_gateway.db.models import ChatChannelRow

        with tracer.start_as_current_span("db.list_channels"):
            from sqlalchemy import select

            stmt = select(ChatChannelRow)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                ChatChannelRecord(
                    id=row.id,
                    name=row.name,
                    scope_kind=row.scope_kind,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def channel_for_scope(
        self, session: AsyncSession, scope_kind: str
    ) -> ChatChannelRecord | None:
        """Return the first channel matching *scope_kind*."""
        from api_gateway.db.models import ChatChannelRow

        with tracer.start_as_current_span("db.channel_for_scope") as span:
            from sqlalchemy import select

            span.set_attribute("scope_kind", scope_kind)
            stmt = select(ChatChannelRow).where(ChatChannelRow.scope_kind == scope_kind)
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row is None:
                return None
            return ChatChannelRecord(
                id=row.id,
                name=row.name,
                scope_kind=row.scope_kind,
                created_at=row.created_at,
            )

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    async def create_thread(
        self,
        session: AsyncSession,
        *,
        thread_id: str,
        channel_id: str,
        scope_kind: str,
        scope_entity_id: str,
        title: str,
        created_at: datetime | None = None,
    ) -> ChatThreadRecord:
        """Insert a new thread row and return its Pydantic record."""
        from api_gateway.db.models import ChatThreadRow

        with tracer.start_as_current_span("db.create_thread") as span:
            now = created_at or datetime.now(UTC)
            span.set_attribute("thread_id", thread_id)
            row = ChatThreadRow(
                id=thread_id,
                channel_id=channel_id,
                scope_kind=scope_kind,
                scope_entity_id=scope_entity_id,
                title=title,
                created_at=now,
                last_message_at=now,
            )
            session.add(row)
            await session.flush()
            logger.info("thread_created", thread_id=thread_id)
            return ChatThreadRecord(
                id=row.id,
                channel_id=row.channel_id,
                scope_kind=row.scope_kind,
                scope_entity_id=row.scope_entity_id,
                title=row.title,
                archived=row.archived,
                created_at=row.created_at,
                last_message_at=row.last_message_at,
            )

    async def get_thread(self, session: AsyncSession, thread_id: str) -> ChatThreadRecord | None:
        """Return a single thread by ID."""
        from api_gateway.db.models import ChatThreadRow

        with tracer.start_as_current_span("db.get_thread") as span:
            span.set_attribute("thread_id", thread_id)
            row = await session.get(ChatThreadRow, thread_id)
            if row is None:
                return None
            return ChatThreadRecord(
                id=row.id,
                channel_id=row.channel_id,
                scope_kind=row.scope_kind,
                scope_entity_id=row.scope_entity_id,
                title=row.title,
                archived=row.archived,
                created_at=row.created_at,
                last_message_at=row.last_message_at,
            )

    async def list_threads(
        self,
        session: AsyncSession,
        *,
        channel_id: str | None = None,
        scope_kind: str | None = None,
        entity_id: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ChatThreadRecord], int]:
        """Return filtered, paginated threads and total count."""
        from api_gateway.db.models import ChatThreadRow

        with tracer.start_as_current_span("db.list_threads") as span:
            from sqlalchemy import func, select

            stmt = select(ChatThreadRow)
            count_stmt = select(func.count()).select_from(ChatThreadRow)

            if not include_archived:
                stmt = stmt.where(ChatThreadRow.archived.is_(False))
                count_stmt = count_stmt.where(ChatThreadRow.archived.is_(False))
            if channel_id is not None:
                stmt = stmt.where(ChatThreadRow.channel_id == channel_id)
                count_stmt = count_stmt.where(ChatThreadRow.channel_id == channel_id)
            if scope_kind is not None:
                stmt = stmt.where(ChatThreadRow.scope_kind == scope_kind)
                count_stmt = count_stmt.where(ChatThreadRow.scope_kind == scope_kind)
            if entity_id is not None:
                stmt = stmt.where(ChatThreadRow.scope_entity_id == entity_id)
                count_stmt = count_stmt.where(ChatThreadRow.scope_entity_id == entity_id)

            stmt = stmt.order_by(ChatThreadRow.last_message_at.desc())

            offset = (page - 1) * per_page
            stmt = stmt.offset(offset).limit(per_page)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            count_result = await session.execute(count_stmt)
            total: int = count_result.scalar_one()

            span.set_attribute("total", total)
            span.set_attribute("page", page)

            records = [
                ChatThreadRecord(
                    id=r.id,
                    channel_id=r.channel_id,
                    scope_kind=r.scope_kind,
                    scope_entity_id=r.scope_entity_id,
                    title=r.title,
                    archived=r.archived,
                    created_at=r.created_at,
                    last_message_at=r.last_message_at,
                )
                for r in rows
            ]
            return records, total

    async def update_thread_timestamp(
        self,
        session: AsyncSession,
        thread_id: str,
        timestamp: datetime,
    ) -> None:
        """Update ``last_message_at`` on a thread."""
        from api_gateway.db.models import ChatThreadRow

        with tracer.start_as_current_span("db.update_thread_ts") as span:
            span.set_attribute("thread_id", thread_id)
            row = await session.get(ChatThreadRow, thread_id)
            if row is not None:
                row.last_message_at = timestamp
                await session.flush()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def add_message(
        self,
        session: AsyncSession,
        *,
        message_id: str,
        thread_id: str,
        actor_id: str,
        actor_kind: str,
        content: str,
        status: str = "sent",
        graph_ref_node: str | None = None,
        graph_ref_type: str | None = None,
        graph_ref_label: str | None = None,
        created_at: datetime | None = None,
    ) -> ChatMessageRecord:
        """Insert a message row and return its Pydantic record."""
        from api_gateway.db.models import ChatMessageRow

        with tracer.start_as_current_span("db.add_message") as span:
            now = created_at or datetime.now(UTC)
            span.set_attribute("thread_id", thread_id)
            span.set_attribute("message_id", message_id)
            row = ChatMessageRow(
                id=message_id,
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
            session.add(row)
            await session.flush()
            logger.debug("message_added", thread_id=thread_id, message_id=message_id)
            return ChatMessageRecord(
                id=row.id,
                thread_id=row.thread_id,
                actor_id=row.actor_id,
                actor_kind=row.actor_kind,
                content=row.content,
                status=row.status,
                graph_ref_node=row.graph_ref_node,
                graph_ref_type=row.graph_ref_type,
                graph_ref_label=row.graph_ref_label,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def list_messages(self, session: AsyncSession, thread_id: str) -> list[ChatMessageRecord]:
        """Return all messages for a thread, ordered by creation time."""
        from api_gateway.db.models import ChatMessageRow

        with tracer.start_as_current_span("db.list_messages") as span:
            from sqlalchemy import select

            span.set_attribute("thread_id", thread_id)
            stmt = (
                select(ChatMessageRow)
                .where(ChatMessageRow.thread_id == thread_id)
                .order_by(ChatMessageRow.created_at.asc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                ChatMessageRecord(
                    id=r.id,
                    thread_id=r.thread_id,
                    actor_id=r.actor_id,
                    actor_kind=r.actor_kind,
                    content=r.content,
                    status=r.status,
                    graph_ref_node=r.graph_ref_node,
                    graph_ref_type=r.graph_ref_type,
                    graph_ref_label=r.graph_ref_label,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
                for r in rows
            ]

    async def message_count(self, session: AsyncSession, thread_id: str) -> int:
        """Return the number of messages in a thread."""
        from api_gateway.db.models import ChatMessageRow

        with tracer.start_as_current_span("db.message_count") as span:
            from sqlalchemy import func, select

            span.set_attribute("thread_id", thread_id)
            stmt = (
                select(func.count())
                .select_from(ChatMessageRow)
                .where(ChatMessageRow.thread_id == thread_id)
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())
