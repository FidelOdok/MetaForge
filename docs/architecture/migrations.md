# Database migrations

One-shot scripts that bring an existing MetaForge database up to a
new schema or invariant. Each migration is named after the Linear
ticket that owns it and lives under `scripts/migrations/`.

## Running a migration

All migrations are idempotent — running them a second time should be
a no-op. They are **explicit**: the operator runs them, the gateway
does not auto-apply on boot.

```bash
# Generic shape
python -m scripts.migrations.<name> [--dry-run] [other options]
```

Use `--dry-run` (when available) to count the rows that *would* be
updated before committing.

## Migration catalogue

### `backfill_project_id` — MET-442

Sets `project_id` on every legacy graph node that pre-dates the
MET-428 partitioning column. Without this, scoped reads filter out
those nodes and they become invisible to users.

```bash
METAFORGE_DEFAULT_PROJECT_ID=11111111-1111-1111-1111-111111111111 \
    python -m scripts.migrations.backfill_project_id
```

Options:

- `--graph-engine neo4j` (default) — connect via the standard
  `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` env vars.
- `--graph-engine in-memory` — run against a fresh in-memory Twin.
  Useful for tests and dry runs.
- `--dry-run` — count nodes that would be updated; don't write.

Output:

```
Updated 42 node(s) with project_id=… (neo4j).
```

Re-running on a fully-migrated DB prints `Updated 0 node(s) …`.

### Adding a new migration

1. Create `scripts/migrations/<name>.py` with a `main(argv)` entry
   point and a `if __name__ == "__main__": raise SystemExit(main())`
   shim so `python -m` works.
2. Make the script idempotent. If you can't, document the gating
   explicitly (e.g. "only run when X column does not exist yet").
3. Add `--dry-run` support if possible.
4. Add a unit test under `tests/unit/test_migration_<name>.py` that
   exercises the script against an in-memory backend.
5. Document the script in this file under "Migration catalogue".
