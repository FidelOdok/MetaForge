# forge (Ink — unified CLI + TUI)

A single MetaForge client, built with [Ink](https://github.com/vadimdemedes/ink)
(React for the terminal). **One entrypoint, two modes chosen by invocation:**

```bash
forge                       # bare + a TTY → interactive TUI
forge runs list             # a subcommand → run it, print, exit
forge runs create --request-json '{"goal":"…","flow":"design_v1","project_id":"…"}'
forge runs approve <id>     # approve a gated run
forge runs watch <id>       # stream status transitions
forge chat -m "hello"       # one-shot assistant turn
forge projects              # list projects
forge twin list             # list twin nodes
forge config show|set model openai/gpt-4o
forge <cmd> --json          # machine output for scripts
```

Both modes share the same typed gateway client, so there's one implementation.
The TUI is the interactive HITL surface (streaming chat, live gate approvals);
the commands are the scriptable surface (automation, CI, hooks).

Why Ink: the reasoning brain and gateway are Python, but the *client* stack
aligns with the React dashboard, so components and mental model are shared
across web + terminal.

## Status

Iteration 1 (scaffold): config sharing (`~/.forge/config.json`), gateway
health, project list, status bar. Chat streaming and the gate-approval modal
are next.

## Develop

```bash
npm install
npm run gen:types     # regenerate TS types from ../docs/reference/openapi.json
npm run typecheck
npm run dev           # launches the TUI (requires a TTY)
```

Config is read from `~/.forge/config.json` (shared with the Python CLI):
`gateway_url`, `provider`, `model`, `mode`.

Keys: `^T` chat · `^R` runs · `^N` new run · `^B` twin · `Esc`/`^C` quit.

## Build & distribute

```bash
npm run build              # stamp provenance + tsc -> dist/ ; run: node dist/cli.js
forge-tui --version        # -> forge-tui 0.1.0 (<commit>, <date>)
```

`npm run stamp` writes the git commit + date into `src/build-info.ts`, so
`--version` reports exactly which build you're running — making the "is my CLI
stale?" question answerable (the trap that bit the Python binary).

A true standalone single binary (no Node needed) is produced with
[Bun](https://bun.sh):

```bash
npm run bundle:bin         # -> dist/forge-tui  (requires bun)
```

## Layout

```
src/
  cli.tsx              # entry — renders <App/>
  App.tsx              # root screen
  config.ts            # reads ~/.forge/config.json
  api/client.ts        # typed gateway HTTP client
  api/schema.ts        # generated from OpenAPI (npm run gen:types)
  components/          # Ink components (StatusBar, …)
```
