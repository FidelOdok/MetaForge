"""Gateway schema at startup: one writer at a time, additive by default,
versioned when it cannot be (FORGE-856).

Every process built from the gateway image (the gateway, the MCP sidecar, the
workers) calls :func:`ensure_schema` on boot. Three rules make that safe for a
deployment that upgrades continuously:

1. **One writer at a time.** Concurrent ``CREATE TABLE IF NOT EXISTS`` on
   Postgres is not safe: two sessions can both pass the existence check and
   one then fails on ``pg_type``'s unique index. Startup DDL runs inside a
   transaction holding a fixed advisory lock, so the processes of one release
   take turns.
2. **Expand, then contract.** Startup DDL only adds: new tables
   (``create_all``) and new nullable or defaulted columns
   (``ADDITIVE_COLUMNS``). The previous release keeps working against the
   upgraded schema, so rolling back one version needs no down-migration.
   ``tests/unit/test_schema_migrations.py`` fails the build for destructive
   DDL outside :mod:`api_gateway.db.migrations`.
3. **Anything else is a numbered migration.** Dropping, renaming, retyping or
   back-filling goes in :data:`api_gateway.db.migrations.MIGRATIONS`, is
   applied once, in order, and recorded in ``schema_migrations``. A
   contracting migration ships at least one release after the code stopped
   using what it removes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from observability.tracing import get_tracer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from api_gateway.db.migrations import Migration

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.db.schema")

#: Advisory-lock key for gateway startup DDL ("MFSC"). Any constant works as
#: long as every process uses the same one.
SCHEMA_LOCK_KEY = 0x4D465343

LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " version INTEGER PRIMARY KEY,"
    " name VARCHAR(255) NOT NULL,"
    " applied_at VARCHAR(64) NOT NULL)"
)


def split_statements(sql: str) -> list[str]:
    """Split a migration script into single statements.

    The asyncpg driver prepares each statement, so a multi-statement string
    must be sent one statement at a time. Migration scripts keep semicolons
    to statement ends; one inside a string literal is not supported.
    """
    return [s.strip() for s in sql.split(";") if s.strip()]


async def apply_migrations(conn: AsyncConnection, migrations: Sequence[Migration]) -> list[int]:
    """Apply every migration not yet in ``schema_migrations``, in version order.

    Returns the versions applied. Runs on the caller's connection and
    transaction, so a failure leaves the ledger and the schema unchanged.
    """
    from sqlalchemy import text

    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration versions: {sorted(versions)}")

    await conn.execute(text(LEDGER_DDL))
    rows = await conn.execute(text("SELECT version FROM schema_migrations"))
    applied = {int(r[0]) for r in rows}

    known_max = max(versions, default=0)
    ahead = sorted(v for v in applied if v > known_max)
    if ahead:
        # A rollback to an older release. Expand/contract makes that safe;
        # say so rather than failing.
        logger.warning("schema_ahead_of_code", applied_versions=ahead, known_max=known_max)

    done: list[int] = []
    for migration in sorted(migrations, key=lambda m: m.version):
        if migration.version in applied:
            continue
        with tracer.start_as_current_span("schema.migration") as span:
            span.set_attribute("migration.version", migration.version)
            span.set_attribute("migration.name", migration.name)
            for statement in split_statements(migration.sql):
                await conn.execute(text(statement))
            await conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (:v, :n, :t)"
                ),
                {"v": migration.version, "n": migration.name, "t": datetime.now(UTC).isoformat()},
            )
        logger.info("schema_migration_applied", version=migration.version, name=migration.name)
        done.append(migration.version)
    return done


async def ensure_schema(engine: AsyncEngine) -> list[int]:
    """Bring the gateway's tables up to this release, under the schema lock.

    Additive DDL first (new tables and columns the code expects), then any
    numbered migrations. All in one transaction: Postgres DDL is
    transactional, so a failure changes nothing.
    """
    from sqlalchemy import text

    from api_gateway.db.migrations import MIGRATIONS
    from api_gateway.db.models import ADDITIVE_COLUMNS, Base

    with tracer.start_as_current_span("schema.ensure") as span:
        async with engine.begin() as conn:
            is_pg = conn.dialect.name == "postgresql"
            span.set_attribute("db.system", conn.dialect.name)
            if is_pg:
                # Released at commit or rollback; a crashed process cannot
                # leave it held.
                await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SCHEMA_LOCK_KEY})
            await conn.run_sync(Base.metadata.create_all)
            if is_pg:
                for statement in ADDITIVE_COLUMNS:
                    await conn.execute(text(statement))
            applied = await apply_migrations(conn, MIGRATIONS)
        span.set_attribute("migrations.applied", len(applied))
    logger.info("schema_ready", migrations_applied=applied)
    return applied
