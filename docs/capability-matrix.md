# Capability Matrix — Phase 1

> **Status:** Phase 1 (v0.1). What MetaForge can do today, what it
> can't yet, and where each capability is exercised end-to-end.
> Last verified against `main` on 2026-06-14.

If you want a feature: search this page first. If it's missing, it's
either Phase 2/3 (see [`roadmap.md`](roadmap.md)) or genuinely not on
the roadmap — file an issue.

## MCP tools (101 across 17 adapters)

The standalone MCP server (`python -m metaforge.mcp --transport stdio`)
loads adapters listed in the `METAFORGE_ADAPTERS` env var. Default is
`knowledge,twin,constraint,cadquery,calculix` (24 tools). FreeCAD, KiCad,
Gazebo, the OpenUSD conversion adapter, and Isaac Sim are opt-in;
`project`, `memory`, and `session` are runtime-injected (registered
when the gateway supplies their backend).

Ninety-four of the 101 are described in the table below. The seven that are
not yet — `cadquery.validate_physics_stability`, `twin.propose_change`
and the five `twin.commit_*` document tools
(`compliance_checklist`, `design_sketch`, `hazard_analysis`,
`procurement_record`, `technical_drawing`) — are registered and callable;
they simply have no row here yet. Every one of the 101 does carry an MCP
annotation (see below), because that set is checked against the registry
by a test rather than maintained by hand.

### Tool profiles

All 100 tools are served by default. Every MCP client caps how many tools
it will carry, though, and the ones that do not error simply drop the
overflow — which leaves the model behaving as though the missing
capability does not exist.

`--profile` serves a named subset instead:

| Profile | Tools | For |
|---|---|---|
| `core` | 30 | Projects, sessions, twin reads and the records that are not domain-specific |
| `mechanical` | 29 | CAD authoring across both kernels, plus geometry commit |
| `simulation` | 29 | FEA, load cases, meshes and the evidence they produce |
| `electronics` | 25 | Schematic and board checks, component search, sourcing |
| `robotics` | 30 | Assemblies, URDF/SDF/USD export, simulators |

```bash
python -m metaforge.mcp --transport http --profile mechanical
```

The sets live in `mcp_core/profiles.py` and are held between 20 and 40
tools by a test rather than trimmed at runtime — a profile that outgrows
its ceiling is a decision about what to drop, and silently dropping it is
the failure profiles exist to prevent. An unknown profile name stops the
server rather than falling back to serving everything.

### "No data" is not a pass

A cross-domain check reports one of three things, not two:

| Status | Meaning |
|---|---|
| `pass` | The check ran and the design satisfies it |
| `fail` | The check ran and the design does not |
| `no_data` | The check could not run — the thing it inspects is not recorded |

`CrossDomainCheck.passed` is true only for `pass`, so a gate reading it
treats `no_data` as unsatisfied rather than as success. Whether missing
data should block is the gate's decision; it cannot make that decision if
the check has already claimed to pass.

This replaced nine checks that returned `passed=True` whenever the thing
they inspected was absent (FORGE-361). A project with no work products,
no mounting holes, no thermal zones, no connectors, no weight budget and
no power figures scored a clean cross-domain sweep — the emptiest project
looked like the best one.

Two of them computed a verdict from absence rather than skipping: a PCB
with no recorded dimensions became 0×0mm and fitted inside any enclosure,
and components with no recorded weight contributed nothing to the budget
they were being checked against. A `no_data` result now names the fields
it is missing, because "no data" is only useful if it says which.

### Asking the thread a question

Two tools name the questions engineers actually ask:

| Tool | Question |
|---|---|
| `twin.what_verifies` | What evidence, tests or claims support this requirement? |
| `twin.where_used` | Which assemblies or designs depend on this part? |

`twin.thread_for` can answer both — but only if you know which way to walk.
Traceability edges point child-to-parent (evidence *satisfies* a
requirement), so a requirement is usually an edge **target**, and
`thread_for`'s default `direction="outgoing"` returns nothing for it. An
empty subgraph reads as *"nothing verifies this requirement"* when the
traversal simply went the wrong way.

These two encode the direction, because it is a property of the question
rather than something the asker should have to know. Each restricts to its
own edge types, so "what verifies this" does not also return everything
that merely contains it.

An empty answer says so explicitly — `verified: false`, `related_count: 0`
and a note naming the edges walked and the direction — so it reads as
"nothing is recorded" rather than "the tool did not work". `thread_for`
stays available for an arbitrary walk.

### MCP prompts

Six curated workflows are served through `prompts/list` and `prompts/get`:

| Prompt | What it does |
|---|---|
| `use` | Pick the project for this session and summarise where it stands |
| `status` | What is built, and which requirements are unverified |
| `design` | Design or revise a part, committed to the twin |
| `fea` | Run a load case and record the evidence against the revision it came from |
| `gate` | Review whether the project can be promoted |
| `doctor` | Check the connection, and what is unreachable |

They are defined once, in `mcp_core/workflows.py`, and served twice: as MCP
prompts to any spec-compliant client, and as slash commands in the
generated Claude Code package. Two definitions would mean two harnesses
getting different instructions for the same task, and the one that drifts
being whichever nobody is currently testing.

