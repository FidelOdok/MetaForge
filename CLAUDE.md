# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Is MetaForge

MetaForge is a **local-first control plane** that turns human intent into reviewable, manufacturable hardware deliverables. It orchestrates specialist AI agents that interface with real engineering tools (KiCad, FreeCAD, CalculiX, SPICE) to produce schematics, BOMs, PCB layouts, firmware scaffolds, manufacturing files, and test plans.

**Prime Rule**: If it can't be versioned, reviewed, and built — MetaForge doesn't output it.

## Planning & Reference Repository

The **MetaForge-Planner** repo (`FidelOdok/MetaForge-Planner`) contains all architectural plans, specifications, and documentation. Use it as the source of truth for:

- **Architecture**: `docs/architecture/` — system vision, orchestrator design, technical specs
- **Repository Structure**: `docs/architecture/repository-structure.md` — canonical monorepo layout
- **Framework**: `docs/FRAMEWORK_MAPPING.md` — 25-discipline taxonomy with phase-by-phase implementation
- **Roadmap**: `docs/roadmap.md` and `docs/architecture/mvp-roadmap.md` — phased delivery plan
- **Agent specs**: `docs/agents/` — per-agent design documents
- **Tool catalog**: `TOOLS_INTEGRATION_CATALOG.md` — all external tool integrations
- **Vision**: `VISION.md` — project principles and non-goals

When planning new features or making architectural decisions, fetch the relevant docs from `FidelOdok/MetaForge-Planner` using GitHub tools before implementing. Do not invent architecture — follow what's specified there.

## Project & Task Management (Linear)

All project tracking lives in **Linear** under the **MetaForge** team:

- **Project**: "MetaForge Platform v1.0" (ID: `9ae4e6e0-3f38-4fea-be87-0876f87a83fd`)
- **Team**: MetaForge (ID: `e30e7c0e-d9a5-44af-9cb0-5745aa3dc78a`)

### Workflow

1. **Before starting work**: Check Linear for the relevant issue/epic using `list_issues` or `get_issue`
2. **When starting an issue**: Update its status from Backlog to In Progress
3. **When done**: Update status to Done and add a comment with what was implemented
4. **New work discovered**: Create a Linear issue under the appropriate epic before implementing

### Epic Structure (MET-5 through MET-10, MET-40)

| Epic | Scope | Phase |
|------|-------|-------|
| MET-40: Phase 0 Specs | Finalize all specification documents before dev | Phase 0 |
| MET-5: Digital Twin Core | Graph engine, versioning, constraints, Twin API | Phase 1 |
| MET-6: Skill System | Registry, loader, schema validator, MCP bridge | Phase 1 |
| MET-7: MCP Infrastructure | Client, wire protocol, tool registry, adapters | Phase 1-2 |
| MET-8: Mechanical Agent | Stress validation, meshing, tolerances (first vertical) | Phase 1 |
| MET-9: Electronics Agent | ERC, DRC, power budget, KiCad adapter | Phase 2 |
| MET-10: Assistant Layer | IDE extensions, approval workflow, CLI | Phase 2-3 |

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Primary Language | Python 3.11+ (Gateway, Agents, Twin, Skills, MCP) |
| CLI / Dashboard | Node.js / TypeScript (CLI only) |
| CLI Libraries | Commander.js, Inquirer, Chalk |
| Gateway | FastAPI + Uvicorn |
| Agent Framework | PydanticAI + Temporal (ADR-001) |
| LLM Providers | `openai` + `anthropic` SDKs via unified abstraction |
| Validation | Pydantic v2 |
| Workflow Engine | Temporal (Python SDK) |
| Graph Database | Neo4j |
| Event Bus | Apache Kafka |
| Observability | OpenTelemetry + structlog + Prometheus + Grafana |

## Dual-Mode Operation

MetaForge supports two operational modes:

- **Assistant Mode** (default): Human edits design files directly; MetaForge validates post-edit and flags issues. Read-only by default — explicit approval required for writes.
- **Autonomous Mode**: AI agents drive the design loop (propose → validate → refine). Human reviews and approves at gate checkpoints.

## Build & Development Commands

```bash
# Python (platform core)
pip install -e ".[dev]"   # Install in dev mode
pytest                    # Run tests
ruff check .              # Lint
mypy .                    # Type check

# Node.js (CLI only)
cd cli && npm install     # Install CLI dependencies
npm run build             # Compile TypeScript (tsc)
npm run dev               # Dev mode (ts-node)
```

