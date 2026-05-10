# Capability Matrix — Phase 1

> **Status:** Phase 1 (v0.1). What MetaForge can do today, what it
> can't yet, and where each capability is exercised end-to-end.
> Last verified against `main` on 2026-05-10.

If you want a feature: search this page first. If it's missing, it's
either Phase 2/3 (see [`roadmap.md`](roadmap.md)) or genuinely not on
the roadmap — file an issue.

## MCP tools (30 across 7 adapters)

The standalone MCP server (`python -m metaforge.mcp --transport stdio`)
loads adapters listed in the `METAFORGE_ADAPTERS` env var. Default is
`knowledge,twin,constraint,cadquery,calculix` (19 tools). FreeCAD and
KiCad adapters are opt-in.

| Adapter | Tool | Purpose | UAT scenario |
|---|---|---|---|
| `knowledge` (default) | `knowledge.ingest` | Index a file or text into the LightRAG-backed KB | [`tier1/ingest.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/ingest.md) |
| `knowledge` | `knowledge.search` | Semantic + fulltext search over indexed sources | [`tier1/retrieval.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/retrieval.md) |
| `twin` (default) | `twin.get_node` | Fetch a Twin node by id with first-hop neighbors | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.thread_for` | Walk the digital thread for a work product | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.find_by_property` | Find nodes matching a property predicate | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.constraint_violations` | List active constraint violations on a project | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.query_cypher` | Run a read-only Cypher query against the Twin | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `constraint` (default) | `constraint.validate` | Pre-flight validate proposed graph changes | [`tier1/constraint-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/constraint-hp.md) |
| `cadquery` (default) | `cadquery.create_parametric` | Generate a parametric solid (box, cylinder, …) → STEP | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.boolean_operation` | Union / cut / intersect two solids | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.get_properties` | Mass / volume / bounding-box for a STEP file | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.export_geometry` | Convert STEP → GLB (web viewer) or STL | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.execute_script` | Run an inline CadQuery Python script | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.create_assembly` | Multi-body assembly (Phase 2 — manifest only) | _Phase 2_ |
| `cadquery` | `cadquery.generate_enclosure` | Parametric enclosure generator (Phase 2 — manifest only) | _Phase 2_ |
| `calculix` (default) | `calculix.run_fea` | Linear-static FEA on a meshed solid | [`tier1/fea-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/fea-hp.md) |
| `calculix` | `calculix.run_thermal` | Steady-state thermal analysis | [`tier1/fea-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/fea-hp.md) |
| `calculix` | `calculix.validate_mesh` | Mesh quality and connectivity checks | [`tier1/fea-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/fea-hp.md) |
| `calculix` | `calculix.extract_results` | Pull max-stress / max-displacement from `.frd` | [`tier1/fea-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/fea-hp.md) |
| `freecad` (opt-in) | `freecad.create_parametric` | FreeCAD-driven parametric solid | _none yet_ |
| `freecad` | `freecad.boolean_operation` | FreeCAD boolean ops | _none yet_ |
| `freecad` | `freecad.get_properties` | FreeCAD shape properties | _none yet_ |
| `freecad` | `freecad.export_geometry` | FreeCAD STEP / STL / IGES export | _none yet_ |
| `freecad` | `freecad.generate_mesh` | FreeCAD-driven mesh generation | _none yet_ |
| `kicad` (opt-in) | `kicad.run_erc` | Electrical rules check | _none yet_ |
| `kicad` | `kicad.run_drc` | Design rules check | _none yet_ |
| `kicad` | `kicad.export_bom` | Bill of materials export | _none yet_ |
| `kicad` | `kicad.export_netlist` | Netlist export | _none yet_ |
| `kicad` | `kicad.export_gerber` | Gerber set for fab | _none yet_ |
| `kicad` | `kicad.get_pin_mapping` | Connector pinmap → JSON | _none yet_ |

**MCP resources** (read-only, addressable):

- `metaforge://knowledge/sources` — list of ingested sources.
- `metaforge://knowledge/sources/{id}` — one source with chunks.

See [`integrations/claude-code.md`](integrations/claude-code.md) for
how to drive these from Claude Code.

## Dashboard routes (11)

Served by Vite under `dashboard/` — boot with
`docker compose up gateway dashboard` and open `localhost:5173`.

| Path | Purpose | Backed by |
|---|---|---|
| `/projects` | Project list + create / delete | `GET/POST /v1/projects` |
| `/projects/:id` | Project detail, work-product tree | `GET /v1/projects/{id}` |
| `/sessions` | Workflow run list | `GET /v1/sessions` |
| `/sessions/:id` | Session detail, agent messages | `GET /v1/sessions/{id}` |
| `/approvals` | Pending change-proposal review | gateway approvals API |
| `/bom` | BOM viewer | `GET /v1/bom/...` |
| `/twin` | 3D viewer (R3F / Three.js) for STEP/GLB | `GET /v1/twin/files/...` |
| `/files` | Legacy file browser | gateway files API |
| `/knowledge` | Ingested-sources table (sortable, filterable) | `GET /api/v1/knowledge/sources` |
| `/knowledge/sources/:id` | Per-source drill-in (placeholder in v1) | _stub_ |
| `/assistant` | Chat panel (gateway → orchestrator) | `POST /v1/chat` |

## CLI commands (8)

Invoke via `python -m cli.forge_cli <cmd>`. Full per-command reference
in [`cli-reference.md`](cli-reference.md).

| Command | Purpose |
|---|---|
| `run` | Invoke a skill via the gateway |
| `status` | Show session / agent status |
| `twin query` | Look up a single Twin node |
| `twin list` | List Twin work products with filters |
| `proposals` | List pending change proposals |
| `approve` / `reject` | Act on a change proposal |
| `ingest` | Ingest a file into the knowledge base |
| `sources` | List / show ingested knowledge sources |

## What works without optional extras

A bare `pip install -e .` (no extras) gets you:

- The MCP server, with `cadquery` / `freecad` / `kicad` adapters
  silently dropped (their Python deps aren't installed).
- The CLI, talking to a gateway.
- The dashboard (TypeScript build, no Python extras needed).

You'll want at least `pip install -e ".[dev,knowledge,cadquery]"` to
get a useful loadout.

## Phase-1 limits

What's deliberately not in scope this phase — see
[`roadmap.md`](roadmap.md) for when each unlocks.

| Limitation | Why | When |
|---|---|---|
| KiCad adapter is **read-only** (ERC, DRC, BOM, Gerber) — no schematic generation, no auto-routing | KiCad write requires a stable round-trip we don't have yet | Phase 2 |
| **No multi-user collaboration** — single-user local only | No auth + presence layer; not a Phase-1 goal | Phase 3 |
| **In-memory Twin fallback** when Neo4j is unreachable — versioning + Cypher are limited | Graceful degradation for laptops without Docker | Always; full mode requires Neo4j |
| **6–7 specialist agents**, not all 25 disciplines | 1:1 agent-to-discipline; covers electronics-heavy products (IoT, drones, embedded) | Phase 2 → 19 agents; Phase 3 → 25 |
| **No production PDF ingest path** — text fixtures only for now | Server-side parser (MET-399) is in flight | Tracked under MET-399 |
| **No streaming progress for long-running tools** in the CLI yet | Streaming notifications work over MCP; CLI wrapper is a follow-up | Tracked separately |

## Where each capability is proven

Every capability above is exercised by at least one Cycle-3 UAT
scenario in [`tests/uat/scenarios/`](https://github.com/FidelOdok/MetaForge/tree/main/tests/uat/scenarios).
The full master plan and verdict tracker is at
[`docs/uat/kb-test-plan.md`](https://github.com/FidelOdok/MetaForge/blob/main/docs/uat/kb-test-plan.md)
in the repo (kept out of the published site as QA-internal).
