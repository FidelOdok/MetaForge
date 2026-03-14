"""Migration v001 -- create chat_channels, chat_threads, chat_messages tables.

This module exposes raw DDL strings and an async :func:`upgrade` helper
that executes them against a given engine.  The DDL is idempotent
(``IF NOT EXISTS``).

Usage::

    from api_gateway.db.migrations.v001_create_chat_tables import upgrade
    await upgrade(engine)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from observability.tracing import get_tracer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.db.migrations")

# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------

UPGRADE_SQL = """\
CREATE TABLE IF NOT EXISTS chat_channels (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    scope_kind  VARCHAR(64)  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_channels_scope_kind
    ON chat_channels (scope_kind);

CREATE TABLE IF NOT EXISTS chat_threads (
    id               VARCHAR(36)  PRIMARY KEY,
    channel_id       VARCHAR(36)  NOT NULL,
    scope_kind       VARCHAR(64)  NOT NULL,
    scope_entity_id  VARCHAR(255) NOT NULL,
    title            VARCHAR(500) NOT NULL,
    archived         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_message_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_channel_id
    ON chat_threads (channel_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_scope_kind
    ON chat_threads (scope_kind);
CREATE INDEX IF NOT EXISTS idx_chat_threads_scope_entity_id
    ON chat_threads (scope_entity_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              VARCHAR(36)  PRIMARY KEY,
    thread_id       VARCHAR(36)  NOT NULL,
    actor_id        VARCHAR(255) NOT NULL,
    actor_kind      VARCHAR(32)  NOT NULL,
    content         TEXT         NOT NULL,
    status          VARCHAR(32)  NOT NULL DEFAULT 'sent',
    graph_ref_node  VARCHAR(255),
    graph_ref_type  VARCHAR(255),
    graph_ref_label VARCHAR(255),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_id
    ON chat_messages (thread_id);
"""

DOWNGRADE_SQL = """\
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_threads;
DROP TABLE IF EXISTS chat_channels;
"""


# ---------------------------------------------------------------------------
# Programmatic helpers
# ---------------------------------------------------------------------------


async def upgrade(engine: AsyncEngine) -> None:
    """Run the upgrade DDL against *engine*."""
    with tracer.start_as_current_span("migration.v001.upgrade"):
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text(UPGRADE_SQL))
        logger.info("migration_v001_applied")


async def downgrade(engine: AsyncEngine) -> None:
    """Run the downgrade DDL against *engine*."""
    with tracer.start_as_current_span("migration.v001.downgrade"):
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text(DOWNGRADE_SQL))
        logger.info("migration_v001_reverted")