CLI binary is `forge` (or `metaforge`), entry point at `cli/index.ts`.

## Git Workflow

**Never commit directly to `main`.** All changes follow this workflow:

1. **Branch** — Create a feature branch from `main` (e.g., `feat/met-15-twin-api`)
2. **Implement** — Commit changes to the feature branch
3. **Test branch** — Run `pytest`, `ruff check .`, `mypy .` on the branch
4. **Pull request** — Open a PR to `main` with a summary of changes
5. **Merge** — Merge the PR into `main` (squash or merge commit)
6. **Test main** — Verify `main` passes all checks after merge

## Commit Convention

Use **Conventional Commits** format:

```
type(scope): description
```

**Types**: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `style`, `perf`, `build`

**Scope** (optional): module name — `twin-core`, `skill-system`, `mcp`, `cli`, `gateway`, `orchestrator`

**Examples**:
```
feat(twin-core): implement Twin API facade
fix(skill-system): handle missing definition.json gracefully
docs: update twin_schema.md to v0.2
test(constraint-engine): add cross-domain validation tests
chore: add .gitignore
```

## Platform Source Repository Structure (Modular Monorepo)

This repo follows the canonical layout defined in `FidelOdok/MetaForge-Planner` at `docs/architecture/repository-structure.md`. Each top-level directory maps to an architectural layer:

```
MetaForge/
├── cli/                        # CLI commands (forge setup, forge run, etc.)
│   ├── index.ts                # Main CLI entry point
│   └── commands/               # Command implementations
│
├── api_gateway/                # Gateway Service — HTTP/WebSocket "front door"
│   ├── routes/
│   ├── middleware/
│   └── auth/
│
├── orchestrator/               # Coordination engine — the "brain"
│   ├── event_bus/              # Pub/sub for design change events
│   ├── dependency_engine.py    # Inter-agent dependency resolution
│   ├── workflow_dag.py         # DAG definition and execution
│   ├── iteration_controller.py # Propose-validate-refine loop
│   └── scheduler.py            # Agent execution queuing
│
├── twin_core/                  # Digital Twin — single source of design truth
│   ├── models/                 # WorkProduct, Constraint, Relationship, Version
│   ├── graph_engine.py         # Core graph CRUD + traversal
│   ├── versioning/             # Branch, merge, diff operations
│   ├── constraint_engine/      # Cross-domain constraint validation
│   ├── validation_engine/      # Schema validation for work product types
│   └── api.py                  # Public Twin API
│
├── skill_registry/             # Skill management layer
│   ├── registry.py             # Skill catalog with auto-discovery
│   ├── loader.py               # Dynamic loading from definition files
│   ├── schema_validator.py     # Input/output schema validation (Pydantic)
│   ├── skill_base.py           # Abstract base class for all skills
│   └── mcp_bridge.py           # Skill tool calls → MCP protocol
│
├── domain_agents/              # One agent per engineering discipline
│   ├── mechanical/
│   │   ├── agent.py            # Agent orchestration logic
│   │   ├── adapters/           # Domain-specific adapters
│   │   └── skills/             # Skills follow strict directory convention
│   │       └── validate_stress/
│   │           ├── definition.json   # Skill metadata
│   │           ├── SKILL.md          # Human-readable docs
│   │           ├── schema.py         # Input/output schemas
│   │           ├── handler.py        # Execution logic
│   │           └── tests.py          # Skill-specific tests
│   ├── electronics/
│   ├── firmware/
│   └── simulation/
│
├── mcp_core/                   # MCP protocol client layer
│   ├── client.py               # MCP client for tool communication
│   ├── protocol.py             # Wire protocol implementation
│   └── schemas.py              # MCP message schemas
│
├── tool_registry/              # MCP-based tool access (containerized)
│   ├── registry.py             # Tool catalog with capabilities
│   ├── execution_engine.py     # Invocation, timeout, retry
│   ├── mcp_server/             # MCP server template for adapters
│   └── tools/                  # Individual tool adapters
│       ├── calculix/           # FEA analysis
│       ├── freecad/            # CAD operations
│       ├── kicad/              # PCB/schematic validation
│       └── spice/              # Circuit simulation
│
├── ide_assistants/             # Human-in-the-loop IDE integrations
│   ├── vscode_extension/       # VS Code (firmware development)
│   ├── pcb_extension/          # KiCad plugin
│   └── cad_extension/          # FreeCAD plugin
│
├── tests/                      # Cross-cutting tests
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── examples/                   # Reference projects
│   └── drone-fc/               # Drone flight controller (first demo)
│
└── docs/                       # Project-level documentation
```

