---
updated: 2026-08-11
---

# CLI & TUI

Two separate client implementations, at different maturity (verified 2026-08-11):

- **`cli/forge_cli/`** (Python, argparse) — the mature, fully-functional CLI today: auth, cad, chat, ingest, knowledge, memory, projects, routines, runs, sources are all real modules. This is what `python -m cli.forge_cli` invokes.
- **`tui/`** (TypeScript, installs as `~/.local/bin/forge`) — the canonical target going forward per its own README ("one entrypoint, two modes": bare invocation launches the Ink/React TUI, subcommands run scriptable). Currently "Iteration 1 (scaffold)" — config sharing, gateway health, project list work; chat streaming and gate-approval are further along than that README suggests but still actively evolving (see [Chat Harness & SSE](chat-harness-and-sse.md)).
- **`cli/main.py` / `cli/config.py` are 0-byte placeholders**; `cli/commands/` has only a `.gitkeep`. Don't assume dead code — it's just not started.
- Practical rule: if you need something that works today from a script, use `cli/forge_cli/`. If you're extending the long-term interactive client, that's `tui/`.
- `forge auth` (in `tui/`) handles login and uses gateway-stored credentials plus `METAFORGE_HARNESS_ADMIN_TOKEN`.
- Debugging the TUI: it's an Ink app, so it cannot log to stdout — see `~/.forge/logs/session.log` and `--debug`/`FORGE_LOG=1`, detailed in [Chat Harness & SSE](chat-harness-and-sse.md).
