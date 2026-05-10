# Troubleshooting

> **Status:** Phase 1 (v0.1). Common errors and how to recover.
> Last verified against `main` on 2026-05-10. If you hit something
> not listed here, check
> [`docs/runbooks/`](https://github.com/FidelOdok/MetaForge/tree/main/docs/runbooks) for ops-level scenarios or open a Linear
> issue.

## Postgres or Neo4j unreachable

**Symptom:** Server logs show
`could not translate host name "postgres"` or
`Failed to establish connection (Neo4j)`. The gateway boots anyway.


**Diagnosis:** MetaForge degrades gracefully — without those
backends it falls back to in-memory implementations.

**What's lost in fallback mode:**

- **No persistence.** Data evaporates when the gateway restarts.
- **Limited Cypher.** `twin.query_cypher` runs against an in-memory
  shim; complex graph patterns may behave differently from real
  Neo4j.
- **No vector search across processes.** `knowledge.*` adapters
  drop silently if Postgres + pgvector aren't reachable; ingest
  returns an error.

**Fix:**

```bash
docker compose up -d postgres neo4j
docker compose ps                    # verify both report "healthy"
```

If you need fallback to fail loudly instead of silently, set
`METAFORGE_REQUIRE_NEO4J=true` and `METAFORGE_REQUIRE_POSTGRES=true`
in the gateway environment — the server then refuses to start
without them.

## `.mcp.json` drift breaks `test_mcp_json_config`

**Symptom:** `pytest tests/unit/test_mcp_json_config.py` fails with:

```
AssertionError: assert '.venv/bin/python' == 'python'
```

**Cause:** Local edits to `.mcp.json` (often automatic from an IDE)
swap the canonical `"command": "python"` for a venv-relative path.

**Fix:**

```bash
git restore .mcp.json
```

If you get `error: unable to unlink old '.mcp.json': Device or
resource busy` on WSL2, see the next section.

## WSL2 file locks (`Device or resource busy`)

**Symptom:** `git restore` or `git checkout` of one of these files
errors out with "Device or resource busy":

- `.git/config`
- `.mcp.json`
- `.claude/settings.local.json`

**Cause:** A Windows-side process (Claude Code, an IDE, an
antivirus) holds the file open. The Windows handle blocks Linux from
unlinking it during a git operation.

**Fix:** Use `git show HEAD:<file>` to read the canonical content,
then write it through the editor / Write tool you're already in. The
overwrite uses Linux-native fs semantics and bypasses the lock:

```bash
git show HEAD:.mcp.json > /tmp/canonical.json
# then copy the content into .mcp.json via your editor
```

Don't kill the holding process unless you know what it is — it's
usually your active shell or IDE.

## MCP error: `<arg> required and must be a string`

**Symptom:** Calling a tool from Claude Code fails with
`'tool_name' is required and must be a string` (or similar) for
every spec-compliant call.

**Cause:** This was MET-420 — the unified MCP server was dropping
the `arguments` payload on `tools/call` for the standard MCP
shape. The fix landed in PR #181.

**Fix:** Pull `main` and rebuild. If you're still seeing the error
after that, double-check your client is sending the standard shape
(`{name, arguments}`) and not the legacy shape (`{tool_id,
parameters}`) — the server accepts both, but mixed shapes confuse
older clients.

## Dashboard 404 on `/knowledge`

**Symptom:** The dashboard renders, but `/knowledge` shows a 404 or
the table is permanently empty.

**Cause:** The gateway's `knowledge` adapter isn't loaded. The
dashboard route exists either way; the data behind it doesn't.

**Fix:** Confirm the env var:

```bash
echo $METAFORGE_ADAPTERS
# expect: cadquery,calculix,knowledge   (or similar including knowledge)
```

If `knowledge` is missing, the standalone server and gateway both
skip the adapter. Set the env var before launching:

```bash
export METAFORGE_ADAPTERS=cadquery,calculix,knowledge
docker compose up gateway dashboard
```

You'll also need `pip install -e ".[knowledge]"` so the LightRAG /
asyncpg deps are present.

## "Adapter X dropped silently at startup"

**Symptom:** `cadquery.*` or `freecad.*` tools don't show up in
`tool/list` even though `METAFORGE_ADAPTERS` includes them. No
error is logged.

**Cause:** The optional Python deps aren't installed. The launcher
declares the manifest but skips the handler — by design, so a bare
clone still boots.

**Fix:** Install the matching extras:

```bash
pip install -e ".[knowledge,cadquery]"     # or any subset you need
```

Available extras: `dev`, `knowledge`, `cadquery`, `freecad`, `kicad`,
`neo4j`. Check `pyproject.toml` `[project.optional-dependencies]`
for the current list.

## CLI: `connection refused` against the gateway

**Symptom:** `python -m cli.forge_cli proposals` exits with
`Error: failed to connect to gateway: ...`.

**Cause:** No gateway is running, or it's on a different host/port
from what the CLI is dialing.

**Fix:**

```bash
# Confirm a gateway is listening:
curl http://localhost:8000/health

# Or override the URL:
python -m cli.forge_cli --gateway-url http://gateway.local:8000 proposals
# Or set the env var:
export METAFORGE_GATEWAY_URL=http://gateway.local:8000
```

If you don't have a gateway anywhere, boot one:

```bash
docker compose up gateway
# or, locally (no Docker):
python -m api_gateway.server
```

## Ingest: `httpx.ReadTimeout`

**Symptom:** `python -m cli.forge_cli ingest large-file.pdf` errors
out with a read timeout.

**Cause:** Embedding a large doc takes longer than the default 300 s.

**Fix:** Bump the per-request timeout:

```bash
python -m cli.forge_cli ingest large-file.pdf --timeout 1800
# or persistently:
export METAFORGE_INGEST_TIMEOUT=1800
```

## `pytest` flake on first run

**Symptom:** First-ever `pytest` run on a fresh clone reports a
random unrelated failure that doesn't reproduce on the second run.

**Cause:** Python `__pycache__` from a prior install of a different
version. Common after switching branches or pulling.

**Fix:**

```bash
find . -name __pycache__ -type d -prune -exec rm -rf {} +
pytest
```

## When to escalate

If the issue is:

- a missing capability — file a Linear issue under the appropriate
  epic (see [`roadmap.md`](roadmap.md)).
- a regression — open a Linear ticket and tag it `regression`; the
  bug-hunter agent (`/bug-hunt`) can help triage.
- an ops-level outage — see [`docs/runbooks/`](https://github.com/FidelOdok/MetaForge/tree/main/docs/runbooks) for
  stack-specific runbooks (`gateway-down.md`, `neo4j-unreachable.md`,
  `kafka-consumer-stopped.md`).