**Note**: The platform core is implemented in Python (`.py`). Only the CLI layer (`cli/`) uses TypeScript (`.ts`).

## User Project Structure (What MetaForge Manages)

When a user runs `forge setup`, MetaForge creates this structure for their hardware project:

```
project/
├── PRD.md                      # Human intent (product requirements)
├── constraints.json            # Design rules and constraints
├── decisions.md                # Design decisions log
├── eda/kicad/                  # Schematic + PCB files
├── bom/                        # BOM, alternates, costing
├── firmware/src/               # Firmware source + pinmap.json
├── manufacturing/              # Gerbers, pick & place
├── tests/bringup.md            # Bring-up checklists
└── .forge/
    ├── sessions/               # Agent session records
    └── traces/                 # Execution traces
```

## Architecture Overview

```
Human Intent (PRD, constraints)
         |
    CLI / IDE Assistant
         |
    Gateway Service (HTTP/WebSocket — the "front door")
         |
    Orchestrator (coordination engine — the "brain")
         |
    Domain Agents (specialist per discipline)
         |
    Skill System (atomic, schema-validated units of expertise)
         |
    MCP Protocol Layer (tool access — never direct invocation)
         |
    Tool Adapters (KiCad, FreeCAD, CalculiX, SPICE — containerized)
         |
    Digital Twin (Digital Thread — single source of design truth)
```

Key architectural rules:
- **Agents never call tools directly** — all tool access goes through MCP protocol
- **Digital Twin owns all state** — agents read from and propose changes to the Twin
- **Human-in-the-loop** — read-only by default, explicit approval required for writes
- **Skills are the atomic unit** — deterministic, schema-validated, independently testable
- **Git-native** — everything versioned, diffed, reviewable

## Phase Scope (Do Not Conflate)

- **Phase 1 (v0.1-0.3)**: 6-7 specialist agents, 6-7 core disciplines. Electronics-heavy products (IoT, drones, embedded). KiCad read-only (ERC/DRC/BOM/Gerber export). Timeline: 6 months total (3-4 months core dev + 1-2 months testing/docs).
- **Phase 2 (v0.4-0.6)**: 19 total agents, 19 disciplines. KiCad write capabilities. Industrial Design + Prototyping added.
- **Phase 3 (v0.7-1.0)**: 25 total agents, all 25 disciplines.

## Terminology

- **Gateway Service**: HTTP/WebSocket API server (the "front door")
- **Orchestrator**: Coordination engine within Gateway (the "brain")
- **Skill**: Atomic unit of domain expertise (deterministic, schema-validated)
- **Digital Twin**: WorkProduct graph that owns all design state
- **MCP**: Model Context Protocol — the wire protocol for tool access
- **Domain Agent**: Specialist agent for one engineering discipline (1:1 ratio)

## Testing Requirements

**Every module must have tests.** MetaForge uses a 12-level testing taxonomy — see [`docs/testing-strategy.md`](docs/testing-strategy.md) for the full taxonomy, per-level tooling, and coverage status.

For Phase 1, the minimum bar per module is:

- **Static Analysis (Level 1)**: `ruff check` and `mypy --strict` pass with zero errors
- **Unit Tests (Level 2)**: all public functions exercised in isolation via `pytest tests/unit/`
- **Component Tests (Level 3)**: key module entry points tested with in-memory doubles (`InMemoryTwinAPI`, `InMemoryMcpBridge`)
- **Integration Tests (Level 5)**: cross-module wiring verified via `pytest tests/integration/`
- **E2E / System Tests (Level 8)**: at least one full vertical test per agent in `pytest tests/e2e/`

Levels 4, 9, 10, 11, and 12 (Contract, Performance, Security, Acceptance, Chaos) are Phase 2+ scope.

## Observability Requirements

**Every module must include observability.** MetaForge uses a 7-level observability taxonomy. When creating or modifying any Python module, instrument the following levels:

1. **Logs (Level 1)** — structured logging via `structlog`: `logger = structlog.get_logger(__name__)` — log key operations with keyword arguments. Implementation: `observability/logging.py` (`configure_logging`, `add_trace_context`).

