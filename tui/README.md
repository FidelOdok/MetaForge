# forge-tui (Ink)

A terminal UI for MetaForge, built with [Ink](https://github.com/vadimdemedes/ink)
(React for the terminal). It is a thin client of the gateway — the same surface
the Python `forge` CLI drives — focused on the **gated design-flow HITL loop**:
streaming chat, live run/gate transitions, and approve/reject at each gate.

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
