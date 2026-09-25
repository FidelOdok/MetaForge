# Capability Matrix — Phase 1

> **Status:** Phase 1 (v0.1). What MetaForge can do today, what it
> can't yet, and where each capability is exercised end-to-end.
> Last verified against `main` on 2026-06-14.

If you want a feature: search this page first. If it's missing, it's
either Phase 2/3 (see [`roadmap.md`](roadmap.md)) or genuinely not on
the roadmap — file an issue.

## MCP tools (63 across 16 adapters)

The standalone MCP server (`python -m metaforge.mcp --transport stdio`)
loads adapters listed in the `METAFORGE_ADAPTERS` env var. Default is
`knowledge,twin,constraint,cadquery,calculix` (22 tools). FreeCAD, KiCad,
Gazebo, the OpenUSD conversion adapter, and Isaac Sim are opt-in;
`project`, `memory`, and `session` are runtime-injected (registered
when the gateway supplies their backend).

| Adapter | Tool | Purpose | UAT scenario |
|---|---|---|---|
| `knowledge` (default) | `knowledge.ingest` | Index a file or text into the LightRAG-backed KB | [`tier1/ingest.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/ingest.md) |
| `knowledge` | `knowledge.search` | Semantic + fulltext search over indexed sources | [`tier1/retrieval.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/retrieval.md) |
| `knowledge` | `knowledge.extract` | Resolve an MPN → current Datasheet work product | [`tier1/retrieval.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/retrieval.md) |
| `knowledge` | `knowledge.populate_bom` | Enrich a BOM from indexed datasheets. Optional `purchase_unit` narrows candidates to knowledge entries tagged that way at ingest time, degrading to an unfiltered search (flagged via `purchase_unit_filter_degraded`) rather than losing recall against an untagged corpus (MET-436) | _none yet_ |
| `component` (injected) | `component.search_parametric` | Range/comparison query against the typed component catalog (e.g. `category=buck_converter`, `v_out==5`, `efficiency>0.9`) — the parametric index `knowledge.search`'s equality-only filters can't express (MET-436). Each row also carries `image_url`/`footprint`/`cad_model_url` (caller-supplied at index time — no extraction pipeline populates them yet) | live-verified (fidel-dev) |
| `component` | `component.search_intent` | Translate a free-text goal ("step 12V down to 5V for a flight controller") into spec bounds, query the parametric catalog, fall back to `knowledge.populate_bom`'s fuzzy search per category; splits results into `buy_complete` (COTS assemblies) vs. `build_from_parts` (discrete components) — never merged (MET-436) | live-verified (fidel-dev) |
| `twin` (default) | `twin.get_node` | Fetch a Twin node by id with first-hop neighbors | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.thread_for` | Walk the digital thread for a work product. `direction` (outgoing/incoming/both, default outgoing) controls traversal — most traceability edges point child-to-parent, so a requirement/constraint root (usually an edge *target*) needs `incoming` or `both` to see anything connected to it (FORGE-72) | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.find_by_property` | Find nodes matching a property predicate | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.constraint_violations` | List active constraint violations on a project. Pass `project_id` to scope explicitly (FORGE-75, the project brief tells the agent to do this the same way it does for `twin.commit_geometry`) — without it, falls back to the calling session's ambient project context if one is set (FORGE-74, not currently reachable from a real chat turn — see FORGE-75), and otherwise evaluates every constraint across every project (admin path, previously always did this unconditionally, a real cross-project leak) | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.query_cypher` | Run a Cypher query against the Twin (mutating Cypher gated by `--allow-twin-mutations`) | [`tier1/twin-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/twin-hp.md) |
| `twin` | `twin.record_decision` | Record a design decision as a typed DESIGN_DECISION work product (markdown blob + project link) | live-verified (MET-495) |
| `twin` | `twin.record_document` | Persist a markdown document (requirements, notes) as a typed PRD/DOCUMENTATION work product (blob + project link); writes immediately, no approval gate | live-verified (MET-588) |
| `twin` (injected) | `twin.record_component_selection` | Persist one chosen `component.search_parametric`/`search_intent` result as a real BOMItem work product (graph node + project link) — without this, a search result was pure chat output with no reviewable, versioned trace in the Twin (MET-436). Optionally stores `datasheet_url`/`image_url`/`footprint`/`cad_model_url` plus purchase/pricing provenance (`purchase_url`, `price_currency`, `priced_distributor`, and an auto-captured `priced_at` timestamp — a price is a snapshot, never caller-timestamped) on the BOMItem; any of those left unset by the caller are auto-filled from the component catalog store when a match on MPN/manufacturer exists, so a bare selection call still gets media/cost fields when the catalog already knows the part. When project-scoped, also adds a real `CONTAINS` edge from the project's `BOM` work product (created on first use, reused via a `kind` metadata marker so a document-shaped BOM work product is never mistaken for the container) to the new BOMItem — without this a BOMItem had only the Postgres project-junction link and no graph edge, so `TwinAPI.find_orphans()` flagged it as an orphan and it was unreachable via `twin.thread_for` | unit-verified (MET-436) |
| `twin` | `twin.record_constraint_set` | Record structured requirements as evaluable Constraint nodes + a CONSTRAINT_SET work product; expressions compile-checked, violations feed gate criteria (MET-583) | unit-verified (MET-582) |
| `twin` | `twin.record_engineering_entity` | Record one Engineering Intent & Requirements Harness entity (intent/stakeholder_need/objective/assumption/question/risk/verification_case/evidence/budget/invariant/waiver/release_approval), optionally linked to parent(s) via a real graph edge (`derives_from`/`satisfies`/`motivates`/...), resolved by exact name or UUID (FORGE-45/35). A `budget`/`invariant` (metric/unit/system_total or limit in `extra`) is read back automatically by the G3 Preliminary Feasibility gate; a `waiver`/`release_approval` only counts toward the G8 Release gate once approved via `twin.approve_engineering_entity` (FORGE-73) | unit-verified (FORGE-45/73) |
| `twin` | `twin.approve_engineering_entity` | Advance one EngineeringEntity's authority from `proposed` to `reviewed`/`approved` — a real approval step distinct from creation, giving `AuthorityState` (FORGE-51) its first-ever non-baseline write path. A `waiver`/`release_approval` recorded but never approved this way FAILS the G8 Release gate's check (FORGE-73) | unit-verified (FORGE-73) |
| `twin` | `twin.record_evidence` | Persist a tool-generated Evidence entity: the real structured output of a calculix/simulation/CAD/test tool call (producer, inputs, a content hash — never a restated assertion), optionally linked to the requirement(s) it supports/contradicts and revision-pinned `valid_against` dependencies that make FORGE-59's staleness propagation apply to evidence for the first time (FORGE-64/35). `supersedes` records a revalidation rerun and flips the stale evidence it replaces to SUPERSEDED (FORGE-65) | unit-verified (FORGE-64/65) |
| `twin` | `twin.record_claim` | Persist a requirement satisfaction claim: a real graph edge asserting an artefact (CAD model, schematic, ...) satisfies a requirement, citing evidence ids. Status (supported/unsupported) is always computed live from current evidence staleness, never cached — a claim with no evidence, or whose evidence has all gone stale, reports unsupported (FORGE-65/35) | unit-verified (FORGE-65) |
| `twin` | `twin.stage_work_product_file` | Materialize a committed work product's blob onto the shared adapter workspace, returning a file_path any freecad/cadquery/calculix tool can load — recovers a work product for inspection even after its authoring session is gone (MET-618) | unit-verified (MET-618) |
| `twin` | `twin.propose_engineering_change` / `twin.analyze_engineering_change` / `twin.approve_engineering_change` / `twin.reject_engineering_change` / `twin.commit_engineering_change` / `twin.mark_engineering_change_rolled_back` | The Engineering Change Transaction lifecycle (PROPOSED → ANALYZING → READY_FOR_REVIEW → APPROVED/REJECTED → COMMITTED/ROLLED_BACK, spec section 13) as MCP tools — real since FORGE-66/67 but unreachable from any agent until now. `analyze` computes real transitive impact (ImpactEngine) and approval level (HITLEngine); `approve`'s `approver` is an agent-asserted identity string (same trust level as every `created_by` field in this API) but the independence check is real — raises if `approver` equals the patch's own `created_by`. `commit` actually applies the patch via `TransactionEngine`; on a conflict the ECT stays `APPROVED` with the conflict recorded, never silently marked committed. `mark_rolled_back` is a bookkeeping label only — no automatic undo capability exists anywhere in this system (FORGE-70/35) | unit-verified (FORGE-70) |
| `run` | `run.start_design_flow` | Start a gated design lifecycle (hardware_v1 / mech_v1 / design_v1) for a goal, project-scoped; every phase gate still requires human approval | unit-verified (MET-587) |
| `run` | `run.get_status` | Check a design-flow run's status / gate reason / result | unit-verified (MET-587) |
| `constraint` (default) | `constraint.validate` | Pre-flight validate proposed graph changes | [`tier1/constraint-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/constraint-hp.md) |
| `cadquery` (default) | `cadquery.create_parametric` | Generate a parametric solid (box, cylinder, …) → STEP | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.boolean_operation` | Union / cut / intersect two solids | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.get_properties` | Mass / volume / bounding-box for a STEP file | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.export_geometry` | Convert STEP → GLB (web viewer) or STL | [`tier1/cad-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/cad-hp.md) |
| `cadquery` | `cadquery.export_urdf` | Export a single-link URDF (robot description) — companion mesh + a real `<inertial>` block (mass/center-of-mass/inertia tensor) from the part's geometry and a material density lookup. Tier-1 only: one link, no joints — a multi-body assembly with real kinematic joints needs the FreeCAD assembly-joint tools mapped to URDF's `<joint>` schema, separate follow-on work. `xacro=true` writes a `.xacro`-extension file with the xacro namespace declared on the root element (no macro directives generated — includable into a larger macro-based description) (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_urdf_assembly` | Export a multi-link URDF with real kinematic joints from a set of STEP parts + a joint list (same shape FreeCAD's `add_assembly_joint`/`list_joints` produce). Maps `fixed`→`fixed`, `slider`→`prismatic` (requires explicit `limits`), `revolute`→`continuous` (no fabricated rotation limit); `cylindrical`/`ball` joints are rejected — no single-joint URDF equivalent. Same `xacro=true` support as `cadquery.export_urdf` (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_sdf` | Export a single-link SDFormat model (Gazebo) — same mass-property/mesh pattern as `export_urdf`, schema grounded directly against `gazebosim/sdformat`'s spec files. `world_name` wraps the model in a `<world>` (`.world` file); otherwise a standalone `.sdf` model. Tier-1 only: one model, one link, no `<joint>` (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_sdf_assembly` | Export a multi-link SDFormat model (Gazebo) with real joints from a set of STEP parts + a joint list (same shape FreeCAD's `add_assembly_joint`/`list_joints` produce). SDF's own `<joint>` schema is more permissive than URDF's: maps `fixed`→`fixed`, `slider`→`prismatic` (requires explicit `limits`), `revolute`→`continuous` (no fabricated rotation limit), and `ball`→`ball` (SDF supports it natively — URDF has no single-joint equivalent); `cylindrical` joints are rejected — no direct SDF `<joint>` equivalent (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_usd` | Export a plain-text `.usda` (USD) file — hand-authored (no `usd-core`/`pxr` dependency), mesh geometry as `UsdGeomMesh` point/face arrays (via an STL round-trip) plus real `PhysicsRigidBodyAPI`/`PhysicsCollisionAPI`/`PhysicsMassAPI` properties, schema grounded against `PixarAnimationStudios/OpenUSD`'s spec. Tier-1 only: axis-aligned parts (negligible off-diagonal inertia) — a rotated/asymmetric part raises an explicit error rather than emitting wrong principal-axis physics data (MET-706) | unit-verified |
| `cadquery` | `cadquery.export_usd_assembly` | Export a multi-body `.usda` (USD) file with real UsdPhysics joints from a set of STEP parts + a joint list (same shape FreeCAD's `add_assembly_joint`/`list_joints` produce). Maps `fixed`→`PhysicsFixedJoint`, `slider`→`PhysicsPrismaticJoint` (requires explicit `limits`), `revolute`→`PhysicsRevoluteJoint` (no fabricated rotation limit), `ball`→`PhysicsSphericalJoint` (USD supports it natively, like SDF); `cylindrical` joints are rejected — no matching UsdPhysics joint type. Since `physics:axis` is a canonical X/Y/Z token (not a free vector), an arbitrary joint axis is expressed via a computed alignment quaternion on the joint frame. Still axis-aligned-inertia-only per part, same tier-1 restriction as `cadquery.export_usd` (MET-706) | unit-verified |
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
| `gazebo` (opt-in) | `gazebo.run_simulation` | Headless physics/dynamics simulation of an SDF/world file | _none yet_ |
| `gazebo` | `gazebo.validate_world` | Validate an SDF/world file's basic structure before simulating | _none yet_ |
| `gazebo` | `gazebo.extract_results` | Parse an existing Gazebo stats JSON file | _none yet_ |
| `omniverse_usd` (opt-in) | `omniverse_usd.convert_glb_to_usd` | Convert a GLB (from cadquery/freecad/occt-converter) into an OpenUSD stage, preserving part names and transforms | _none yet_ |
| `omniverse_usd` | `omniverse_usd.validate_usd_minimum` | Cheap structural viability gate on a USD stage (default prim, mesh count, metersPerUnit) before downstream simulation dispatch | _none yet_ |
| `omniverse_usd` | `omniverse_usd.describe_stage` | Basic structural info about a USD stage (up axis, scale, prim paths, mesh count) | _none yet_ |
| `isaac_sim` (opt-in) | `isaac_sim.run_physics` | Dispatch a PhysX physics job to the `nvcr.io/nvidia/isaac-sim` container via ephemeral GPU compute; caller supplies the container command, requires `accept_eula=true` | _none yet_ |
| `isaac_sim` | `isaac_sim.render_scene` | Dispatch an RTX render job to the same container | _none yet_ |
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
| `offer_resolver` (injected, no required backend) | `distributors.resolve_offers` | Fan out to whichever of Digi-Key/Mouser/Nexar are configured, per MPN — quantity-aware price-tier selection, MOQ-forced overbuy, stock sufficiency (never just `stock>0`), deadline-vs-cost ranking. Registers even with zero distributor credentials; "no offers found" is a normal result, not an error. There is no FX-rate source in this layer, so offers spanning more than one currency are flagged via `currency_mismatch` rather than compared as if same-currency — a caller must check each offer's own `currency` before trusting cross-offer cost ranking (MET-436) | live-verified (fidel-dev) |
| `web` (requires `BRAVE_API_KEY`) | `web.search` | Ranked public-web results (title/url/snippet) via the Brave Search API — current datasheets, standards, vendor docs, errata. Not registered at all when the key is unset, so an agent never sees a tool that cannot work. A rate-limit or API failure **raises** rather than returning an empty list, so the agent can't report "no results found" for a query that was never answered; 429s are retried with backoff first (MET-7) | live-verified |
| `web` | `web.fetch` | Fetch one public http(s) URL as readable text — **HTML and PDF** (datasheets, app notes, reference manuals). SSRF-guarded: private/loopback/link-local hosts refused on the initial URL **and every redirect hop**; http/https + ports 80/443 only. HTML capped at 2 MB; a PDF is read up to `max_pages` (default 30) and says so in-band when later pages were skipped, is never byte-truncated (that corrupts the xref table), and an oversize/scanned/encrypted PDF is refused with the reason. Content is returned inside an explicit untrusted-data fence. Pair with `knowledge.ingest` (`source_path=<url>`) to keep a source (MET-7) | live-verified |

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
| `cad_generation` | `create_parametric` | `create_parametric` | `shape_type` ∈ {bracket, plate, enclosure, cylinder}, `parameters`, `material`, `output_path` → `cad_file`, `volume_mm3`, `surface_area_mm2`, `bounding_box`, `parameters_used` | Both backends add `mass_kg` (volume × `materials.py` density) when `material` is given; omitted, never defaulted to 0, when it isn't (FORGE-100) |
| `cad_operations` | `boolean_operation` | `boolean_operation` | `input_file_a`, `input_file_b`, `operation` ∈ {union, subtract, intersect}, `output_path` → `output_file`, `result_volume`, `result_area` | — (field-for-field identical) |
| `cad_analysis` | `get_properties` | `get_properties` | `input_file` → `volume`, `area`, `center_of_mass`, `bounding_box` | CadQuery adds `inertia`; FreeCAD's does not return it |
| `cad_export` | `export_geometry` | `export_geometry` | `input_file`, `output_format`, `output_path` → `output_file`, `file_size_bytes`, `format` | CadQuery supports `step/stl/obj/brep/amf/svg`; FreeCAD supports only `step/stl/obj/brep` (no `amf`/`svg`), and errors on non-STEP today. Both include `step_base64` when the export is STEP (MET-489) — pass it to `twin.commit_geometry`'s `step_base64` argument to persist the result to MinIO + a `WorkProduct` node; without that follow-up call the file only exists in the adapter container and is lost on redeploy. |
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
`create_assembly` and `generate_enclosure` also return `volume_mm3` and
(when a `material` is given) `mass_kg`, computed the same way as
`create_parametric` above — for `create_assembly` this is a first-order
estimate (summed part volume × one material's density), not per-part-accurate,
since `AssemblyPart` doesn't carry a per-part material (FORGE-100).

**Measured properties reach the Twin, not just the tool response**: the three
mechanical skills that commit CAD geometry (`generate_cad`,
`generate_enclosure`, `create_assembly`) pass whichever of `volume_mm3`,
`surface_area_mm2`, `mass_kg`, and `bounding_box` (as `bbox_mm`) the tool
actually returned to `commit_geometry`'s `extra_metadata` argument
(`domain_agents/shared/commit_geometry.py`'s `measured_metadata_from_cad_result`),
which flattens them onto the committed work product's top-level `metadata`.
This is what lets a constraint expression like
`wp.metadata.get('mass_kg', 0) <= 4.5` read a real measured value instead of
always the vacuous-pass default (FORGE-100).

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

## Compute providers (MET-564)

`tool_registry.container_runtime.ContainerRuntime` is the abstraction
tool execution runs against — `DockerRuntime` (local Docker) is the
default. `tool_registry.compute_providers.resolve_runtime()` selects a
runtime by provider id (env `METAFORGE_COMPUTE_PROVIDER`, default
`docker`), so simulation-heavy work (CalculiX FEA, meshing) can burst
onto ephemeral cloud compute instead of only local Docker.

| Provider id | Backend | Credential | Notes |
|---|---|---|---|
| `docker` (default) | Local Docker daemon | — | No change from prior behavior |
| `runpod` | RunPod Serverless | `RUNPOD_API_KEY` | `ContainerConfig.image` is the RunPod *endpoint id*, not a Docker tag — a Serverless endpoint is pre-built around one worker image |
| `vast_ai` (aliases `vastai`, `vast.ai`) | Vast.ai instance rental | `VAST_API_KEY` | Best-effort success signal — Vast.ai's REST API doesn't expose a process exit code, so `success` is inferred from instance lifecycle state |

Neither remote runtime supports `ContainerConfig.volumes` (no host
filesystem to bind-mount into a remote provider) — `run()` raises
`RemoteVolumesUnsupportedError` if volumes are set. Real blob-store-backed
input/output is tracked separately (MET-489). This first slice is also
not yet wired into `bootstrap.py`'s on-demand adapter spin-up — see
MET-564 for what's deliberately deferred (AWS Batch / GCP / Azure /
CoreWeave / Lambda Cloud / Paperspace adapters, a GPU field on
`ContainerConfig`).

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
