# CLI Reference

> **Status:** Phase 1 (v0.1). Every `python -m cli.forge_cli`
> subcommand, with one example per command. Source of truth:
> `cli/forge_cli/main.py` and `cli/forge_cli/sources.py`. Last
> verified against `main` on 2026-05-10.

The CLI is a thin Python wrapper over the gateway HTTP API. It
needs a running gateway on the other end (`docker compose up gateway`,
or `python -m api_gateway.server`).

## Invocation

There is no `forge` console-script entry yet — invoke the module
directly:

```bash
python -m cli.forge_cli <command> [args...]
```

(The only `[project.scripts]` entry today is `forge-server`, which
launches the gateway, **not** the CLI. A `forge` binary is tracked
as a follow-up.)

## Global flags

These work on every subcommand and must be passed **before** the
subcommand name:

| Flag | Default | Purpose |
|---|---|---|
| `--format {table,json,compact}` | `table` | Output rendering |
| `--gateway-url <url>` | `$METAFORGE_GATEWAY_URL` or `http://localhost:8000` | Override gateway base URL |

```bash
python -m cli.forge_cli --format json --gateway-url http://gateway.local:8000 proposals
```

## Commands

### `run` — invoke a skill

```
run <skill_name> --work_product <uuid> [--params JSON] [--session-id <uuid>]
```

Triggers a skill against a target work product via the gateway's
`/v1/skills/run` endpoint. Returns the resulting session id and the
skill's output payload.

| Arg / flag | Required | Notes |
|---|---|---|
| `skill_name` | yes | Registry id (e.g. `validate_stress`) |
| `--work_product <uuid>` | yes | Target node id |
| `--params <json>` | no | JSON object; default `{}` |
| `--session-id <uuid>` | no | Existing session to attach to |

```bash
python -m cli.forge_cli run validate_stress \
  --work_product 4f1c-... \
  --params '{"load_n": 500, "axis": "x"}'
```

### `status` — session status

```
status <session_id>
```

```bash
python -m cli.forge_cli status 8e2a-...
```

Returns the current state, the agent that owns the run, and the last
few tool calls. Useful when chasing a long-running workflow.

### `twin query` — fetch a node

```
twin query <node_id>
```

```bash
python -m cli.forge_cli twin query 7c91-...
```

Properties + first-hop neighbours. Same data the
`twin.get_node` MCP tool returns; this is the CLI surface.

### `twin list` — filter work products

```
twin list [--domain <domain>] [--type <work_product_type>]
```

```bash
python -m cli.forge_cli twin list --domain electronics --type schematic
```

| Flag | Notes |
|---|---|
| `--domain` | One of: `mechanical`, `electronics`, `firmware`, `simulation`, … |
| `--type` | Work-product type: `cad_model`, `schematic`, `bom`, etc. |

### `proposals` — list pending proposals

```
proposals
```

Lists every change proposal that's still in `pending`. The output
includes `change_id` (use it with `approve` / `reject`), the
proposing agent, the target work product, and the diff summary.

### `approve` / `reject` — act on a proposal

```
approve <change_id> --reason "..." [--reviewer <id>]
reject  <change_id> --reason "..." [--reviewer <id>]
```

Both commands require a `--reason` (audit-trail). `--reviewer`
defaults to `cli-user`; pass your identity if you have a real
reviewer record.

```bash
python -m cli.forge_cli approve 1a2b-... --reason "fits power budget"
python -m cli.forge_cli reject  1a2b-... --reason "BOM cost over budget"
```

### `ingest` — index docs into the knowledge layer

```
ingest <path> [--type <knowledge_type>] [--no-recursive] [--dry-run]
              [--work-product <uuid>] [--metadata <json>] [--timeout <seconds>]
```

Ingests a file or a directory tree into the L1 knowledge layer. Same
backend the `knowledge.ingest` MCP tool uses — this CLI is the second
surface for the same store.

| Flag | Default | Notes |
|---|---|---|
| `path` | _(required)_ | File or directory |
| `--type` | inferred from path | `design_decision` / `component` / `failure` / `constraint` / `session` |
| `--no-recursive` | off | When `path` is a directory, only its immediate children |
| `--dry-run` | off | Print what would be ingested; make no HTTP calls |
| `--work-product <uuid>` | none | Tag every chunk with a `source_work_product_id` |
| `--metadata <json>` | none | Extra metadata round-tripped on search hits |
| `--timeout <seconds>` | 300 | Per-request HTTP timeout (env: `METAFORGE_INGEST_TIMEOUT`) |

```bash
# One file with explicit type
python -m cli.forge_cli ingest tests/fixtures/datasheets/rp2040.txt \
  --type component \
  --metadata '{"vendor": "Raspberry Pi", "mpn": "RP2040"}'

# Whole directory, dry run first
python -m cli.forge_cli ingest docs/decisions/ --type design_decision --dry-run
python -m cli.forge_cli ingest docs/decisions/ --type design_decision
```

### `sources list` — list ingested sources

```
sources list [--type <knowledge_type>] [--project <uuid>] [--limit <n>]
```

```bash
python -m cli.forge_cli sources list
python -m cli.forge_cli sources list --type component --limit 25
```

Default columns: `knowledge_type`, `source_path`, `fragment_count`,
`indexed_at`. Pass `--format json` to get the raw envelope.

### `sources show` — single-source detail

```
sources show <source_id>
```

```bash
python -m cli.forge_cli sources show 'datasheet://rp2040'
```

Renders metadata + chunks. `source_id` is the `source_path` you used
at ingest time. Exits `2` with `Error: source not found …` if the
path is unknown.

### `sources delete` — purge a source

```
sources delete <source_id> [--yes]
```

```bash
python -m cli.forge_cli sources delete 'datasheet://rp2040' --yes
```

Removes every chunk for a source. Without `--yes` the CLI asks for
interactive confirmation. Returns the count of deleted chunks.

## Output formats

`--format table` (default) prints a fixed-column ASCII table.
`--format json` dumps the gateway response verbatim — best for
piping into `jq`. `--format compact` is the smallest one-line-per-row
form, useful in scripts.

```bash
python -m cli.forge_cli --format json sources list | jq '.sources[].sourcePath'
```

## Environment variables

| Var | Used by | Purpose |
|---|---|---|
| `METAFORGE_GATEWAY_URL` | every command | Base URL for the gateway |
| `METAFORGE_INGEST_TIMEOUT` | `ingest` | Override the default 300 s timeout |

## Troubleshooting

If a command hangs or returns a connection error, check
[`troubleshooting.md`](troubleshooting.md) for the gateway-down /
WSL2 / `.mcp.json` recovery paths.
