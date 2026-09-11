# Log

Append-only, chronological. Each entry starts with `## [YYYY-MM-DD] <operation> | <short title>` so it stays greppable (`grep "^## \[" wiki/log.md | tail -5`). Operations: `init`, `ingest`, `query`, `lint`.

## [2026-08-11] init | Seeded initial wiki, 17 pages

Grounded 10 architecture pages against actual current code (not CLAUDE.md's aspirational canonical layout): Gateway Service, Orchestrator, Digital Twin, Skill Registry, MCP Core & Tool Registry, Domain Agents, CLI & TUI, Dashboard, Observability Stack, Agent Session Capture. Promoted 7 operational-knowledge pages out of personal Claude Code memory (durable, non-personal): MCP HTTP Sidecar, CAD/FEA Adapter Containers, Twin Project Scoping, FreeCAD & CAD Authoring Conventions, Chat Harness & SSE, CI/Lint/Docker Gotchas, Repository Structure Drift. Side-finding filed into Repository Structure Drift + fixed directly in CLAUDE.md: the session-capture section named an uncommitted hook file.

## [2026-08-11] lint | Aligned structure to Karpathy's actual LLM-wiki spec

Initial seed had drifted from the source pattern — no `log.md`, index folded into `README.md` instead of a dedicated `index.md`, no `updated:` frontmatter for staleness tracking, and the CLAUDE.md schema section didn't define Ingest/Query/Lint operations. Fixed: added this file, split the catalog into `index.md`, added `updated: 2026-08-11` frontmatter to all 17 pages, rewrote `README.md` around the three-layer model (raw sources / wiki / schema), and rewrote CLAUDE.md's wiki section to define the three operations explicitly.
