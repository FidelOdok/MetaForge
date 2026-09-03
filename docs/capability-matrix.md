# Capability Matrix — Phase 1

> **Status:** Phase 1 (v0.1). What MetaForge can do today, what it
> can't yet, and where each capability is exercised end-to-end.
> Last verified against `main` on 2026-06-14.

If you want a feature: search this page first. If it's missing, it's
either Phase 2/3 (see [`roadmap.md`](roadmap.md)) or genuinely not on
the roadmap — file an issue.

## MCP tools (52 across 13 adapters)

The standalone MCP server (`python -m metaforge.mcp --transport stdio`)
loads adapters listed in the `METAFORGE_ADAPTERS` env var. Default is
`knowledge,twin,constraint,cadquery,calculix` (19 tools). FreeCAD and
KiCad are opt-in; `project`, `memory`, and `session` are runtime-injected
(registered when the gateway supplies their backend).

| Adapter | Tool | Purpose | UAT scenario |
|---|---|---|---|
| `knowledge` (default) | `knowledge.ingest` | Index a file or text into the LightRAG-backed KB | [`tier1/ingest.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/ingest.md) |
| `knowledge` | `knowledge.search` | Semantic + fulltext search over indexed sources | [`tier1/retrieval.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/retrieval.md) |
| `knowledge` | `knowledge.extract` | Resolve an MPN → current Datasheet work product | [`tier1/retrieval.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/retrieval.md) |
| `knowledge` | `knowledge.populate_bom` | Enrich a BOM from indexed datasheets | _none yet_ |
| `component` (injected) | `component.search_parametric` | Range/comparison query against the typed component catalog (e.g. `category=buck_converter`, `v_out==5`, `efficiency>0.9`) — the parametric index `knowledge.search`'s equality-only filters can't express (MET-436) | live-verified (fidel-dev) |
| `component` | `component.search_intent` | Translate a free-text goal ("step 12V down to 5V for a flight controller") into spec bounds, query the parametric catalog, fall back to `knowledge.populate_bom`'s fuzzy search per category; splits results into `buy_complete` (COTS assemblies) vs. `build_from_parts` (discrete components) — never merged (MET-436) | live-verified (fidel-dev) |
| `twin` (default) | `twin.get_node` | Fetch a Twin node by id with first-hop neighbors | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.thread_for` | Walk the digital thread for a work product | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.find_by_property` | Find nodes matching a property predicate | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.constraint_violations` | List active constraint violations on a project | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.query_cypher` | Run a Cypher query against the Twin (mutating Cypher gated by `--allow-twin-mutations`) | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.record_decision` | Record a design decision as a typed DESIGN_DECISION work product (markdown blob + project link) | live-verified (MET-495) |
| `twin` | `twin.record_document` | Persist a markdown document (requirements, notes) as a typed PRD/DOCUMENTATION work product (blob + project link); writes immediately, no approval gate | live-verified (MET-588) |
| `twin` | `twin.record_constraint_set` | Record structured requirements as evaluable Constraint nodes + a CONSTRAINT_SET work product; expressions compile-checked, violations feed gate criteria (MET-583) | unit-verified (MET-582) |
| `twin` | `twin.stage_work_product_file` | Materialize a committed work product's blob onto the shared adapter workspace, returning a file_path any freecad/cadquery/calculix tool can load — recovers a work product for inspection even after its authoring session is gone (MET-618) | unit-verified (MET-618) |
| `run` | `run.start_design_flow` | Start a gated design lifecycle (hardware_v1 / mech_v1 / design_v1) for a goal, project-scoped; every phase gate still requires human approval | unit-verified (MET-587) |
| `run` | `run.get_status` | Check a design-flow run's status / gate reason / result | unit-verified (MET-587) |
| `constraint` (default) | `constraint.validate` | Pre-flight validate proposed graph changes | [`tier1/constraint-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/constraint-hp.md) |
| `cadquery` (default) | `cadquery.create_parametric` | Generate a parametric solid (box, cylinder, …) → STEP | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.boolean_operation` | Union / cut / intersect two solids | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.get_properties` | Mass / volume / bounding-box for a STEP file | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.export_geometry` | Convert STEP → GLB (web viewer) or STL | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.export_urdf` | Export a single-link URDF (robot description) — companion mesh + a real `<inertial>` block (mass/center-of-mass/inertia tensor) from the part's geometry and a material density lookup. Tier-1 only: one link, no joints — a multi-body assembly with real kinematic joints needs the FreeCAD assembly-joint tools mapped to URDF's `<joint>` schema, separate follow-on work (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_sdf` | Export a single-link SDFormat model (Gazebo) — same mass-property/mesh pattern as `export_urdf`, schema grounded directly against `gazebosim/sdformat`'s spec files. `world_name` wraps the model in a `<world>` (`.world` file); otherwise a standalone `.sdf` model. Tier-1 only: one model, one link, no `<joint>` (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_usd` | Export a plain-text `.usda` (USD) file — hand-authored (no `usd-core`/`pxr` dependency), mesh geometry as `UsdGeomMesh` point/face arrays (via an STL round-trip) plus real `PhysicsRigidBodyAPI`/`PhysicsCollisionAPI`/`PhysicsMassAPI` properties, schema grounded against `PixarAnimationStudios/OpenUSD`'s spec. Tier-1 only: axis-aligned parts (negligible off-diagonal inertia) — a rotated/asymmetric part raises an explicit error rather than emitting wrong principal-axis physics data (MET-706) | unit-verified |
| `cadquery` | `cadquery.generate_ros2_launch` | Generate a standalone ROS 2 launch file (`robot_state_publisher` + optional `joint_state_publisher`(`_gui`) + `rviz2`) for an exported URDF, grounded directly against `ros/urdf_launch`'s real launch files. Pure text generation — no STEP file or CadQuery geometry involved (MET-706) | unit-verified |
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
| `freecad` | `freecad.describe_step_file` | Per-component breakdown of a multipart assembly file (Label/volume/area/bbox per named part, not just the flattened aggregate) (MET-629) | unit-verified (MET-629) |
| `freecad` | `freecad.export_geometry` | FreeCAD STEP / STL / IGES export | _none yet_ |
| `freecad` | `freecad.generate_mesh` | FreeCAD-driven mesh generation | _none yet_ |
| `kicad` (opt-in) | `kicad.run_erc` | Electrical rules check | _none yet_ |
| `kicad` | `kicad.run_drc` | Design rules check | _none yet_ |
| `kicad` | `kicad.export_bom` | Bill of materials export | _none yet_ |
| `kicad` | `kicad.export_netlist` | Netlist export | _none yet_ |
| `kicad` | `kicad.export_gerber` | Gerber set for fab | _none yet_ |
| `kicad` | `kicad.get_pin_mapping` | Connector pinmap → JSON | _none yet_ |
| `project` (injected) | `project.create` | Create a project (rejects a case-insensitive duplicate name) | [`tier1/project.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/project.md) |
| `project` | `project.list` | List projects the caller can see | [`tier1/project.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/project.md) |
| `project` | `project.get` | Fetch a project by id or name | [`tier1/project.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/project.md) |
| `project` | `project.update` | Rename, redescribe, or change status on an existing project | _none yet_ |
| `project` | `project.delete` | Permanently delete a project by id | _none yet_ |
| `memory` (injected) | `memory.retrieve_similar_experience` | Semantic recall of past agent experiences | _none yet_ |
| `memory` | `memory.list_insights` | List consolidated memory insights | _none yet_ |
| `session` (injected) | `session.start` | Open an agent session to record narrative (MET-494) | live-verified |
| `session` | `session.log_event` | Append a thought / action / decision / … to a session | live-verified |
| `session` | `session.complete` | Close a session with terminal status + summary | live-verified |
| `offer_resolver` (injected, no required backend) | `distributors.resolve_offers` | Fan out to whichever of Digi-Key/Mouser/Nexar are configured, per MPN — quantity-aware price-tier selection, MOQ-forced overbuy, stock sufficiency (never just `stock>0`), deadline-vs-cost ranking. Registers even with zero distributor credentials; "no offers found" is a normal result, not an error (MET-436) | live-verified (fidel-dev) |

Session capture also runs **server-side** (every tool call → an `action`
event, MET-496) — see [`session-capture.md`](session-capture.md) for the full
three-layer model and install.

**MCP resources** (read-only, addressable):

- `metaforge://knowledge/sources` — list of ingested sources.
- `metaforge://knowledge/sources/{id}` — one source with chunks.

See [`integrations/claude-code.md`](integrations/claude-code.md) for
how to drive these from Claude Code.

### Chat-harness-native tools (not via the standalone MCP server)

Registered directly into a chat turn's `ToolRegistry` (`orchestrator/harness/tools.py`)
rather than through an MCP adapter — only reachable from *within* a running
chat turn (`forge chat`, the TUI workspace, the dashboard chat), never from an
external MCP client like Claude Code.

| Tool | Purpose | UAT scenario |
|---|---|---|
| `chat.set_project_scope` | Rescope the CURRENT chat thread to a project (id/name/substring, or `none` to leave) — in place, preserving the conversation (MET-580) | see [`cli-reference.md`](cli-reference.md#project-scoped-chat) |

### CAD kernel capability contract

The `cadquery` and `freecad` adapters both implement 5 shared capability
tags. Mechanical skills (`generate_cad`, `generate_cad_script`) resolve a
backend at runtime via `mcp.list_tools(capability=...)`
(`domain_agents/shared/cad_backend.py`'s `resolve_cad_backend`) rather than
hardcoding a tool id per backend — a new adapter becomes a valid candidate
for every skill that calls this helper the moment it registers a tool under
one of these tags, no skill code changes required.

| Capability | `cadquery` tool | `freecad` tool | Guaranteed fields (both backends) | Backend-only extras |
|---|---|---|---|---|
| `cad_generation` | `create_parametric` | `create_parametric` | `shape_type` ∈ {bracket, plate, enclosure, cylinder}, `parameters`, `material`, `output_path` → `cad_file`, `volume_mm3`, `surface_area_mm2`, `bounding_box`, `parameters_used` | — (field-for-field identical) |
| `cad_operations` | `boolean_operation` | `boolean_operation` | `input_file_a`, `input_file_b`, `operation` ∈ {union, subtract, intersect}, `output_path` → `output_file`, `result_volume`, `result_area` | — (field-for-field identical) |
| `cad_analysis` | `get_properties` | `get_properties` | `input_file` → `volume`, `area`, `center_of_mass`, `bounding_box` | CadQuery adds `inertia`; FreeCAD's does not return it |
| `cad_export` | `export_geometry` | `export_geometry` | `input_file`, `output_format`, `output_path` → `output_file`, `file_size_bytes`, `format` | CadQuery supports `step/stl/obj/brep/amf/svg`; FreeCAD supports only `step/stl/obj/brep` (no `amf`/`svg`) |
| `cad_scripting` | `execute_script` | `execute_code` | Script assigns its result to a variable named `result` | Different call shape: CadQuery is stateless (`{script, output_path}` → one file); FreeCAD is session-based (`{session_id, code}` against a live document → `obj_id`, needs a separate `open_session`/`measure`/`export_model`/`close_session` sequence, see `generate_cad_script`'s `_run_freecad_code`) |

**A verification step (or anything else consuming this contract) should rely
on the guaranteed-fields column above, not assume full identity** — e.g. a
measurement check shouldn't require `inertia` unless it knows it's routed to
CadQuery specifically.

**Adding a new kernel**: register its tools under these same 5 capability
tags with input/output shapes matching the guaranteed-fields column, and
`resolve_cad_backend` (plus every skill that calls it) picks it up
automatically. Capabilities outside this table (FreeCAD's `PartDesign`/
`Sketcher` feature-tree tools, assembly joints, `generate_gear`/
`fastener_hole`/`generate_ic_package`/`lattice_perforation`; CadQuery's
`create_assembly`/`generate_enclosure`) are intentionally kernel-specific and
not part of this uniform contract — FreeCAD's session/feature-tree model is a
categorically different, stateful capability CadQuery cannot replicate.

## Dashboard routes (11)

Served by Vite under `dashboard/` — boot with
`docker compose up gateway dashboard` and open `localhost:5173`.

| Path | Purpose | Backed by |
|---|---|---|
| `/projects` | Project list + create / update / delete | `GET/POST/PATCH/DELETE /v1/projects` |
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