2. **Metrics (Level 2)** — via `observability.metrics`: register counters, histograms, and gauges in `MetricsRegistry` for throughput, latency, and error rates. Implementation: `observability/metrics.py` (`MetricDefinition`, `MetricsRegistry`).

3. **Traces (Level 3)** — distributed tracing via OpenTelemetry: `tracer = get_tracer("module.name")` from `observability.tracing` — wrap key operations with `tracer.start_as_current_span()`, set relevant attributes. Call `span.record_exception(exc)` in except blocks. Implementation: `observability/tracing.py` (`get_tracer`, `NoOpTracer` fallback).

4. **Profiling (Level 4)** — CPU/memory profiling via Pyroscope. Captured at the process level automatically — module authors do not need to add instrumentation. To inspect: use `mcp__grafana__query_pyroscope` or the Grafana Pyroscope datasource.

5. **Alerting (Level 5)** — when writing new metrics, add corresponding alert rules to `observability/alerting/rules.yaml` for anomalous values (error rate spikes, latency SLO breaches). Do not create ad-hoc Grafana alerts — all alert rules must be version-controlled in `alerting/`.

6. **Synthetic Monitoring (Level 6)** — proactive fake requests to verify the system is alive. Implemented via the `dashboard-tester` agent (`.claude/agents/dashboard-tester.agent.md`) and the `/test-dashboard` command. Not yet integrated as a polling service in CI.

7. **RUM / Real User Monitoring (Level 7)** — experience as seen by actual users. In progress (MET-288). Front-end concern only — no per-module Python instrumentation required.

Follow existing patterns in `observability/tracing.py` (get_tracer, NoOpTracer fallback) and `observability/metrics.py` (MetricDefinition, MetricsRegistry). The system degrades gracefully without the OTel SDK installed.

## Observability Stack (Grafana)

The dev environment includes a full observability stack accessible via Grafana MCP:

| Datasource | UID | Purpose |
|-----------|-----|---------|
| Prometheus | `PBFA97CFB590B2093` | Metrics (HTTP latency, error rates, agent task counters) |
| Loki | `loki` | Structured logs (all gateway/agent logs via OTel) |
| Tempo | `P214B5B846CF3925F` | Distributed traces (spans across gateway → orchestrator → agent → skill) |

### Log Labels

Loki logs are labeled with `service_name` (currently `metaforge-gateway`) and `deployment_environment` (`docker`). Each log entry includes OTel context: `trace_id`, `span_id`, `scope_name` (logger), `severity_text`, and `code_file_path`.

### Custom Agents & Commands

| Agent / Command | File | Purpose |
|----------------|------|---------|
| `dashboard-tester` | `.claude/agents/dashboard-tester.agent.md` | E2E dashboard testing via Playwright + Grafana observability validation |
| `bug-hunter` | `.claude/agents/bug-hunter.agent.md` | Scans Grafana for errors/anomalies, triages, deduplicates against Linear, files bugs |
| `/test-dashboard` | `.claude/commands/test-dashboard.md` | Launch dashboard-tester agent with scenario or natural language |
| `/bug-hunt` | `.claude/commands/bug-hunt.md` | Launch bug-hunter agent — scan last 1h (default), custom window, or focused search |

### Bug Hunt Workflow

`/bug-hunt` runs a 5-phase pipeline:

1. **Pre-flight** — verify Grafana datasources are reachable
2. **Scan** — Loki error logs, error patterns, Prometheus error rates, latency anomalies, firing alerts
3. **Triage** — classify severity, enrich with trace context + source code, generate Grafana deeplinks, deduplicate against Linear
4. **Report** — structured findings with code context and Grafana links
5. **File** — create Linear issues (only after user approval)

Scoped to **gateway** and **dashboard** services only. Never auto-files bugs without user confirmation.

## Critical Constraints

1. Never claim Phase 1 has KiCad schematic generation (that's Phase 2 write capability)
2. Phase 1 is 6-7 disciplines, not 12 — 1:1 agent-to-discipline ratio
3. Phase 1 timeline is "6 months total" not "3-4 months"
4. MetaForge uses PydanticAI + Temporal for agent orchestration (ADR-001)
5. All tool adapters run in Docker containers
6. The first end-to-end vertical is the Mechanical Agent (MET-8): CAD model -> FEA -> Digital Twin update
