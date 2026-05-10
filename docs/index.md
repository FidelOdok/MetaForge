# MetaForge Documentation

> **Status:** Phase 1 (v0.1). Engineer-facing user docs. For
> contributor / architecture material, see the bottom section.

MetaForge is a **local-first control plane for hardware design** —
it turns engineer intent into reviewable, manufacturable
deliverables by orchestrating specialist AI agents over the
[Model Context Protocol](https://modelcontextprotocol.io). New
here? Start with [Getting Started](getting-started.md).

## User Guide

If you're a hardware engineer using MetaForge as a tool, these are
the docs you need.

| Doc | What's in it |
|---|---|
| [Getting Started](getting-started.md) | 5-step quickstart from a fresh clone. Three parallel tracks: CLI, Claude Code MCP, dashboard. |
| [CLI Reference](cli-reference.md) | Every `python -m cli.forge_cli` subcommand with flags, examples, output shapes. |
| [Dashboard Tour](dashboard-tour.md) | One section per route — what it shows, what data drives it. |
| [Project Structure](project-structure.md) | The on-disk layout MetaForge expects (PRD.md, constraints.json, eda/, bom/, .forge/…). |
| [Capability Matrix](capability-matrix.md) | What works in Phase 1: 30 MCP tools, 11 dashboard routes, Phase-1 limits. |
| [Troubleshooting](troubleshooting.md) | Common errors and recovery — backend fallback, WSL2 file locks, MCP issues, missing optional deps. |

## Integrations

How to drive MetaForge from external harnesses.

- [Claude Code](integrations/claude-code.md) — spawn the MetaForge
  MCP server as a Claude Code subprocess.
- [Codex](integrations/codex.md) — Codex variant of the same setup.
- [MCP Config Examples](integrations/mcp-config-examples.md) —
  reference for stdio / HTTP / SSE transports and auth shapes.
- [LightRAG UI](integrations/lightrag-ui.md) — operator guide for
  the standalone knowledge UI on `:9621`.

## Reference example

[`examples/drone_flight_controller/`](https://github.com/MetaForge-HA/MetaForge/tree/main/examples/drone_flight_controller#readme) —
a 4-layer PCB around the STM32F405RGT6, walked through six
engineering disciplines. Runs entirely on mock adapters; no Docker
required.

## Architecture & contributors

Lower-level material for people **building** MetaForge rather than
using it. Expect specs, ADRs, and architectural diagrams.

- [Architecture overview](architecture.md)
- [Roadmap](roadmap.md) — phased delivery plan
- [Skill spec](skill_spec.md) — the atomic unit of expertise
- [MCP spec](mcp_spec.md) — MetaForge's wire protocol
- [Twin schema](twin_schema.md) — Neo4j graph model
- [Testing strategy](testing-strategy.md) — 12-level test taxonomy
- [Governance](governance.md) — branch strategy, PR rules,
  directory ownership

If you're contributing code, start with the repo-root
[`CLAUDE.md`](https://github.com/MetaForge-HA/MetaForge/blob/main/CLAUDE.md)
file — it covers the development workflow, the observability
requirements, and the git conventions.

## Operations

For SREs running a MetaForge deployment:

- [Runbooks (in repo)](https://github.com/MetaForge-HA/MetaForge/tree/main/docs/runbooks) —
  `gateway-down.md`, `neo4j-unreachable.md`,
  `kafka-consumer-stopped.md`, `device-telemetry-stopped.md`,
  `fleet-anomaly-pattern.md`.

## UAT artifacts

QA-internal — kept in the repo for traceability, not on this site:

- [KB master test plan](https://github.com/MetaForge-HA/MetaForge/blob/main/docs/uat/kb-test-plan.md)
- [Cycle 1+2 acceptance matrix](https://github.com/MetaForge-HA/MetaForge/blob/main/docs/uat/cycle-1-2-acceptance-matrix.md)
- Cycle 3 run reports under [`docs/uat/`](https://github.com/MetaForge-HA/MetaForge/tree/main/docs/uat).
