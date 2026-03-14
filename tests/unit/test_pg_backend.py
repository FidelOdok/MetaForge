"""Unit tests for the PostgreSQL backend layer (MET-216).

All tests use an in-memory SQLite database via SQLAlchemy's async engine
so **no real PostgreSQL instance is required**.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_gateway.db import HAS_SQLALCHEMY
from api_gateway.db.models import Base, ChatChannelRow, ChatMessageRow, ChatThreadRow
from api_gateway.db.repository import PgChatRepository

# ---------------------------------------------------------------------------
# Skip entire module when sqlalchemy is not installed
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.skipif(not HAS_SQLALCHEMY, reason="sqlalchemy not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    """Create an in-memory async SQLite engine with tables."""
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)

    # Enable foreign key support for SQLite
    @event.listens_for(eng.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):  # type: ignore[no-untyped-def]
    """Provide an async session bound to the in-memory engine."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


@pytest.fixture
def repo() -> PgChatRepository:
    return PgChatRepository()


# ---------------------------------------------------------------------------
# 1. Seed default channels
# ---------------------------------------------------------------------------


async def test_seed_default_channels(repo: PgChatRepository, session: AsyncSession) -> None:
    """Seeding should insert 5 default channels."""
    await repo.seed_default_channels(session)
    await session.commit()

    channels = await repo.list_channels(session)
    assert len(channels) == 5
    scope_kinds = {ch.scope_kind for ch in channels}
    assert "session" in scope_kinds
    assert "approval" in scope_kinds


# ---------------------------------------------------------------------------
# 2. Idempotent seed (no duplicates)
# ---------------------------------------------------------------------------


async def test_seed_idempotent(repo: PgChatRepository, session: AsyncSession) -> None:
    """Calling seed twice should not duplicate channels."""
    await repo.seed_default_channels(session)
    await session.commit()
    await repo.seed_default_channels(session)
    await session.commit()

    channels = await repo.list_channels(session)
    assert len(channels) == 5


# ---------------------------------------------------------------------------
# 3. Create and retrieve a thread
# ---------------------------------------------------------------------------


async def test_create_and_get_thread(repo: PgChatRepository, session: AsyncSession) -> None:
    """Create a thread and retrieve it by ID."""
    await repo.seed_default_channels(session)
    await session.commit()

    channels = await repo.list_channels(session)
    channel = channels[0]
    thread_id = str(uuid4())

    thread = await repo.create_thread(
        session,
        thread_id=thread_id,
        channel_id=channel.id,
        scope_kind=channel.scope_kind,
        scope_entity_id="entity-1",
        title="Test thread",
    )
    await session.commit()

    assert thread.id == thread_id
    assert thread.title == "Test thread"
    assert thread.archived is False

    fetched = await repo.get_thread(session, thread_id)
    assert fetched is not None
    assert fetched.id == thread_id


# ---------------------------------------------------------------------------
# 4. List threads with filtering
# ---------------------------------------------------------------------------


async def test_list_threads_filtered(repo: PgChatRepository, session: AsyncSession) -> None:
    """list_threads should respect scope_kind filter and pagination."""
    await repo.seed_default_channels(session)
    await session.commit()

    channels = await repo.list_channels(session)
    ch_session = next(c for c in channels if c.scope_kind == "session")
    ch_approval = next(c for c in channels if c.scope_kind == "approval")

    for i in range(3):
        await repo.create_thread(
            session,
            thread_id=str(uuid4()),
            channel_id=ch_session.id,
            scope_kind="session",
            scope_entity_id=f"s-{i}",
            title=f"Session thread {i}",
        )
    await repo.create_thread(
        session,
        thread_id=str(uuid4()),
        channel_id=ch_approval.id,
        scope_kind="approval",
        scope_entity_id="a-0",
        title="Approval thread",
    )
    await session.commit()

    session_threads, total = await repo.list_threads(session, scope_kind="session")
    assert total == 3
    assert len(session_threads) == 3

    # Test pagination
    page1, _ = await repo.list_threads(session, scope_kind="session", per_page=2, page=1)
    assert len(page1) == 2

    page2, _ = await repo.list_threads(session, scope_kind="session", per_page=2, page=2)
    assert len(page2) == 1


