# CLI Reference

> **Status:** Phase 1 (v0.1). Every `python -m cli.forge_cli`
> subcommand, with one example per command. Source of truth:
> `cli/forge_cli/main.py` and `cli/forge_cli/sources.py`. Last
> verified against `main` on 2026-05-10.

The CLI is a thin Python wrapper over the gateway HTTP API. It
needs a running gateway on the other end (`docker compose up gateway`,
or `python -m api_gateway.server`).

## Invocation

After `pip install -e .`, use the `forge` console script:

```bash
forge <command> [args...]
```

Equivalently, run the module directly (no install step needed):

```bash
python -m cli.forge_cli <command> [args...]
```

Both entry points are identical. `forge` is a Python console-script (it
requires the project's Python environment, like `forge-server`). Examples in
this reference use `python -m cli.forge_cli`, but `forge` works everywhere in
its place.

### Standalone binary (no Python required)

**Install a prebuilt binary** (from GitHub Releases):

```bash
# latest release, auto-detects your OS/arch
curl -fsSL https://raw.githubusercontent.com/FidelOdok/MetaForge/main/scripts/install.sh | sh

# or a specific version
curl -fsSL https://raw.githubusercontent.com/FidelOdok/MetaForge/main/scripts/install.sh | sh -s v0.1.0
```

This drops `forge` into `~/.local/bin` (override with `FORGE_BIN_DIR`). Windows
users download `forge-windows-x64.exe` from the
[Releases page](https://github.com/FidelOdok/MetaForge/releases) directly.

Binaries are published per platform on every `v*` tag by the `release` workflow
(`.github/workflows/release.yml`): `forge-linux-x64`, `forge-macos-arm64`, and
`forge-windows-x64.exe`. Intel Macs aren't prebuilt (GitHub is retiring Intel
runners) — build from source there.

**Build it yourself** with PyInstaller:

```bash
pip install -e ".[build]"          # installs pyinstaller
scripts/build_forge_binary.sh      # produces dist/forge
./dist/forge chat --help
```

The CLI is a thin HTTP client (only `httpx` + `structlog` beyond the standard
library), so the bundle stays small (~40 MB); the gateway/server stack is
excluded. The resulting `dist/forge` runs on its own — copy it onto a target
machine and run it directly.

> **Platform note:** PyInstaller bundles for the platform it runs on. Build on
> each OS you want to ship (macOS / Linux / Windows) — typically a CI matrix —
> rather than cross-compiling. This is a developer/packaging step; most users
> should just `pip install -e .` and use the `forge` console script above.

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

### `chat` — interactive assistant REPL

```
chat [-m MESSAGE] [--thread ID] [--session ID] [--title T]
     [--provider P] [--model M] [--timeout S]
     [--mode {ask,auto,plan}] [--no-stream] [--no-color]
     [--hooks PATH] [--no-hooks]
```

A Claude-Code-style terminal front-end for the MetaForge assistant. It's a
**thin client** over the gateway's `/v1/chat` surface (harness-backed), so the
agent loop, tools, and approval gates all run server-side. Streams the answer
token-by-token, renders a live tool-call timeline, and prompts for approval on
gated design changes.

```bash
# Interactive session (default: streaming, ask-mode)
python -m cli.forge_cli chat

# One-shot, scriptable
python -m cli.forge_cli chat -m "What is the stress margin on the bracket?"
```

| Flag | Default | Purpose |
|---|---|---|
| `-m, --message <text>` | — | One-shot: send a single message and exit |
| `--thread <id>` | new thread | Reuse an existing chat thread |
| `--session <id>` | random | Scope-entity id for a new thread |
| `--title <text>` | `CLI session` | Title for a new thread |
| `--provider <id>` | gateway default | Per-turn provider override |
| `--model <id>` | gateway default | Per-turn model override |
| `--timeout <s>` | `120` | Per-turn timeout (an agent turn runs inside the request) |
| `--mode {ask,auto,plan}` | `ask` | How gated change proposals are handled (see below) |
| `--no-stream` | off | Use request/refetch instead of SSE streaming |
| `--no-color` | off | Disable ANSI colors |
| `--hooks <path>` | `.forge/hooks.json` | Lifecycle-hooks config |
| `--no-hooks` | off | Disable lifecycle hooks |

#### Permission modes

The consequential action in chat is a **gated design-change proposal**
(`twin.propose_change`). `--mode` (or `/mode` in-session) governs how new
proposals are handled after each turn:

| Mode | Behavior |
|---|---|
| `ask` (default) | Prompt `[a]pprove / [r]eject / [s]kip` per proposal (interactive); one-shot mode just prints a notice |
| `auto` | Auto-approve new proposals (prints a warning on entry) |
| `plan` | Hold — list proposals but never apply them (nothing mutates the twin) |

#### Slash commands (interactive)

| Command | Effect |
|---|---|
| `/help` | List commands |
| `/model [provider] <model>` | Show or set the provider/model for the session |
| `/mode [ask\|auto\|plan]` | Show or set the permission mode |
| `/plan` | Shortcut for `/mode plan` |
| `/thread` | Show the current thread id |
| `/clear` | Start a fresh thread (clears context) |
| `/exit`, `/quit` | Leave the chat |

#### Hooks

Run your own shell commands on lifecycle events by creating `.forge/hooks.json`:

```json
{
  "hooks": {
    "session_start": [{"command": "echo session started"}],
    "user_prompt":   [{"command": "echo \"you asked: $FORGE_HOOK_MESSAGE\""}],
    "post_turn":     [{"command": "./scripts/on_turn.sh"}],
    "session_end":   [{"command": "echo bye"}]
  }
}
```

Events: `session_start`, `user_prompt` (before send), `post_turn` (after the
reply), `session_end`. Each command receives the payload as `FORGE_HOOK_*`
environment variables (e.g. `FORGE_HOOK_EVENT`, `FORGE_HOOK_MESSAGE`,
`FORGE_HOOK_THREAD_ID`) and as JSON on stdin. Hooks are best-effort — a failure
or timeout logs a warning and never breaks the turn.

> **Note:** the assistant only produces replies when the gateway has an LLM
> provider configured for the harness (`METAFORGE_CHAT_HARNESS` + credentials).
> Without one, `forge chat` still runs but reports "no reply".

### `routine` — scheduled background runs

```
routine [--file PATH] {add,list,remove,run-due}
routine add "<prompt>" --every 30m [--provider P] [--model M] [--mode M]
routine list
routine remove <id>
routine run-due
```

A daemonless way to run assistant prompts on a schedule (the "routines" idea).
Routines are stored in `.forge/routines.json`; `run-due` fires every routine
whose interval has elapsed (creating an assistant thread and sending the prompt)
and records `last_run`. Wire `run-due` to OS cron or a loop for real scheduling:

```bash
# add a nightly design-review prompt
python -m cli.forge_cli routine add "Review the latest DRC results" --every 1d

# in crontab: fire due routines every 15 minutes
*/15 * * * * python -m cli.forge_cli routine run-due
```

Intervals are `30s` / `10m` / `2h` / `1d` (not full cron). Each `run-due` is
best-effort — one routine's failure doesn't stop the others.

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
