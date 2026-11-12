"""Gateway schema changes are safe to ship continuously (FORGE-856).

A hosted deployment upgrades on every open-core release and rolls back by
deploying the previous one. That holds only if:

* startup DDL never destroys anything (the previous release must still run
  against the upgraded schema) -- checked by scanning the source;
* numbered migrations apply once, in order, atomically, and a database that
  is *ahead* of the code (after a rollback) is tolerated -- checked against
  SQLite through the real runner;
* every gateway process takes the same advisory lock before DDL on Postgres.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from api_gateway.db import schema  # noqa: E402
from api_gateway.db.migrations import MIGRATIONS, Migration  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Python sources that may contain destructive DDL, and why.
DESTRUCTIVE_ALLOWED = {
    # Numbered migrations and the legacy v001 downgrade: the reviewed path.
    "api_gateway/db/migrations/",
    # Drops a scratch table it just created to probe for SQLite FTS5.
    "orchestrator/harness/ledger.py",
}
DESTRUCTIVE = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN|INDEX|SCHEMA)|RENAME\s+(TO|COLUMN)|ALTER\s+COLUMN\s+\w+\s+(SET\s+DATA\s+)?TYPE)\b",
    re.IGNORECASE,
)
SKIP_DIRS = {"tests", ".venv", "venv", "node_modules", ".git", "docs-site", ".claude"}


def _sources() -> list[Path]:
    out = []
    for p in REPO_ROOT.rglob("*.py"):
        rel = p.relative_to(REPO_ROOT)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        out.append(p)
    return out


def test_no_destructive_ddl_outside_numbered_migrations() -> None:
    offenders = []
    for path in _sources():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(a) for a in DESTRUCTIVE_ALLOWED):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if DESTRUCTIVE.search(line) and ('"' in line or "'" in line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        "Destructive DDL runs on every start and breaks rollback to the previous "
        "release. Put it in api_gateway/db/migrations (MIGRATIONS) one release "
        "after the code stops using what it removes:\n" + "\n".join(offenders)
    )


def test_released_migration_versions_are_unique_and_increasing() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(set(versions))
    assert all(v > 0 for v in versions)


def test_split_statements_drops_empty_fragments() -> None:
    assert schema.split_statements("A;\n\n B ;; C") == ["A", "B", "C"]


@pytest.fixture
async def engine(tmp_path: Path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    yield eng
    await eng.dispose()


M1 = Migration(1, "add widgets", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
M2 = Migration(
    2,
    "backfill widgets",
    "INSERT INTO widgets (id) VALUES (1); INSERT INTO widgets (id) VALUES (2)",
)


async def _versions(eng) -> list[int]:
    async with eng.connect() as conn:
        rows = await conn.execute(text("SELECT version FROM schema_migrations ORDER BY version"))
        return [int(r[0]) for r in rows]


async def test_migrations_apply_once_in_order(engine) -> None:
    async with engine.begin() as conn:
        assert await schema.apply_migrations(conn, [M2, M1]) == [1, 2]
    async with engine.begin() as conn:
        assert await schema.apply_migrations(conn, [M1, M2]) == []
    assert await _versions(engine) == [1, 2]
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT COUNT(*) FROM widgets"))).scalar() == 2


async def test_a_failed_migration_changes_nothing(engine) -> None:
    """Half a migration never lands, and it is not recorded as applied.

    Checked with DML: SQLite's driver commits DDL implicitly, whereas Postgres
    -- the hosted database -- rolls DDL back with the transaction too.
    """
    async with engine.begin() as conn:
        await schema.apply_migrations(conn, [M1])
    bad = Migration(2, "broken", "INSERT INTO widgets (id) VALUES (1); INSERT INTO nope VALUES (1)")
    with pytest.raises(Exception, match="nope"):
        async with engine.begin() as conn:
            await schema.apply_migrations(conn, [M1, bad])
    assert await _versions(engine) == [1]
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT COUNT(*) FROM widgets"))).scalar() == 0


async def test_a_database_ahead_of_the_code_is_tolerated(engine) -> None:
    """After a rollback the ledger holds versions this release does not know."""
    async with engine.begin() as conn:
        await schema.apply_migrations(conn, [M1, M2])
    async with engine.begin() as conn:
        assert await schema.apply_migrations(conn, [M1]) == []


async def test_duplicate_versions_are_refused(engine) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        async with engine.begin() as conn:
            await schema.apply_migrations(conn, [M1, Migration(1, "again", "SELECT 1")])


async def test_ensure_schema_creates_tables_and_ledger(engine) -> None:
    await schema.ensure_schema(engine)
    async with engine.connect() as conn:
        tables = {
            r[0]
            for r in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert {"chat_messages", "schema_migrations"} <= tables


def test_gateway_startup_goes_through_the_locked_path() -> None:
    server = (REPO_ROOT / "api_gateway" / "server.py").read_text(encoding="utf-8")
    assert "ensure_schema(engine)" in server
    assert "metadata.create_all" not in server
    src = (REPO_ROOT / "api_gateway" / "db" / "schema.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in src