An unknown prompt name lists the ones that exist, rather than rejecting
bare — the caller cannot otherwise tell a typo from a server with no
prompts at all.

### MCP resources

The server advertises `resources` on `initialize` and routes
`resources/list` and `resources/read`. Resources use the `metaforge://`
scheme:

```
metaforge://<adapter>/<kind>/<id>
```

A read routes on the adapter segment the URI already carries, rather than
asking every adapter and taking the first match — that would make the
answer depend on the order adapters happen to be constructed in, which no
caller can see or control.

`resources/list` follows the same rule as `tools/list`: an adapter that
cannot answer is reported under `_meta.unavailableAdapters`, never quietly
dropped. A short resource list reads to a model as "that context does not
exist".

Adapters have been able to register resources since MET-384 and the
knowledge adapter already did — but until FORGE-355 the unified server
dispatched neither method and advertised no capability, so a
spec-compliant client got "Unknown method" and no way to learn they
existed.

#### Project resources

| URI | What it answers |
|---|---|
| `metaforge://twin/brief/{project_id}` | What this project is and what has been built in it, newest work first, with recent requirement docs inline |
| `metaforge://twin/hierarchy/{project_id}` | The product breakdown, with mass and cost rolled up the tree |
| `metaforge://twin/requirements/{project_id}` | Requirements against the evidence that verifies them, with each row's live status |
| `metaforge://twin/decisions/{project_id}` | Recorded decisions, their rationale and the alternatives considered |
| `metaforge://twin/risks/{project_id}` | Recorded risk entities |

The brief is the **same** one the chat harness gives its agent — one
builder, two callers — so an MCP client and a chat agent are briefed
identically and stay that way as it changes.

Each resource renders an existing source rather than computing anything
new. A resource doing its own analysis would be a second opinion the
dashboard and the gates do not share, and the one an agent reads would be
the one nobody validated.

The requirement resource reports `no_data` as itself and states the count
of requirements with no evidence at all. An unverified requirement must
never read as a satisfied one — the same rule the cross-domain checks
follow.

A project that does not exist is an error; a project that exists with
nothing recorded returns a resource saying so. "No such project" and
"nothing built yet" are different answers, and the second is the one an
agent needs at the start of a session.

### Grounding: citing what actually happened

Every `tools/call` response carries a reference in `_meta`:

```json
{
  "content": [{"type": "text", "text": "{\"node\": \"n-1\"}"}],
  "isError": false,
  "_meta": {"callId": "7a619373979b4216"}
}
```

The same id is recorded against the action in the agent session, so a
reply claiming *"I committed the geometry"* can name a call id — and that
id either appears in the session timeline or the claim is unsupported.
Without it, a grounded answer and an invented one read identically: both
are prose, and a reviewer has no way to tell which is which.

Failed calls get a reference too. An agent claiming it *tried* something
is as worth checking as one claiming it succeeded.

The id lives in `_meta`, not inside the text payload — the text is the
tool's own output, and burying a protocol-level id in it would make every
adapter's result schema wrong.

### Approval-held writes

A tool that writes is held for a human when the request comes from a
remote caller, whatever client asked. The gate is on the MCP dispatch
path itself, not in any client — a harness cannot opt out of it by not
implementing it.

Until FORGE-359 this existed only on the chat path: `HarnessRuntime`
paused on `requires_approval`, and the MCP path consulted nothing. The
same `twin.commit_geometry` was gated when a person asked in the
dashboard and ungated when an external harness asked over MCP.

What is held is decided from the same annotations the client is shown
(see above), so a tool cannot advertise `readOnlyHint: true` and be
treated as a write, or the reverse. A tool nobody has classified is
held.

| Caller | Read | Write |
|---|---|---|
| Local stdio | runs | runs (see below) |
| Remote (OAuth identity) | runs | **held** |
| Untrusted (tunnel, no identity) | runs | **held** |

Held calls land in the same queue as chat approvals and appear on the
dashboard's Approvals page, recorded with `caller` and `source: mcp` —
the same write from a remote harness and from the dashboard are not the
same request to whoever is deciding.

Every outcome other than an approval stops the call:

| Outcome | Meaning |
|---|---|
| `not_configured` | The server was told to hold writes and given no gate. Refused, never run. |
| `rejected` | A reviewer said no. |
| `timed_out` | Nobody answered inside the window (180s). |

`rejected` and `timed_out` stay distinct because "a person said no" and
"nobody was looking" need different words to an agent; all three carry
`retryable: false`.

**Local stdio writes are exempt by default.** Not because local is
trusted — the chat path holds local writes today — but because a stdio
session has nowhere to answer: no dashboard is necessarily open and the
MCP client cannot render a prompt until elicitation lands (F2). Holding
there with no way to approve is an outage, not a guardrail. Set
`exempt_local_writes=False` in any deployment where a reviewer is
watching the dashboard.

### When the list is short

`tools/list` never drops an adapter quietly. If an adapter cannot answer
— its container is down, or it returns something that is not JSON — the
tools it would have contributed are missing from the list, and the
response says so in `_meta`:

