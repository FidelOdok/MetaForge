# MetaForge Wiki

A compounding knowledge base for this codebase, in the spirit of Andrej Karpathy's ["LLM wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern: plain markdown, one page per entity, cross-linked, written and maintained by whichever agent (or human) is working in this repo at the time.

## What this is not

- **Not `docs/`.** `docs/` is the curated, published architecture reference (MkDocs site, `--strict` CI). It answers "how does the system work." This wiki answers "what do you need to know to work in this repo effectively that isn't obvious from reading the code once" — drift between docs and reality, gotchas, dead ends, where things actually live vs. where an architecture diagram says they should live.
- **Not personal memory.** Claude Code's per-user memory (`~/.claude/projects/.../memory/`) is private to one person's local sessions. This wiki is checked into git — every contributor and every agent that clones this repo gets it. Promote a personal memory into this wiki once it's proven durable and would help someone else, not before.
- **Not a replacement for reading code.** A wiki page is a shortcut and a warning label, not a substitute for verifying against current source.

## How to use it

- Before working in an unfamiliar part of the repo, check if a page here already covers it.
- When you learn something durable and non-obvious — a gotcha, a piece of drift between docs and reality, a "look here not there" fact — write or update a page. Don't wait for permission; that's the whole point of a compounding wiki.
- Keep pages short. An entity page is a note, not an essay. Link out to `docs/` for the full architectural story.
- Cross-link with normal relative markdown links (`[Twin Core](digital-twin.md)`) so pages read as a graph, not a list, and render as real links on GitHub.
- Correct or delete a page the moment you find it's wrong. Stale wiki pages are worse than no wiki — date-stamp claims that are likely to rot (`verified 2026-08-11`) so the next reader can judge freshness.
- Add new pages to the index below in the same change that creates them.

## Index

### Architecture (grounded against actual code — not the aspirational canonical layout in CLAUDE.md)

- [Gateway Service](gateway-service.md) — FastAPI front door, `api_gateway/server.py`
- [Orchestrator](orchestrator.md) — scheduler/workflow engine, in-memory today, Temporal scaffolding not yet live
- [Digital Twin](digital-twin.md) — `twin_core/`, dual-backend (in-memory default, Neo4j opt-in); legacy `digital_twin/` dir still present
- [Skill Registry](skill-registry.md) — skill catalog + MCP bridging
- [MCP Core & Tool Registry](mcp-core-and-tool-registry.md) — wire protocol vs. tool adapters, the two layers
- [Domain Agents](domain-agents.md) — mechanical/electronics/firmware/simulation/supply_chain/compliance, all real
- [CLI & TUI](cli-and-tui.md) — `cli/forge_cli/` (mature) vs `tui/` (canonical target, still scaffold)
- [Dashboard](dashboard.md) — React/Vite app
- [Observability Stack](observability-stack.md) — structlog + OTel + Prometheus wiring
- [Agent Session Capture](agent-session-capture.md) — how tool calls and reasoning get recorded

### Operational knowledge (promoted from personal memory — durable, applies to anyone working here)

- [MCP HTTP Sidecar](mcp-http-sidecar.md) — how dev MCP is reached, and what breaks reconnection
- [CAD/FEA Adapter Containers](cad-fea-adapter-containers.md) — why heavy tools silently fall back and fail with `-32001`
- [Twin Project Scoping](twin-project-scoping.md) — project membership is stored twice; both must be written
- [FreeCAD & CAD Authoring Conventions](freecad-and-cad-authoring.md) — headless FreeCAD architecture, naming, colour rules
- [Chat Harness & SSE](chat-harness-and-sse.md) — how dashboard/TUI chat actually streams, and where it breaks
- [CI, Lint & Docker Gotchas](ci-lint-and-docker-gotchas.md) — ruff check vs format, forcing a real IP change
- [Repository Structure Drift](repository-structure-drift.md) — where the code has diverged from CLAUDE.md's canonical layout
