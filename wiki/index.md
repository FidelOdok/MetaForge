# Index

Content-oriented catalog of every page in this wiki. Read this first when answering a question or starting work in an unfamiliar area, then drill into the pages that look relevant. Update this file — same PR, same session — whenever a page is added, renamed, or removed.

## Architecture

Grounded against actual current code, not CLAUDE.md's aspirational canonical layout.

| Page | Summary | Updated |
|---|---|---|
| [Gateway Service](gateway-service.md) | FastAPI front door, `api_gateway/server.py`, ~14 routers | 2026-08-11 |
| [Orchestrator](orchestrator.md) | Scheduler/workflow engine — in-memory today, Temporal scaffolding not yet live | 2026-08-11 |
| [Digital Twin](digital-twin.md) | `twin_core/`, dual-backend (in-memory default, Neo4j opt-in); legacy `digital_twin/` dir still present | 2026-08-11 |
| [Skill Registry](skill-registry.md) | Skill catalog + MCP bridging layer | 2026-08-11 |
| [MCP Core & Tool Registry](mcp-core-and-tool-registry.md) | Wire protocol (`mcp_core/`) vs. tool adapters (`tool_registry/`) — two distinct layers | 2026-08-11 |
| [Domain Agents](domain-agents.md) | mechanical/electronics/firmware/simulation/supply_chain/compliance — all real implementations | 2026-08-11 |
| [CLI & TUI](cli-and-tui.md) | `cli/forge_cli/` (mature) vs `tui/` (canonical target, still scaffold) | 2026-08-11 |
| [Dashboard](dashboard.md) | React/Vite app, Kinetic Console design system | 2026-08-11 |
| [Observability Stack](observability-stack.md) | structlog + OTel + Prometheus wiring | 2026-08-11 |
| [Agent Session Capture](agent-session-capture.md) | How tool calls and reasoning get recorded into the digital thread | 2026-08-11 |

## Operational knowledge

Promoted from personal Claude Code memory once proven durable — applies to anyone working here, not just one contributor.

| Page | Summary | Updated |
|---|---|---|
| [MCP HTTP Sidecar](mcp-http-sidecar.md) | How dev MCP is reached over HTTP transport, and what breaks reconnection | 2026-08-11 |
| [CAD/FEA Adapter Containers](cad-fea-adapter-containers.md) | Why heavy tools silently fall back to in-process and fail with `-32001` | 2026-08-11 |
| [Twin Project Scoping](twin-project-scoping.md) | Project membership is stored twice; both writes are required | 2026-08-11 |
| [FreeCAD & CAD Authoring Conventions](freecad-and-cad-authoring.md) | Headless FreeCAD architecture, part-naming rule, STEP-colour-only rule | 2026-08-11 |
| [Chat Harness & SSE](chat-harness-and-sse.md) | How dashboard/TUI chat actually streams, the envelope gotcha, TUI debugging | 2026-08-11 |
| [CI, Lint & Docker Gotchas](ci-lint-and-docker-gotchas.md) | `ruff check` vs `ruff format --check`, forcing a real Docker IP change | 2026-08-11 |
| [Repository Structure Drift](repository-structure-drift.md) | Where the code has diverged from CLAUDE.md's canonical layout diagram | 2026-08-11 |

See [`log.md`](log.md) for when each page was ingested or last touched, and [`README.md`](README.md) for the pattern this wiki follows.