# ---------------------------------------------------------------------------
# 5. Add and list messages
# ---------------------------------------------------------------------------


async def test_add_and_list_messages(repo: PgChatRepository, session: AsyncSession) -> None:
    """Adding messages to a thread and listing them back."""
    await repo.seed_default_channels(session)
    await session.commit()

    channels = await repo.list_channels(session)
    channel = channels[0]
    thread_id = str(uuid4())

    await repo.create_thread(
        session,
        thread_id=thread_id,
        channel_id=channel.id,
        scope_kind=channel.scope_kind,
        scope_entity_id="e-1",
        title="Msg thread",
    )
    await session.commit()

    await repo.add_message(
        session,
        message_id=str(uuid4()),
        thread_id=thread_id,
        actor_id="user-1",
        actor_kind="user",
        content="Hello",
    )
    await repo.add_message(
        session,
        message_id=str(uuid4()),
        thread_id=thread_id,
        actor_id="agent-1",
        actor_kind="agent",
        content="Hi there",
        graph_ref_node="node-42",
    )
    await session.commit()

    messages = await repo.list_messages(session, thread_id)
    assert len(messages) == 2
    assert messages[0].content == "Hello"
    assert messages[1].graph_ref_node == "node-42"

    count = await repo.message_count(session, thread_id)
    assert count == 2


# ---------------------------------------------------------------------------
# 6. channel_for_scope lookup
# ---------------------------------------------------------------------------


async def test_channel_for_scope(repo: PgChatRepository, session: AsyncSession) -> None:
    """channel_for_scope should return the matching channel."""
    await repo.seed_default_channels(session)
    await session.commit()

    ch = await repo.channel_for_scope(session, "bom-entry")
    assert ch is not None
    assert ch.scope_kind == "bom-entry"

    missing = await repo.channel_for_scope(session, "nonexistent")
    assert missing is None


# ---------------------------------------------------------------------------
# 7. Connection fallback (no DATABASE_URL)
# ---------------------------------------------------------------------------


def test_engine_fallback_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_engine should return None when DATABASE_URL is not set."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Reset module-level singleton so it re-evaluates
    import api_gateway.db.engine as engine_mod

    engine_mod._engine = None

    result = engine_mod.get_engine()
    assert result is None
    assert engine_mod.is_pg_available() is False


# ---------------------------------------------------------------------------
# 8. ORM model table names
# ---------------------------------------------------------------------------


def test_orm_table_names() -> None:
    """Verify SQLAlchemy models have correct table names."""
    assert ChatChannelRow.__tablename__ == "chat_channels"
    assert ChatThreadRow.__tablename__ == "chat_threads"
    assert ChatMessageRow.__tablename__ == "chat_messages"


# ---------------------------------------------------------------------------
# 9. Migration DDL strings are well-formed
# ---------------------------------------------------------------------------


def test_migration_ddl_contains_tables() -> None:
    """The v001 migration DDL should reference all three tables."""
    from api_gateway.db.migrations.v001_create_chat_tables import (
        DOWNGRADE_SQL,
        UPGRADE_SQL,
    )

    assert "chat_channels" in UPGRADE_SQL
    assert "chat_threads" in UPGRADE_SQL
    assert "chat_messages" in UPGRADE_SQL
    assert "CREATE TABLE" in UPGRADE_SQL
    assert "DROP TABLE" in DOWNGRADE_SQL


# ---------------------------------------------------------------------------
# 10. Update thread timestamp
# ---------------------------------------------------------------------------


async def test_update_thread_timestamp(repo: PgChatRepository, session: AsyncSession) -> None:
    """update_thread_timestamp should change last_message_at."""
    await repo.seed_default_channels(session)
    await session.commit()

    channels = await repo.list_channels(session)
    thread_id = str(uuid4())
    await repo.create_thread(
        session,
        thread_id=thread_id,
        channel_id=channels[0].id,
        scope_kind=channels[0].scope_kind,
        scope_entity_id="ts-entity",
        title="Timestamp thread",
    )
    await session.commit()

    new_ts = datetime(2099, 1, 1, tzinfo=UTC)
    await repo.update_thread_timestamp(session, thread_id, new_ts)
    await session.commit()

    thread = await repo.get_thread(session, thread_id)
    assert thread is not None
    assert thread.last_message_at.year == 2099