```json
{
  "tools": [...],
  "_meta": {
    "unavailableAdapters": [
      {"adapter_id": "calculix", "error": "adapter container is down (-32001)"}
    ],
    "profile": {"name": "mechanical", "toolCount": 12, "missing": ["cadquery.create_parametric"]}
  }
}
```

`_meta` is absent when there is nothing to report, so its presence is
itself the signal. `profile.missing` names tools the profile asked for
that no loaded adapter registers, which is a configuration mistake rather
than a smaller profile.

### Health is measured, not asserted

`health/check` on the unified server asks every adapter the same question
and reports what came back. It does not read the registration table.

```json
{
  "service": "metaforge-mcp",
  "status": "degraded",
  "adapter_count": 3,
  "tool_count": 100,
  "unreachable_adapters": ["calculix", "freecad"],
  "detail": "2 of 3 adapters did not answer: calculix, freecad. Their tools are registered but calls to them will fail.",
  "adapters": [
    {"adapter_id": "twin", "version": "0.1.0", "tools_registered": 12, "reachable": true},
    {"adapter_id": "calculix", "version": "0.1.0", "tools_registered": 5, "reachable": false,
     "error": "adapter container is down (-32001)"},
    {"adapter_id": "freecad", "version": "0.1.0", "tools_registered": 41, "reachable": false,
     "error": "no answer within 3s"}
  ]
}
```

`status` is `healthy` only when every adapter answered; otherwise
`degraded`, and `unreachable_adapters` names which. Each adapter gets
three seconds; the probes run concurrently, so the whole check costs one
timeout rather than one per adapter.

The field is `tools_registered`, not `tools_available`. Those numbers come
from the registry and stay the same whether the adapter is up or down —
calling them "available" next to `reachable: false` claimed the opposite
of what the same object said one key later.

Two failure shapes stay distinct in `error`, because they need different
fixes: **"container is down"** means the adapter answered with an error
(start the container), **"no answer within 3s"** means nothing came back
at all (the process is wedged, or the network to it is).

The HTTP status stays **200** even when degraded. The MCP server is up and
answering — it is telling you the truth about something else. A 503 here
would have an orchestrator restart the gateway to cure a sick CAD
container, which does not work and takes the working half down with it.

### What is protecting this connection

`health/check` also reports the auth posture and who is on the other end —
the other three things `/metaforge:doctor` is asked about.

```json
{
  "auth": {
    "mode": "open",
    "transport": "stdio",
    "identifies_caller": false,
    "detail": "no API key and no OAuth configured: every connection on this transport is accepted, and no call is attributable to anyone"
  },
  "client": {
    "connected": true,
    "name": "claude-code",
    "version": "2.1.4",
    "protocol_requested": "2025-06-18",
    "protocol_negotiated": "2024-11-05",
    "protocol_skew": true,
    "detail": "client asked for MCP 2025-06-18; this server pinned 2024-11-05. Anything added after the pinned revision is not available on this connection."
  }
}
```

`auth.mode` is one of `open`, `api_key`, `oauth`, `api_key+oauth` — or
`unknown`, which means the transport never declared one and the server
genuinely cannot say. `unknown` is deliberately not merged into `open`:
a deployment with nothing configured and a reporting gap need to be
distinguishable, and the safe reading of `unknown` is "assume nothing".

`identifies_caller` is true only under OAuth. A shared API key authorises
the call but identifies nobody, which is the same distinction the HTTP
gate already draws by returning an actor for a token and none for a key
match — so a session record from an `api_key` connection is attributable
to the key, not to a person.

`detail` appears only when something is worth saying, which for `auth`
means open mode. That is the state an unset `METAFORGE_MCP_API_KEY` gives
you, so it is the one most likely to be in force without anyone having
chosen it.

`client` is filled from the `initialize` handshake. Before any handshake
it reports `connected: false` rather than an anonymous client — the
legacy transports dispatch without one, and an empty name would read as a
client that failed to identify itself. `protocol_skew` appears when the
client asked for an MCP revision this server does not speak: the
connection still works, minus anything added after the pinned revision,
which otherwise surfaces as "the tool is just missing".

