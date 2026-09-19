---
updated: 2026-08-11
---

# Dashboard

React/TypeScript app — `dashboard/` (Vite, Playwright e2e, vitest). Real and mature, not scaffolding.

- **Design system**: the "Kinetic Console" spec (color tokens, nav rail, Material Symbols icons, glass panels, page-by-page layouts) is the source of truth for visual design. Always find and read the relevant design-system spec before implementing dashboard UI — don't improvise the look.
- Dev loop: `docker compose up gateway dashboard-dev` (the override file enables hot-reload with polling — see [CI, Lint & Docker Gotchas](ci-lint-and-docker-gotchas.md) for why polling matters on WSL2).
- Chat, Twin viewer, approval workflows, and project pages all live under `dashboard/src/` — check there before assuming a page doesn't exist yet.
- **Vite dev server must be restarted on file deletion** (`docker restart metaforge-dashboard-dev-1`) — HMR chokes trying to hot-reload a deleted module and serves a stale bundle; a browser hard-reload is not enough.
- `crypto.randomUUID()` needs a secure context (HTTPS/localhost) — over plain HTTP (e.g. via Tailscale) it's `undefined` and crashes. Use `dashboard/src/utils/id.ts`'s `generateId()` instead.

Related: [Chat Harness & SSE](chat-harness-and-sse.md) for how the chat UI actually streams.
