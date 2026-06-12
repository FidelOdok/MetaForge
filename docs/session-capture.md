# Agent session capture

Capturing what an agent *does* and *thinks* while driving MetaForge over MCP,
into the digital thread's `/sessions`. Three layers (see MET-492):

| Layer | Mechanism | Captures | Clients |
|-------|-----------|----------|---------|
| **A** | MCP server middleware (MET-496) | actions (every tool call) | **all** MCP clients, zero config |
| **B** | client capture core + adapters (MET-497/498) | actions **+ reasoning** | any client with hooks or a local transcript |
| **C** | `session.*` / `twin.record_decision` tools (MET-494/495) | curated thoughts + typed decisions | any cooperating agent |

Layer A is always on (server-side). Layer B is what this doc covers.

## The core

`tools/session_capture/metaforge_capture.py` — stdlib + httpx only, no MetaForge
imports, so it runs in any client's environment. CLI:

```
metaforge-capture --client <name> --session <id> ensure-session [...]
metaforge-capture --client <name> --session <id> push-event --type <t> --message <m> [--data JSON]
metaforge-capture --client <name> --session <id> push-transcript-delta --transcript <file>
metaforge-capture --client <name> --session <id> complete [--status ...] [--summary ...]
metaforge-capture --client <name> tail --path '<glob>' [--parser <name>] [--follow]
```

Config: `METAFORGE_GATEWAY_URL` (default `http://localhost:8000`),
`METAFORGE_MCP_API_KEY`, `METAFORGE_SESSION_CAPTURE=off` (kill-switch). Always
exits 0 — capture never breaks the host turn.

## The universal fallback: `tail`

`tail --client <name> --path '<glob>'` watches a client's local transcript
files and pushes deltas (one MetaForge session **per file**, byte-cursor keyed
so a restart never re-emits). It works for **any** client that writes a local
transcript — you only need a parser for that client's JSONL shape
(`tools/session_capture/parsers.py`, registry keyed by client name). Adding a
client = one parser function.

One-shot (cron-friendly) by default; `--follow` polls.

## Per-client status

| Client | Mechanism | Actions | Thoughts | Status |
|--------|-----------|---------|----------|--------|
| **Claude Code** | hooks (`tools/session_capture/claude_code_adapter.py`) | ✅ | ✅ transcript | shipped (MET-497) |
| **Codex CLI** | `tail` parser over `~/.codex/sessions/*.jsonl` | via tailer | ✅ | parser shipped (best-effort schema); `notify` adapter TODO |
| **Cursor** | native `hooks.json` | ✅ | ⚠️ | **deferred** — use `tail` if Cursor writes a local transcript; native hook adapter pending schema verification |
| **OpenCode** | TS plugin on its event bus | ✅ | ✅ | **deferred** — plugin pending; `tail` works if it persists a transcript |
| **Gemini CLI** | (no stable hook system at time of writing) | — | ⚠️ | **deferred** — `tail` + a parser once its chat-log format is confirmed |
| **claude.ai web** | none (cloud, no local exec) | via Layer A | ✗ | **not adaptable** client-side — Layer A only; sessions labelled by OAuth subject (MET-480) |

### Why Cursor / OpenCode / Gemini are deferred

Their native hook/plugin schemas and local-transcript formats move fast and
weren't verifiable at implementation time. Rather than ship guesses, the
**`tail` fallback + parser registry** is the supported path for them today:
point `tail` at their transcript directory and add a parser. Native adapters
(richer, real-time) are follow-ups tracked on MET-498.

## Install (Claude Code)

See `tools/session_capture/claude_code/README.md` — merge `settings.snippet.json`
into `.claude/settings.json`.