`/metaforge:doctor` (see [MCP prompts](#mcp-prompts)) is built on this
call, so anything the report cannot say, the doctor cannot say either.

### Tool annotations

Every tool reports `readOnlyHint`, `destructiveHint`, `idempotentHint`
and `openWorldHint` on `tools/list`, so a harness can tell
`twin.get_node` from `project.delete` without reading the description.

The classification lives in `mcp_core/annotations.py` and is deliberate,
never derived from the tool's name — `twin.record_claim` and
`twin.reject_engineering_change` both read like queries and neither is
one. A tool nobody has classified inherits MCP's own defaults
(`readOnlyHint: false`, `destructiveHint: true`), so a new adapter is
over-guarded rather than silently waved through.

`twin.query_cypher` is the exception worth knowing about: it rejects
mutating Cypher by default, but the adapter takes `--allow-twin-mutations`,
and when that is set the tool stops being read-only. The annotation is
computed per request from the adapter's own flag, so the hint matches
what the server will actually enforce.

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
| `twin` | `twin.record_document` | Persist a text artifact as a typed PRD/DOCUMENTATION/ROBOT_DESCRIPTION/SIMULATION_RESULT/LOAD_CASE work product (blob + project link); writes immediately, no approval gate. A `load_case` (FORGE-278) persists a reusable FEA boundary-condition definition (material, node sets, load force, source of loads) so it doesn't need retyping inline on every `calculix.run_fea` call | live-verified (MET-588) |
| `twin` (injected) | `twin.record_component_selection` | Persist one chosen `component.search_parametric`/`search_intent` result as a real BOMItem work product (graph node + project link) — without this, a search result was pure chat output with no reviewable, versioned trace in the Twin (MET-436). Optionally stores `datasheet_url`/`image_url`/`footprint`/`cad_model_url` plus purchase/pricing provenance (`purchase_url`, `price_currency`, `priced_distributor`, and an auto-captured `priced_at` timestamp — a price is a snapshot, never caller-timestamped) on the BOMItem; any of those left unset by the caller are auto-filled from the component catalog store when a match on MPN/manufacturer exists, so a bare selection call still gets media/cost fields when the catalog already knows the part. When project-scoped, also adds a real `CONTAINS` edge from the project's `BOM` work product (created on first use, reused via a `kind` metadata marker so a document-shaped BOM work product is never mistaken for the container) to the new BOMItem — without this a BOMItem had only the Postgres project-junction link and no graph edge, so `TwinAPI.find_orphans()` flagged it as an orphan and it was unreachable via `twin.thread_for` | unit-verified (MET-436) |
| `twin` | `twin.record_constraint_set` | Record structured requirements as evaluable Constraint nodes + a CONSTRAINT_SET work product; expressions compile-checked, violations feed gate criteria (MET-583). Each entry needs EITHER a raw `expression` OR a structured `metric`+`operator`+`limit`+`unit`+`target_node_type` binding (FORGE-259, gap G-A3) — the latter records a benign placeholder expression (`"True"`) instead of requiring hand-written Python, since live pass/fail/no_data status comes from `GET /v1/requirements/matrix`'s real Claim/Evidence data, never from evaluating this expression. An entry may also declare `verification_method` (FORGE-312) and `expected_evidence` (FORGE-258, gap G-A2) — the kind of evidence expected to verify it, validated against the same `EVIDENCE_TYPES` set `twin.record_evidence` itself accepts; G7 (Verification Readiness) checks both on every critical requirement | unit-verified (MET-582/FORGE-259/FORGE-258) |
| `twin` | `twin.record_engineering_entity` | Record one Engineering Intent & Requirements Harness entity (intent/stakeholder_need/objective/assumption/question/risk/verification_case/evidence/budget/invariant/waiver/release_approval), optionally linked to parent(s) via a real graph edge (`derives_from`/`satisfies`/`motivates`/...), resolved by exact name or UUID (FORGE-45/35). A `budget`/`invariant` (metric/unit/system_total or limit in `extra`) is read back automatically by the G3 Preliminary Feasibility gate; a `waiver`/`release_approval` only counts toward the G8 Release gate once approved via `twin.approve_engineering_entity` (FORGE-73). A `budget`'s `allocations[].target` can be a HierarchyNode id (FORGE-264) so `GET /v1/twin/hierarchy` can check that branch's real rolled-up mass/cost against its allocation, not just the flat project-wide total the G3 gate checks | unit-verified (FORGE-45/73) |
| `twin` | `twin.approve_engineering_entity` | Advance one EngineeringEntity's authority from `proposed` to `reviewed`/`approved` — a real approval step distinct from creation, giving `AuthorityState` (FORGE-51) its first-ever non-baseline write path. A `waiver`/`release_approval` recorded but never approved this way FAILS the G8 Release gate's check (FORGE-73) | unit-verified (FORGE-73) |
| `twin` | `twin.record_evidence` | Persist a tool-generated Evidence entity: the real structured output of a calculix/simulation/CAD/test tool call (producer, inputs, a content hash — never a restated assertion), optionally linked to the requirement(s) it supports/contradicts and revision-pinned `valid_against` dependencies that make FORGE-59's staleness propagation apply to evidence for the first time (FORGE-64/35). A `valid_against` entry can also pin a `work_product` (e.g. `CAD-BODY-003`, the doc's own example) — re-committing that same named geometry via `twin.commit_geometry` supersedes it and propagates staleness to any evidence pinned to the prior revision, and transitively to whatever depends on that evidence (FORGE-314). `supersedes` records a revalidation rerun and flips the stale evidence it replaces to SUPERSEDED (FORGE-65) | unit-verified (FORGE-64/65/314) |
| `twin` | `twin.record_claim` | Persist a requirement satisfaction claim: a real graph edge asserting an artefact (CAD model, schematic, ...) satisfies a requirement, citing evidence ids. Status (supported/unsupported) is always computed live from current evidence staleness, never cached — a claim with no evidence, or whose evidence has all gone stale, reports unsupported (FORGE-65/35) | unit-verified (FORGE-65) |
| `twin` | `twin.stage_work_product_file` | Materialize a committed work product's blob onto the shared adapter workspace, returning a file_path any freecad/cadquery/calculix tool can load — recovers a work product for inspection even after its authoring session is gone (MET-618) | unit-verified (MET-618) |
| `twin` | `twin.propose_engineering_change` / `twin.analyze_engineering_change` / `twin.approve_engineering_change` / `twin.reject_engineering_change` / `twin.commit_engineering_change` / `twin.mark_engineering_change_rolled_back` | The Engineering Change Transaction lifecycle (PROPOSED → ANALYZING → READY_FOR_REVIEW → APPROVED/REJECTED → COMMITTED/ROLLED_BACK, spec section 13) as MCP tools — real since FORGE-66/67 but unreachable from any agent until now. `analyze` computes real transitive impact (ImpactEngine) and approval level (HITLEngine), storing the full structured `revalidation_plan` (a pre-commit preview) on the ECT. `approve`'s `approver` is an agent-asserted identity string (same trust level as every `created_by` field in this API) but the independence check is real — raises if `approver` equals the patch's own `created_by`. `commit` actually applies the patch via `TransactionEngine`; on success it ALSO calls `StalenessEngine.propagate` for real for every REVISE/SUPERSEDE/DEPRECATE/INVALIDATE op — a real gap until FORGE-316 (commit never used to propagate staleness at all, despite the living plan's tracker assuming it already did) — and overwrites `revalidation_plan` with the real post-commit plan. On a conflict the ECT stays `APPROVED` with the conflict recorded, never silently marked committed. `mark_rolled_back` is a bookkeeping label only — no automatic undo capability exists anywhere in this system (FORGE-70/35/316) | unit-verified (FORGE-70/316) |
| `twin` | `twin.execute_revalidation_plan` | After a committed `twin.commit_engineering_change`, automatically re-runs exactly the Evidence a committed ECT's real `revalidation_plan` marked stale — only for entities carrying a replayable `metadata["replay"] = {tool_id, args}` recipe (e.g. `twin.evaluate_metric`'s own evidence, FORGE-315). Records fresh Evidence superseding the stale one (FORGE-65's flow) for each; anything without a replay recipe (hand-authored evidence, or a non-evidence entity the impact walk reached, e.g. a Constraint) is reported `manual_review_needed`, never guessed at. Does NOT attempt property-level impact through `Constraint.dependencies` (that field doesn't exist yet — explicitly deferred since FORGE-312/Step 2) or show anything on the dashboard (no ECT-related UI exists yet — both are their own separable follow-ups) (FORGE-316) | unit-verified (FORGE-316) |
| `twin` | `twin.rank_sensitivity` | One-at-a-time finite-difference sensitivity of a metric's margin against its critical parameters, against a CAD work product's own recorded geometry (target lifecycle spec App. A Solver / step 15). `metric="tip_deflection"` ranks `wall_thickness_mm` and `length_mm` (a new hollow-rectangular-tube deflection formula, `twin_core/prediction/evaluator.py`'s `hollow_tube_tip_deflection_mm` — the FORGE-315 solid-beam model has no wall-thickness concept at all); `metric="mass"` ranks `wall_thickness_mm` (continuous, `hollow_tube_mass_kg`) and candidate materials (categorical — ranked by the actual margin delta from swapping material, not a derivative; densities/elastic moduli resolved from `tool_registry.tools.cadquery.materials`, FORGE-234's own table, not a second one). Records the ranking as Evidence, `valid_against` the work product. No dashboard tornado-chart UI — result is MCP-tool/Evidence only, same scope cut as FORGE-316's own dashboard bullet (FORGE-317) | unit-verified (FORGE-317) |
| `twin` | `twin.evaluate_metric` | Tiered evaluator (target lifecycle spec §30, "minimum sufficient fidelity"): a tier-0 closed-form cantilever-beam hand-calc (`twin_core/prediction/evaluator.py`) against a CAD work product's own recorded geometry (`bounding_box`, MET-630), escalating to a real tier-2 `calculix.run_fea` call (via a lazily-bound MCP bridge, `api_gateway/server.py`'s `_LazyBridge`) when the estimate's margin to a supplied `limit_mm` falls inside its error band. Only `metric="tip_deflection"` is implemented. Every tier's result is recorded as Evidence pinned (`valid_against`, FORGE-314) to the work product's current revision. Tier-2 requires the caller to already have a generated mesh + node sets (`tier2: {mesh_file, fixed_node_set, load_node_set, material, load_force_n}`) — this tool does not derive FEA boundary conditions on its own; with escalation but no `tier2` args, it returns a clear "not attempted" result rather than guessing node-set names. Tier-2's persisted Evidence carries an explicit `"tier": 2` key (FORGE-318 fix — the raw `calculix.run_fea` output never named its own tier, forcing any consumer to guess it from `producer.tool` string matching) (FORGE-315/318) | unit-verified (FORGE-315/318) |
| `twin` | `twin.attempt_promotion` | The first gate in this system that actually REFUSES rather than only reports (`twin_core.consistency.gates`'s G3-G8 gates stay purely advisory — unchanged by this ticket). Checks each of `required_claim_ids` against §2.15's own live `build_requirement_matrix` status; `pass` → satisfied, `uncertain`/`stale` → blocks, `fail`/`no_data` → blocks unless an APPROVED `waiver` EngineeringEntity (FORGE-73) names that exact requirement id in its `parent_refs` (reuses G8's own `_evaluate_waivers_check` pattern, now scoped per-requirement rather than a blanket scan). Even with every required claim satisfied, promotion needs a non-empty `decided_by` (an agent-asserted identity string, same trust level as every `created_by`/ECT `approver` field in this API) — omitting it is a dry run, not an automatic grant. Persists an immutable `MaturityGate` record either way (whether promoted or refused), so "why wasn't this promoted" always has a real, queryable answer. `decided_by` is now recorded on every attempt, not only a promoted one. Gained `comment` (a reviewer's own rationale, distinct from the system-derived `blocked_reason`) and `reject` (FORGE-290, gap G-G4: a human veto that refuses a promotion EVEN IF every required claim passes — the evidence gate is necessary, never sufficient, to override a reviewer's own judgement). Exposed to the dashboard's new "Gate review" section on `/requirements` via `POST /v1/promotion/attempt` / `GET /v1/promotion` (the latter wraps the previously-unsurfaced `twin.list_maturity_gates`) | unit-verified (FORGE-319/290) |
| `twin` | `twin.optimize_parameter` | Bisection search for the minimum-mass `wall_thickness_mm` meeting `deflection_limit_mm` and `sf_limit` (safety factor), against a CAD work product's own recorded geometry (target lifecycle spec App. A Optimiser, step 10). "Over Design IR parameters" (the ticket's own Jira wording) doesn't correspond to anything real — `twin_core/design_ir/` is a CAD-authoring op sequence, never persisted onto a committed work product (FORGE-317's own finding, confirmed still true) — so this searches `wall_thickness_mm` instead, the parameter FORGE-317's own sensitivity ranking already found dominant. Mass is the stated objective, not a third constraint (the project's `moving_mass_budget` covers the whole assembly, not one part in isolation, and no per-part budget allocation exists to check a share of it against). Both constraints are monotonic in wall thickness, so bisection finds the exact minimum-mass feasible point, not a heuristic. New tier-0 safety-factor hand-calc (`cantilever_max_bending_stress_mpa`) needed a real yield-strength table — `tool_registry.tools.cadquery.materials.MATERIAL_YIELD_MPA`/`resolve_yield_mpa`, same discipline as the existing elastic-modulus table. Records the search as Evidence pinned to the work product; a feasible winner is also recorded as a Decision via the EXISTING `twin.record_decision` (no new "Decision with alternatives" node type — that mechanism already is exactly that), each rejected candidate as an alternative. Does NOT propose/commit an actual geometry change (`ControlledEntityKind` doesn't support `work_product` yet — a separate, nontrivial ECT widening; no parametrized re-authoring script exists for the real arm part either) or attempt automatic FEA boundary-condition derivation (same real, unsolved gap FORGE-315 already documented) — a numeric recommendation only. No dashboard UI, same precedent as FORGE-315/316/317/319 (FORGE-320) | unit-verified (FORGE-320) |
| `twin` | `twin.register_device_instance` | Register a specific manufactured unit (serial-number-level) of a product as a DeviceInstance (target lifecycle spec App. B REALISE, step 11) — the model existed since before this epic but had no CRUD or MCP tool until now. Optionally links it via an INSTANCE_OF edge to the design revision (a WorkProduct) it was built from, when `design_revision_ref` resolves to one; `product_id` stays a free-text identifier, never assumed to be a resolvable graph ref, since a unit can be registered without a born-digital design record on hand (FORGE-321) | unit-verified (FORGE-321) |
| `twin` | `twin.start_design_loop` / `twin.get_design_loop` / `twin.approve_design_loop` | Closed loop: propose → build → simulate → evaluate against constraints → revise → repeat, until pass or proven infeasible, with an iteration budget (target lifecycle spec's "dual state machine", gap G-G1). Does not reimplement the search — composes `twin.optimize_parameter` (FORGE-320) unchanged, then persists EVERY candidate it evaluates as a real, queryable `DesignLoopIteration` node (linked via `SUPERSEDES`/`CONSTRAINED_BY`) instead of the one opaque Evidence blob FORGE-320 left behind. `get_design_loop` returns the full iteration timeline; `approve_design_loop` records a human's approval of the converged winner (raises if the loop never converged — the "with a human approving at gates" half of this ticket's own yardstick line). Deliberately out of scope: infeasibility/duplicate-commit guards (FORGE-291), evidence-gated blocking (FORGE-290), an eval harness (FORGE-292), true multi-objective/Pareto-front search (FORGE-288) — all separate tickets (FORGE-287) | unit-verified (FORGE-287) |
| `twin` | `twin.start_tube_height_design_loop` | A second real parameter over the SAME closed-loop machinery above (FORGE-288, gap G-G2) — sweeps `height_mm`, `wall_thickness_mm` held fixed, over the identical hollow-tube hand-calc `twin.optimize_parameter` already uses; proof `start_design_loop`'s own candidate ingestion generalized beyond a single hardcoded parameter (`parameter_name`/`metric`/`candidate_mapper`, defaulting to the exact prior wall-thickness behavior — the full pre-existing design-loop test suite passes unchanged as the regression proof), not a second, separate loop implementation. `twin.get_design_loop`/`twin.approve_design_loop` work unchanged — they already read/update any `DesignLoopIteration` by `loop_id`. The dashboard's Design Loop section gained a small hand-rolled inline-SVG sparkline (objective vs iteration, no chart library in this codebase) and generic column headers reading `parameter_name`/`metric` off the fetched iterations, rendering correctly for either loop kind | unit-verified (FORGE-288) |
| `twin` | `twin.record_measurement` | Record a real-world measurement against an interface quantity (target lifecycle spec App. B REALISE/LEARN, step 11) — e.g. a measured tip deflection on a built unit. Appends the value to the named metric on an interface embedded in a system_architecture work product (explicit `work_product_id`, or the project's one such document, FORGE-313's own one-per-project assumption), and links the measuring DeviceInstance via a new MEASURED_BY edge. When `predicted_value` is supplied (e.g. from a prior `twin.evaluate_metric` call — the natural, honest composition, never fabricated here), records the residual as Evidence for calibration; the first real prediction a quantity gets backfills its previously-unset `predicted` field, never overwrites an existing one. `digital_twin/calibration/store.py` turns residuals into a real band (`k * stddev(\|residual\|)`, k=2.0, needs >=3 samples — below that the caller's fixed prior is unchanged) that `twin.evaluate_metric` now reads INSTEAD of its fixed 0.2 prior once enough history exists, scanned across every project on purpose since calibration is meant to inform the NEXT project, not cache per-project (`band_source`/`calibration_sample_count` in the response say which was used). Deliberately NOT full conformal prediction — running stats is the smallest real thing the ticket's own acceptance wording needs. Gerbers/pick-and-place export (a separate bullet in this ticket's own Jira Scope) deliberately deferred: the yardstick project has zero PCB artifacts to honestly demonstrate against, and the ticket's own Acceptance never exercises it (FORGE-321) | unit-verified (FORGE-321) |
| `twin` | `twin.record_hierarchy_node` | Persist one position in the project's product hierarchy tree (product/system/subsystem/assembly), nested via a real `CONTAINS{quantity, placement}` edge from an optional parent; `realized_by_node_id`/`instance_of_node_id` link a position to the cad_model/robot_description geometry or BOMItem that actually fulfils it (FORGE-260, gap G-B1) | unit-verified (FORGE-260) |
| `twin` | `twin.compute_hierarchy_rollup` | Sum mass/cost over one branch of the product hierarchy (a root id and everything it `CONTAINS`, recursively, weighted by each edge's own quantity), reading `mass_kg` from `REALIZED_BY`-linked cad_models and cost from `INSTANCE_OF`-linked BOMItems — computed live, never cached, so it can't go stale relative to its children (FORGE-260) | unit-verified (FORGE-260) |
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
| `calculix` | `calculix.extract_results` | Pull max-stress / max-displacement from `.frd`. FORGE-280: the `stress` block's own `accuracy` field auto-flags a suspicious result (max disproportionate to the rest of the nodal field — usually a point-load/BC concentration artifact) | [`tier1/fea-hp.md`](https://github.com/FidelOdok/MetaForge/blob/main/tests/uat/scenarios/tier1/fea-hp.md) |
| `calculix` | `calculix.cross_check_cantilever_beam` | Euler-Bernoulli hand-calc cross-check (sigma = M\*c/I) for a rectangular cantilever, tip-loaded — compares against an FEA max stress within a tolerance, for the textbook case a human caught FORGE-239's bad `fixed_node_set` with manually (FORGE-280) | unit-verified (FORGE-280) |
| `calculix` | `calculix.check_mesh_convergence` | Whether max stress has stopped changing meaningfully across element sizes already run (does not orchestrate the sweep itself — compares results the caller already produced) (FORGE-280) | unit-verified (FORGE-280) |
| `freecad` (opt-in) | `freecad.create_parametric` | FreeCAD-driven parametric solid | _none yet_ |
| `freecad` | `freecad.boolean_operation` | FreeCAD boolean ops | _none yet_ |
| `freecad` | `freecad.get_properties` | FreeCAD shape properties | _none yet_ |
| `freecad` | `freecad.describe_step_file` | Per-component breakdown of a multipart assembly file (Label/volume/area/bbox per named part, not just the flattened aggregate) (MET-629) | unit-verified (MET-629) |
| `freecad` | `freecad.export_geometry` | FreeCAD STEP / STL / IGES export | _none yet_ |
| `freecad` | `freecad.generate_mesh` | FreeCAD-driven mesh generation; `element_order` (1 or 2) selects linear (C3D4) or quadratic (C3D10) tetrahedra — second-order elements capture bending stress more accurately per element (FORGE-280) | _none yet_ |
| `freecad` | `freecad.list_named_faces` | Re-fetch an already-generated mesh's per-face geometry table (name, bbox, centroid, area, normal) without re-running gmsh — backs the dashboard's geometric boundary-condition face picker (FORGE-277) | unit-verified (FORGE-277) |
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

## Dashboard routes (13)

Served by Vite under `dashboard/` — boot with
`docker compose up gateway dashboard` and open `localhost:5173`.

| Path | Purpose | Backed by |
|---|---|---|
| `/projects` | Project list + create / update / delete | `GET/POST/PATCH/DELETE /v1/projects` |
| `/projects/:id` | Project detail, work-product tree | `GET /v1/projects/{id}` |
| `/sessions` | Workflow run list | `GET /v1/sessions` |
| `/sessions/:id` | Session detail, agent messages | `GET /v1/sessions/{id}` |
| `/approvals` | Pending change-proposal review | gateway approvals API |
| `/requirements` | Per-requirement quality flags (clarity/atomicity/quantified/traceability/verification-readiness), pairwise numeric-bound conflict detection with the conflicting pair highlighted, and a per-product-type completeness checklist (FORGE-257, gap G-A1); "Fix with AI" proposes a rewrite via the Requirement Author agent (never applied automatically). Evidence matrix (FORGE-318, spec step 16): requirements × claims × evidence, one row per requirement with a live-derived status (`pass`/`uncertain`/`fail`/`no_data`/`stale`) from the most current cited evidence's own margin, flagging stale evidence even when the claim is still SUPPORTED by other current evidence; expandable evidence detail (method/tier/value/limit/margin/staleness) and a Structure-tab link per row; client-side CSV/MD export. Constraint editor (FORGE-259, gap G-A3): a "+ New constraint" form picks metric/operator/limit/unit/target node directly — no hand-typed Python expression — reusing the exact same `twin.record_constraint_set` validation as every MCP caller; the new requirement appears immediately on the matrix (honestly `no_data` until a claim cites it). The editor and matrix rows also carry `verification_method`/`expected_evidence` (FORGE-258, gap G-A2) — a row where both are blank renders a red "not declared" badge, distinct from the matrix's own `no_data` status (which means "no evidence recorded yet", not "never said what verification it expects"). Design loop (FORGE-287, gap G-G1): a "Start design loop" form runs the closed propose→build→simulate→evaluate→revise loop against a CAD work product, renders the resulting iteration timeline (parameter/objective values, feasibility, status per candidate), and an "Approve winner" action once converged. Gate review (FORGE-290, gap G-G4): pick required claims + a maturity level + reviewer identity + optional comment, Approve (evidence-gated — blocks on `no_data`/`fail` unless waived, and on missing `decided_by`) or Reject (a human veto that refuses even a fully-satisfied set of claims); a history list shows every past attempt. Feature library (FORGE-269, gap G-D1): pick a named parametric feature (`bolt_pattern` -- a mounting plate with a circular bolt-hole pattern, or `rib` -- a triangular gusset), fill its typed parameters, generate a standalone CAD_MODEL work product from it via `generate_parametric_feature` (`domain_agents/mechanical/skills/`, a thin macro layer over the same Design IR entities `generate_cad_ir` already lowers -- only 2 of the ticket's 7 named features ship in this first cut, the rest are the same pattern, deliberately deferred) | `GET /v1/requirements/quality`, `GET /v1/requirements/matrix`, `POST /v1/requirements/{id}/fix`, `POST /v1/requirements/constraints`, `POST /v1/design-loop/start`, `GET /v1/design-loop/{loop_id}`, `POST /v1/design-loop/{loop_id}/approve`, `POST /v1/promotion/attempt`, `GET /v1/promotion`, `POST /v1/features/generate` |
| `/bom` | BOM viewer, with a flat/hierarchical toggle (FORGE-267): the hierarchical view derives an EBOM from the product hierarchy, quantities multiplied down the CONTAINS tree | `GET /v1/bom/...`, `GET /v1/bom/hierarchical` |
| `/sim` | Load-case editor + list (FORGE-278), with a 3D geometric face picker for fixed/load faces (FORGE-277); FEA results list with a numeric side-by-side version-compare panel (FORGE-279 — a mesh contour/colorMap overlay is deferred pending Twin mesh persistence), per project | `GET/POST /v1/simulation/load-cases`, `POST /v1/simulation/named-faces`, `GET /v1/simulation/results` |
| `/twin` | 3D viewer (R3F / Three.js) for STEP/GLB; its Structure tab (FORGE-261) shows the product hierarchy tree-table with per-node mass/cost rollups, allocation owner/discipline, and a per-node interfaces badge (FORGE-313, from `twin.commit_system_architecture`'s own interfaces); its Assembly tab's mates/joints are editable in place (add/delete, base/follower picked from the assembly's own part list) and persist directly onto the node with no live FreeCAD session or re-export needed (FORGE-271). Its History tab now also shows a parameter-level diff against a `SUPERSEDES` predecessor (FORGE-270, gap G-D2) when one exists — "editing" a FORGE-269 parametric feature is re-generating it with the same name and a changed parameter value, and `generate_cad_ir` now threads that value into the committed node's `geometry_features.parameters` so this diff has something real to read | `GET /v1/twin/files/...`, `GET /v1/twin/hierarchy`, `PATCH /v1/twin/nodes/{id}/assembly-joints`, `GET /v1/features/{id}/diff` |
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
