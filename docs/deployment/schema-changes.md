---
title: Changing the database schema
---

# Changing the gateway's database schema

The gateway, the MCP sidecar and the workers all run from the gateway image,
and each brings the database up to its own release when it starts
(`api_gateway/db/schema.py`, `ensure_schema`). A host that updates on every
release, and rolls back by deploying the previous one, relies on three rules.

## One writer at a time

On Postgres, startup DDL runs in one transaction that first takes a fixed
advisory lock (`pg_advisory_xact_lock`). The processes of a release take
turns instead of racing on `CREATE TABLE IF NOT EXISTS`, which Postgres does
not make safe under concurrency. The lock is released at commit or rollback,
so a crashed process cannot leave it held.

## Expand, then contract

What runs on every start only **adds**:

| Change | Where |
|---|---|
| A new table | a new model in `api_gateway/db/models.py` (created by `create_all`) |
| A new column | the model, plus an `ALTER TABLE … ADD COLUMN IF NOT EXISTS` line in `ADDITIVE_COLUMNS`; nullable or with a default |

Because nothing is removed, the previous release still runs against the
upgraded schema, and rolling back needs no down-migration.
`tests/unit/test_schema_migrations.py` fails the build if a `DROP`, `RENAME`
or type change appears anywhere outside the numbered migrations.

## Numbered migrations for everything else

Dropping, renaming, retyping or back-filling goes in `MIGRATIONS` in
`api_gateway/db/migrations/__init__.py`:

```python
MIGRATIONS = (
    Migration(1, "drop chat_threads.legacy_title", "ALTER TABLE chat_threads DROP COLUMN legacy_title"),
)
```

Each one is applied once, in version order, in the same locked transaction,
and recorded in `schema_migrations`. A database whose ledger holds versions the
running code does not know, which is what a rollback looks like, is logged
(`schema_ahead_of_code`) and tolerated.

A contracting migration ships **at least one release after** the code stopped
using what it removes:

1. Release N: the code stops reading and writing `legacy_title`; the column stays.
2. Release N+1: the migration drops it. Rolling back to N still works, because N
   never touches the column.

Released migrations are never edited and versions only increase. Keep
semicolons to statement ends: statements are split on them.

## Scope

This covers the gateway's SQLAlchemy tables. Stores that manage their own
tables (the component catalog, pgvector memory, OAuth clients, the SQLite job
and flow stores) follow the same additive rule, which the test above also
enforces, but do not yet take the advisory lock.
