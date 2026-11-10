# The Imagine stage

Imagine is the first of Create's four stages (**Imagine, Develop, Check,
Make**). It holds intent and concept capture: the concept studio an agent
generates for each product, and the sketch tools. Develop, Check and Make are
driven by the digital twin (exact CAD, KiCad, firmware, simulation, DFM,
interface contracts, release); they show twin state and are not a second
editor.

The studio is **formgraph**, merged into MetaForge from formgraph 2.7.0 and
restyled to MetaForge's design tokens. The standalone formgraph plugin keeps
its own copy; this one lives in `api_gateway/imagine/` (provenance in its
`README.md`).

| | |
|---|---|
| Gateway | `/v1/twin/projects/{project_id}/imagine/...` (`api_gateway/imagine/`) |
| Twin item | `IMG-STUDIO`, work product type `imagine_studio` (a definition: every config change or rework is its next revision) |
| MCP | `twin.imagine_get_studio`, `twin.imagine_put_studio`, `twin.imagine_handover_status`; under delegation only, `twin.imagine_handover` and `twin.imagine_rework` |
| Engine | `api_gateway/imagine/engine/` (HTML, CSS and JS), three.js r128 vendored under `engine/vendor/` |

## Verification levels

Every claim the studio makes carries a level, and the browser cannot reach
past V2:

| Level | Name | Who reaches it |
|---|---|---|
| V0 | Captured | The browser: the person said it, or the agent inferred it from the brief |
| V1 | Consistent | The browser: the config passes the formgraph rules and agrees with itself |
| V2 | Screened | The browser: rules of thumb and simplified models ran in the engine |
| V3 | Analysed | MetaForge in the cloud: real solvers, bound to a twin revision |
| V4 | Verified | MetaForge in the cloud: checked against exact CAD, the schematic or a test, bound to a revision |
| V5 | Qualified | People, with evidence |
| Released | Released | The release gate |

**Nothing from the browser is stored above V2.** The gateway drops any field a
page could use to claim more (`verified`, `verification_level`, `authority`,
`approved`, `released` and the like) before anything is written; screening
results are stored as preliminary guidance (`dfm_status:
preliminary_guidance`, `verification_level: V2`) with the engine's rule
version; decisions, requirements and entities are written at their default,
proposed authority; manufacturing context fields are `assumption` or
`user_stated`, never `decision`; interface contract values are `estimated`.

## The studio

An agent writes the studio with `twin.imagine_put_studio` (or `PUT
/studio`): a formgraph product config, checked with the merged
`build_page` and `fg_context` rules. One thing differs from the plugin: the
product context (real parts, machines, options, models, scenarios, packed
references, made parts) travels **inline** as `config.context`, never as a
folder path, because the gateway never reads a path a browser or an agent
names. A reference must arrive packed (a base64 image data URI, or an encoded
mesh); a `file` is refused.

Each `PUT` is the next `IMG-STUDIO` revision. The person's earlier choices,
scenarios and confirmations carry over. A handed-over studio refuses a `PUT`
until a person reworks it.

### The catalogue

The processes are `im` (injection moulding), `cnc`, `sls` and `fdm`. `fdm`
is desktop FDM on a Bambu Lab P1S-class printer (0.4 mm nozzle, 0.2 mm
layers, 256 x 256 x 256 mm, enclosed chamber) with `petg`, `pla`, `petgcf`
and `asa`, finishes `asp`, `fine` and `sanded`, and the insert and
thread-forming fixings. It has its own screening rules, never the SLS ones:
walls of 1.2 mm or more in whole perimeters of a 0.42 mm line, the closed top
printed face down because it bridges only about 20 mm, a warp check on the
footprint (150 mm for ASA and filled grades, 220 mm otherwise), heat-set
inserts rather than thread-forming screws for repeat assembly, slots at least
two lines wide, and holes that print undersize. Cost is filament plus machine
time, with no tooling. The default `fdm-generic` machine uses the FDM rule
book, and a context can no longer replace a built-in process with a
`ruleBase` stand-in.

A library material named for its family, such as `prusament_petg`,
`bambu-petg-cf` or `polymaker_asa`, is accepted for `fdm`: the page screens
it with its family's figures (`petg`, `petgcf`, `asa`; the longest match
wins) and the validator warns that it did. The stored config, and so the
manufacturing context Develop reads, keep the name given.

The functions include `e_ldrv`, a constant-current buck LED driver dimmed by
PWM from the board, for a product whose main light is a high-power LED (a
lamp). `e_led` stays a status light.

## From a prompt

The chat builds the studio from what the person says. The
project brief the chat agent gets carries one line on the studio (*not
started*, *draft, revision n* or *handed over at revision n*), and the chat
system prompt adds one rule when the studio tools are in reach: with no
studio and a product described, read the `imagine_studio` playbook and build
the studio; with the studio handed over and the person asking to proceed,
read that playbook's *After handover* section and start Develop.

The playbook (`mcp_core/plugin_skills/imagine_studio/SKILL.md`, read with
`chat.read_playbook`) is the formgraph `new` workflow rewritten for the twin
tools:

1. Work from what the person said, asking at most three questions.
2. Call `twin.imagine_put_studio` with a config built from the playbook's
   skeleton, which passes the checks as written (`engine: "formgraph@2"`, a
   `product` with a lowercase slug id, a `form.template` from the catalogue,
   requirements with `pri` of `Must`, `Should` or `Off`, a `programme` with the
   intent ledger, assumptions and markets, an `environment` block, scenarios,
   interfaces and the inline context with the manufacturing quantity). A test
   (`tests/unit/test_imagine_studio_playbook.py`) runs that skeleton through
   the same validator.
3. Fix every reported error in one retry.
4. Call `twin.imagine_handover_status` and tell the person what to do in the
   studio and what is still missing.

The agent never claims above V2 and does not hand over: the handover is the
person's, in the studio, unless the owner delegates it (see
[delegated to an agent](#delegated-to-an-agent)). After it, the playbook reads the handed-over records
and starts Develop in order: the concept mesh, then exact CAD per made part
with the DFM and manufacturing context, then electronics and firmware against
the interface contracts. Approvals stay the person's.

In the dashboard, **Ask the assistant** on the empty studio and **Start
developing** after the handover send these turns directly (see
[the Create view](create-view.md#the-studio)). The Claude Code plugins carry
the same playbook as the `imagine_studio` skill and a `/metaforge:imagine`
command; there, after handover, mechanical parts are written client side with
the `generate_cad_script` skill (Claude Code writes the CadQuery and the
server runs and renders it), rather than `twin.design_cad_loop`, which spends
the gateway's model budget.

## REST

All under `/v1/twin/projects/{project_id}/imagine`. Authentication is the
gateway's middleware, like every other route.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/studio` | | `{project_id, status: "none"\|"draft"\|"handed_over", revision, config_ref, engine_url, config, updated_at, handover: {...}\|null, collections, warnings}` |
| PUT | `/studio` | `{config}` | the same; a new studio revision. 422 with `errors`, `warnings` and `findings` (the same finding objects `POST /validate` returns, no verdict) when the config fails the formgraph checks; 409 when handed over |
| GET | `/engine` | | the engine page with the project's config embedded (see below) |
| GET | `/engine/assets/{name}` | | the engine's own CSS and scripts: `engine.css`, `engine.js`, `three.min.js`, `OrbitControls.js`, `GLTFExporter.js` |
| POST | `/events` | `{event_id, kind, data}` | `{accepted: true, mapped_to: [refs], duplicate}`; idempotent per `event_id` |
| GET | `/handover` | | `{ready, items: [{id, label, status: "done"\|"missing"\|"assumed", detail}]}` |
| GET | `/validation` | | the validation report for the head revision (see Validation) |
| POST | `/validate` | `{config?, revision?, scope?, gates?}` | the same report as a dry run; 404 with no studio, 409 for a `config` on a handed-over studio, 422 for a non-head `revision`, an unknown `scope` ref (`bad_refs`) or an invalid config (the body is the report) |
| POST | `/handover` | `{op_id}` | 409 with `{detail: {message, items}}` (the whole checklist, the blocking ones `missing`) when not ready; otherwise `{handed_over: true, revision, created: [refs]}` |
| POST | `/rework` | `{op_id, reason}` | the studio, the same shape as `GET /studio`, on its new revision, with `stale: [refs]` |

`collections` is the twin-backed state of each browser collection, keyed by
document id (`choices`, `scenarios`, `contextEdits`, `assumptions`,
`requirements`, `ledger`, `screening`). The host sends it to the engine as
`fg:snapshot`.

`kind` is one of `choice`, `scenario`, `context_edit`,
`assumption_confirmed`, `requirement`, `ledger`, `screening`. The dashboard
sends the whole page write as `data`: `{collection, id, data}` with `event_id`
`evt-<uuid>`; a flat document with its `id` is also accepted. An event for a handed-over studio is refused with 409; a twin
write that fails returns 502 and the event can be retried with the same
`event_id`.

The handover and the rework are a person's decisions: a request from an agent
surface (`X-MetaForge-Surface: agent`) is refused with 403, as is an agent
confirming an assumption or a scenario, unless the owner delegates (below).
Both are idempotent per `op_id`. Each operation is published to the project's
operations log (`job:imagine-...`). An agent-surface scenario event that does
not confirm is stored as the agent's (`by: agent`), so it never reads as the
person's own scenario. `agent` and `on_behalf_of` in any event body are
dropped: only the delegated path sets them.

### Delegated to an agent

When the gateway owner delegates human-authority decisions
(`METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY=true`, default off; see
[delegating approvals to an agent](approvals.md#delegating-approvals-to-an-agent)),
an agent may hand over and rework, for fully autonomous runs:

- **REST.** `POST /handover` and `POST /rework` from the agent surface must
  carry `X-MetaForge-Agent` and `X-MetaForge-On-Behalf-Of`; without either
  the call is a 422, as on the approvals API. With authentication on, the
  person must be the authenticated principal (403 otherwise). With the flag
  off nothing changes: 403.
- **MCP.** `twin.imagine_handover {op_id}`, `twin.imagine_rework {op_id,
  reason}` and `twin.imagine_accept {op_id, assumptions, scenarios}` are
  always registered with the studio tools and are refused unless the flag is
  on in the process the call lands in (the gateway, or the MCP sidecar that
  serves the twin tools, so set it on both). They are human-authority tools
  (`mcp_core/guardrails.py`, and destructive in `mcp_core/annotations.py`):
  no project approval policy approves them. An agent whose MCP connection
  sends `X-MetaForge-Agent` and `X-MetaForge-On-Behalf-Of` gets a delegated
  approval on the ledger and the call runs at once, with no prompt; without
  them the call is held for a person (see
  [delegated holds through MCP](approvals.md#delegated-holds-through-mcp)).
  The person it acted for comes from that approval, never from a name the
  model supplies.

**Acceptance without a person.** `twin.imagine_accept` accepts the studio's
open assumptions and confirms its inferred scenario tests for the person the
agent acts for. An unknown id is refused with the ids the studio has. Each is
an ordinary studio event, idempotent per `op_id` and id:

- an assumption is recorded as **accepted**, never confirmed: it stays an
  assumption, the manufacturing context field it names stays `assumed`, and
  its `assumption` entity carries `accepted_by_agent` and `on_behalf_of`;
- a scenario is confirmed and marked `by: agent` with the agent and the
  person; its scenario requirement's source reads *agent <name> on behalf of
  <person>*.

The checklist then counts them (assumptions read `assumed`, scenarios
`done`), and each item's detail says so: *a-drop, a-qty accepted by agent
claude-code-e2e on behalf of fidel; not confirmed by a person*. The REST
equivalent is `POST /events` with `assumption_confirmed` (`confirmed` or
`accepted`) or `scenario` with `confirmed: true` from the agent surface:
403 with the flag off, 422 without both headers, and with authentication on
the person must be the authenticated principal. An agent's "confirm" of an
assumption is recorded as an acceptance there too.

**The concept mesh without a person.** The checklist's `concept_assembly`
item needs an illustrative concept mesh in the project. An agent makes one
with `twin.render_concept_image` and then `twin.generate_concept_mesh` from
that image (a job; poll `twin.job_status`). Both are illustrative writes, so
a project policy of `auto_clean` or `auto` approves them within the spend cap
(3 meshes an hour by default). Both default to the MCP session's project when
the call names none, and a mesh made from a concept image inherits the
image's project, so the checklist counts the agent's mesh like a person's.

The record says an agent decided, for that person, never that the person
did: the handover (and rework) record carries `by: "agent:<name>"`, `agent`
and `on_behalf_of`, and the operations log line ends *decided by agent
<name> on behalf of <person>*. The `imagine_studio` playbook and the
plugins' `/metaforge:imagine` command, in a delegated run, carry on without
stopping: accept, make the concept mesh, hand over once the checklist is
ready, then Develop, Check and Make, recording each delegated decision with
`twin.record_decision`. Without delegation they ask the person.

**After the handover, to a release.** Check and Make use tools that take a
reference, never file bytes:

1. State the machine the parts are made on with
   `twin.state_manufacturing_context` (`machine_or_supplier_profile`, for
   example "Bambu Lab P1S"). A delegated agent states it for the person; a
   machine the library holds is linked, so DFM assesses each part against
   its limits
   ([stating the manufacturing context](approvals.md#stating-the-manufacturing-context)).
   Then measure each validated part with `twin.evaluate_dfm` (it measures every
   part with no exact analysis yet) or `twin.measure_dfm` with its
   `item_key`; the gateway reads the STEP from the twin
   ([measuring by reference](twin_schema.md#dfm-by-reference)).
2. Resolve the concept's components with `twin.resolve_concept_component`:
   the illustrative components the handover seeded (`CMP-...`) are
   `realised_by` the CAD parts that make them, or `retired`, each with a
   reason; `restored` undoes it
   ([resolving concept components](twin_schema.md#concept-component-resolution)).
   It is the agent's own design decision, so a project policy approves it.
3. Run the structural load cases with `twin.run_item_fea` (mesh, then solve)
   and record each result with `twin.record_document`.
4. Run `kicad.run_erc` and `kicad.run_drc` and record each result with
   `twin.record_evidence`, pinned to the schematic or board revision it
   checked (`depends_on: ["PCB-...@n"]`): the release needs a passing ERC
   and DRC on the current revisions.
5. Put the product together and check it fits: commit each board's
   `kicad.export_3d` STEP with `twin.commit_geometry`, build the assembly with
   `twin.build_assembly` (every part and board at its placement, each hinge a
   revolute joint with its limits, and every screw and hinge pin in
   `fasteners` by designation, such as `ISO 4762 M2.5x8` or `ISO 2338 4x30`,
   placed in the holes it goes through), and check it with
   `twin.check_assembly` (overlaps, hole patterns, the board outline in its
   pocket, each edge connector's mating axis against its slot's opening and a
   USB-C plug envelope through the slot, each fastener in its holes, washer
   stacks, joint sweeps, seated parts). A connector named by its reference
   (`J1`) takes its footprint and rotation from the board file; record each
   slot on its part as `connector_slots`. A hole pattern or hinge with no
   fastener is an unfastened joint unless it is a snap or a press fit. Fix
   each high and medium finding
   ([design for assembly](twin_schema.md#design-for-assembly)).
6. Record the fabrication outputs, each with its `source_ref` (the revision
   it was made from: an output made from an older revision blocks the
   release), and two BOMs with `twin.record_design_files`
   (`work_product_type: bom`): the board's from `kicad.export_bom`, pinned to
   the `PCB-...@n`, and the assembly's, listing every made part and every
   fastener by designation and quantity, pinned to the `ASM-...@n`; an empty
   BOM, or one missing a bought part, blocks the release
   ([BOM files](twin_schema.md#bom-files)). Record every board and schematic
   as its item's revision: one that is not is left out of the package and
   blocks it. Then `twin.create_baseline`, a
   `release_approval` request with `twin.record_engineering_entity`, and
   `twin.create_release_package`. The baseline and the approval wait for the
   person: neither is a human-authority tool, so the human-authority
   delegation does not reach them
   ([release packages](twin_schema.md#release-packages)). When the owner also
   sets `METAFORGE_ALLOW_AGENT_RELEASE=true`, the delegated agent creates the
   baseline, approves the release with `twin.approve_release` and creates the
   package, each recorded as its decision on the person's behalf
   ([delegating a release](approvals.md#delegating-a-release-to-an-agent)).
   A failing ERC, DRC or assembly finding, or a stray board left out of the
   package, that is truly acceptable is
   accepted with `twin.accept_release_finding` and a reason: held for the
   person, or, under the same release switch, recorded as the agent's
   decision for them ([the release checklist](twin_schema.md#release-checklist)).

## Mapping into the twin

Each event is written through the service the rest of MetaForge uses for that
kind of record (`api_gateway/imagine/twin_bridge.py`):

| Studio | Twin |
|---|---|
| Intent ledger | An `intent` item per statement (`INT-IMG-L<n>`), with its ledger status and what it maps to |
| Requirements, custom requirements | The `CS-IMAGINE` constraint set: each with its source (`limit_source`) and verification method |
| Assumptions | An `assumption` entity; the manufacturing context field it names, marked `assumption`. A person's confirmation records it as stated and flips the field to `user_stated` |
| Option choices | A design decision, with the other candidates as alternatives, pinned to the studio revision |
| Parts (at handover) | A concept assembly decision: each chosen part gets its `CMP-` component id; sources, retrieved dates and envelopes are in the record |
| Scenarios | Scenario requirements in `CS-IMAGINE` (verified by demonstration) |
| Context edits | Manufacturing context changes with provenance (`imagine.studio`, the actor, the studio ref) |
| Interfaces (at handover) | Proposed interface contracts: `IFC-CAD-EDA` (each part's envelope) and `IFC-EDA-FIRMWARE` (each part's buses). One Develop already holds is left alone |
| Screening results | An `evidence` record at V2: `dfm_status: preliminary_guidance`, the rule version, every rule's result |

## The handover gate

`GET /handover` (and `twin.imagine_handover_status`) checks, in order. The gateway
still evaluates this checklist to gate `POST /handover`. The verification service
decides the same nine items for the validation report (see Validation), and a
parity test keeps the two equal:

1. `ledger`: the intent ledger is complete, every statement reviewed.
2. `assumptions`: each assumption confirmed, or explicitly accepted as an
   assumption (then the item reads `assumed`; an agent's acceptance is named
   in the detail).
3. `scenarios`: every scenario test confirmed by a person, or by a delegated
   agent for them (named in the detail, never as the person's).
4. `requirements`: each requirement has a source and a verification method
   (`assumed` when the catalogue's default method is used).
5. `parts`: one chosen part per function, each with a source, a retrieved
   date, interfaces and a 3D envelope.
6. `manufacturing_context`: process, material and quantity recorded.
7. `concept_assembly`: a chosen part for every function, and an illustrative
   concept mesh for the project.
8. `interfaces`: interfaces described, so contracts can be proposed.
9. `screening`: screening results stored at V2.

`ready` is true when no item is `missing`. `POST /handover` then writes the
requirements, the manufacturing context, the ledger, the concept assembly
(with component ids) and the proposed contracts, freezes the studio revision
read-only and returns every reference it created.

## Validation

`GET /validation`, `POST /validate` and `twin.imagine_validate` answer one
question: is the Imagine phase done, and if not, what is left and who clears
it. They are a dry run. Nothing is written, no assumption is accepted and
nothing is handed over.

**The verification service decides, not this gateway.** Every rule that
decides pass or fail lives in MetaForge-Verification (profile `imagine-v1`,
`POST /v1/validations`). The gateway states the studio's facts and renders the
report it gets back. The facts are the ledger rows, the assumptions, the
scenarios, the requirements, the part chosen for each function with its
source, date, interface count and whether it has an envelope, the
manufacturing context, the markets, and whether a screening result and a
concept mesh exist. The report is returned unchanged, with `project_id`,
`computed_at` and `stale` added. A field the service adds later passes through.
The studio never shows a pass the service did not compute.

| Setting | Meaning |
|---|---|
| `METAFORGE_VERIFICATION_URL` | Base URL of the verification service. Unset means not configured. Read by the gateway and the MCP sidecar. |
| `METAFORGE_VERIFICATION_TOKEN` | Bearer token the service issues. A secret. |

**When the service cannot answer** (not configured, unreachable, refusing the
credential, failing, or sending a report this gateway cannot read) the answer
is `200` with `verdict: blocked_external`, `level: V0`, no gates and exactly one
finding, `verification.service`, with resolver `external` and status
`not_assessed`. It is never a local fallback: the studio is simply not
assessed, and an agent reports that and does not retry in a loop. A request the
service calls malformed is a `502`, because that is a bug here and not an outage.

### The report

- `verdict` says who acts next, first match wins: `invalid` (the config check
  failed), `needs_agent`, `blocked_external`, `needs_person`,
  `ready_for_handover`. The agent never declares the studio done.
- `level` is `V0` (captured), `V1` (consistent) or `V2` (screened), never higher.
- `gates` lists `config_valid`, `handover` and `define_exit`, each with a
  `decision` of `pass`, `fail` or `incomplete`. A gate decision and a verdict are
  different answers: a gate can be `incomplete` while the verdict is `needs_person`.
- `findings` each carry `check_id`, `gate`, `severity` (`blocking` or
  `advisory`), `status` (`pass`, `fail`, `assumed`, `inconclusive`,
  `not_assessed`), a `target` (`kind`, `id`, `revision`), generated `message`
  text, `evidence` refs, a `resolver` and an optional `fix`.
- `resolver` is who can clear a finding: `agent` edits the config, `person`
  makes a decision only a person can, `external` needs something outside the
  project (a concept mesh, or the service itself).
- `fix` is `{op, pointer, needs}`. It names what is needed and where it must
  come from, never a value. `pointer` addresses the submitted revision (for
  example `/parts/0/source`), not the formgraph config, so a client maps it to
  its own document.
- `debt` lists the `assumed` findings: accepted as assumptions and not
  confirmed. They are never folded into a pass, and `accepted_by` names an
  agent that accepted one for a person.

Finding text is generated from ids and counts. It is data, not instructions.

### Gates

- `handover`: the nine items above, as findings. The parts item is one finding
  per part per check (`parts.source`, `parts.interfaces`, `parts.envelope`) and
  one per function (`parts.chosen`), so a fix is addressable.
- `define_exit`: the Programme view's exit from Define (ledger reviewed,
  assumptions confirmed, markets chosen, every requirement has a value, every
  requirement has a planned test). It is advisory for the handover and a gate of
  its own. It is stricter on purpose: an accepted assumption passes the
  handover and fails the Define exit. The last two checks need the studio
  engine's own answers, which the gateway does not send yet, so they read
  `not_assessed` rather than a guess.
- `config_valid`: the formgraph check, which this gateway still runs. This is a
  labelled interim exception until those rules move into the service. The
  gateway sends the result as findings, one `pass` when the config is clean, and
  the service folds it into the verdict. It never assumes a config is valid.

### Request and errors

`POST /validate` takes `config` (check a draft instead of the head's), `revision`
(only the head is validated), `scope` (re-check only the refs you changed, as
`{kind, id}`) and `gates` (`config_valid`, `handover`, `define_exit`). `GET
/validation` is the report for the head revision.

An invalid config is a `422` whose body is the service's report with verdict
`invalid`. Over MCP, `twin.imagine_validate` returns that report, and the
blocked report, as a result and not as an error. It is read-only, so it is never
held for approval.

### The form in the revision

When the studio's form is authored (`form.template` is `authored`), the revision
sent to the service carries `form`: `kind: "authored"` and each part's `id`, its
`kind` (`generated`, `primitive` or `component`) and whether it is the `body`. The
geometry code never leaves the studio. A studio from before the enclosure template was
removed sends no `form`, because the service refuses fields it does not know. If an
authored block has no usable parts, none is sent.

For an authored form the service adds two advisory findings and no blocking one:
`form.enclosure_checks` (`not_assessed`: the checks that need an enclosure were not
run) and `form.confidence_mix` (every part is a bought component). Advisory findings
cannot change the verdict, a gate or the level. The verification service must be a
build that knows `form`; an older one refuses the request, so the pin MetaForge's CI
installs has to move with this change.

## Rework

`POST /rework` opens the next `IMG-STUDIO` revision as a draft. Moving the
item's head is what marks the records made for the frozen revision stale,
through the twin's existing impact path: decisions are pinned to the studio
revision and go stale with it, and the screening evidence is marked stale
through the same staleness path. Develop and Check then show that evidence as
needing a re-run.

## The engine page and the host bridge

`GET /engine` serves the fixed engine with the project's config embedded the
way formgraph's `build_page` does it: the JSON with every `</` escaped, in a
`<script id="fg-config" type="application/json">` block the page reads with
`JSON.parse`. Asset URLs are relative (`engine/assets/<name>?v=<hash>`), so
the page works behind the dashboard's `/api` proxy. `?theme=light` or
`?theme=dark` sets the theme; otherwise it follows the system.

It is served with a strict Content Security Policy:

```text
default-src 'none'; script-src 'self'; style-src 'self';
style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; worker-src blob:;
font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none';
form-action 'none'; frame-ancestors 'self'
```

No inline script, no `eval`, no third-party origin. three.js r128 is vendored
(`engine/vendor/MANIFEST.json` records the npm tarball's integrity and each
file's sha256, and a test checks them). No web fonts are fetched: the page
uses the MetaForge faces when installed, then the system UI font. The
background solver is a `blob:` worker the engine builds from its own code, and
reference images are `data:` URIs, which is all `worker-src` and `img-src`
allow beyond the gateway. `METAFORGE_IMAGINE_FRAME_ANCESTORS` widens
`frame-ancestors` when the dashboard is served from another origin.

The dashboard hosts the page in `<iframe sandbox="allow-scripts">`, without
`allow-same-origin`. Messages carry `{fg: 1, type, ...}`:

| Direction | Message | Meaning |
|---|---|---|
| page to host | `fg:ready {engine}` | the engine booted |
| page to host | `fg:write {collection, id, data, event_id}` | a write; the host forwards it to `POST /events` |
| page to host | `fg:height {px}` | the height the page would like at its width (480 to 960 px); never the document's height |
| page to host | `fg:select {component_id}` | a BOM line was selected |
| page to host | `fg:download {name, mime, base64}` | a file to save (the sandbox cannot download) |
| host to page | `fg:snapshot {collections}` | the twin-backed state of each collection |
| host to page | `fg:readonly {value}` | after the handover: every edit control is disabled |

The page detects it is hosted when `window.parent !== window`. It listens
only to its parent frame, locks onto the origin of the first host message,
queues writes until the first `fg:snapshot`, and never takes code from the
host. In read-only mode every edit control is disabled (view, simulate, Try
it and copy controls stay usable), graph editing is blocked, and any write is
refused in the page as well as by the gateway. Standalone, with no parent
frame, the engine keeps the plugin's `claude.use('db')` path, so the two
copies can diverge cleanly.

The host validates every message's origin and shape, forwards `fg:write` to
`POST /events` and never executes anything from the page.

### The engine hosted in Create

Hosted, the engine marks its root `fg-hosted` and fits the frame rather than
a browser window:

- **Chrome.** The product name and logo, and the side-panel handles, are
  hidden: Create shows the product, the stages, the Studio and Sketch tabs and
  the theme. The bar keeps **Design** / **Programme**, **Simple** /
  **Engineer** and the more menu. The 3D view's own first mode reads
  **Model** (standalone it is **Studio**), so it is not confused with
  Create's Studio tab.
- **Layout.** At 900 px wide and more, the controls run down the left
  (36%, at least 280 px) beside the 3D view, which is `min(560px, 60vh)` tall
  with the panels under it; each part scrolls on its own and the frame's
  height is the host's. Below 900 px everything stacks in one column, the 3D
  view first at `clamp(300px, 70vw, 440px)`, and the page scrolls inside the
  frame. Nothing scrolls sideways.
- **3D view.** A `ResizeObserver` on the view resizes the renderer and the
  camera's aspect. The camera frames the part's bounding box (its bounding
  sphere fits both the view's height and width, with a 15% margin, along a
  fixed three-quarter view), and frames it again whenever the view changes
  size, until the person moves the camera.
- **Height.** `fg:height` reports the height the page would like from its
  width alone: the bar, the view (560 px wide, up to 440 px narrow) and room
  for the panels, between 480 and 960 px. It never reports the document's
  height, which follows the frame's own height and would grow the frame
  without end.
- **Only the configured product.** The shell's section and part names come
  from the config: `form.label` when it is set, *Fuselage shell* for a
  multirotor airframe, *Shell* otherwise; never the template's own words.
  When the config sets no `form.params`, there is nothing of the product's
  own to draw: the 3D view shows *No shape yet* (no part, no floor grid, no
  dimensions or colour chips) until the person moves a size control in the
  studio. The embedded `fg-config` is the only config read; a sandboxed frame
  has no storage, so no earlier session's state can replace it.

The engine's tests are `tests/unit/test_imagine.py` (static checks that run
in CI) and `dashboard/e2e/imagine-engine.spec.ts`, which hosts the real engine
files in a sandboxed frame in Chromium and checks the empty state, the
labels, the bounding-box fit through a resize, the one-column layout and the
bounded height (`npx playwright test imagine-engine`).

## Authored forms

Every form is authored. The enclosure template was removed: the checker refuses it with a
message saying so, and a studio written before the removal stays readable but cannot be
drawn (the engine page answers 410 and `twin.imagine_look` answers `not_authored`). Nothing
deletes stored studios; write a new authored studio instead. A form sets `form.template` to
`authored` and describes the form as parts, each carrying the body of a geometry function,
`build(p, g)`. A box around electronics is a form whose body part is a `g.box`. The agent skill
(`mcp_core/plugin_skills/imagine_studio/SKILL.md`) is the reference for writing
one, with the shape builder and a worked lamp; this section says how the gateway
and the page treat it.

### The config

| Field | Meaning |
|---|---|
| `form.params` | The dimensions, as numbers. A name is letters, digits and underscores, starting with a letter, 32 characters at most. |
| `form.ranges` | `[min, max]` for the parameters a person may move. |
| `form.authored.parts[]` | `id` (a slug), `name`, `kind` (`generated`, `primitive` or `component`), `code`, `uses` (parameters the code reads, each declared), `body` (at most one part), `max_triangles` (optional). |

`kind` says how far to trust a part as geometry, and the page colours the three
differently. A `component` is a stand in for a bought part drawn from its
envelope, not the part's real mesh. `form.template` must be `authored`.

### Checks when it is written

`api_gateway/imagine/formgraph/authored.py` is pure and runs inside the formgraph
checks, so `PUT /studio` refuses the config, naming the part, when a part's code:

- contains any of `fetch`, `XMLHttpRequest`, `WebSocket`, `importScripts`, `import`, `eval`, `Function`, `document`, `window`, `self`, `globalThis`, `postMessage`, `constructor`, `prototype`, `__proto__`, `require`, `process`, `this`, in code, a comment or a string;
- contains a backslash escape or a backtick template string, which can hide a word;
- is over 6000 bytes, or the parts together are over 40000;
- declares more than 20000 triangles for a part or 100000 in all.

More than 24 parts, a duplicate id, an unknown kind, two bodies or a
parameter the code uses but `form.params` does not declare are refused too. With
no `body` part the write passes and warns that the checks that need an enclosure
will read `not_assessed`. This is the first of two layers: a word list is never
the safety boundary.

### The sandbox

`engine/authored.js` is loaded before `engine.js` and runs the code on the page
without trusting it:

- **Isolation.** There is one worker per part, so a syntax error, a crash or an
  endless loop belongs to one named part and stops no other.
- **The sandbox first.** Each worker's source removes the network, storage,
  timers, messaging and `eval` from its scope (18 names) before the part's code is
  placed in it. The code is the body of a function in the worker's source text,
  never passed to `eval`, so the page's Content Security Policy is unchanged:
  `script-src 'self'`, `worker-src blob:` and `connect-src 'none'` still apply, and
  `Function(...)` inside a worker dies with an `EvalError` (checked in Chromium).
- **A time limit on the code, not the page.** The page solves on its own thread,
  so a busy page cannot read a worker's reply for seconds. The limit (3 seconds)
  starts when the worker reports that it has started; a separate 20 second limit
  covers a worker that never starts.
- **Nothing it sends back is trusted.** Every number must be finite and within
  100000 mm, every index must point at a real vertex, and the triangle count must
  fit the part's budget, or the part reports an error.

### What the page shows

The page keeps the studio's own look and controls. The left pane is the engine's own controls
panel, filled with the form's parameters: the same sections and sliders as any studio, with
readable labels (`slot_len` reads "Slot len"), and a Parts list that gives each part's colour
(its kind) and size. Moving a slider runs the code again with no model call. The enclosure's
own node graph editor is hidden, because it edits parameters a form made of code has none.

A part of kind `component` can name its real part with `component_ref`, the `id` of a part in
`context.components.parts`, and say where it sits with `at` (`[x, y, z]` in millimetres, Y up) and
optionally `rot` (degrees). With no `code` of its own, the studio writes the drawing code from that
part's size on every write, so the shape and the sourced part cannot drift apart. The write names each
part drawn this way in its warnings. A part with its own code keeps it, a reference to a part that does
not exist is refused, and a component with no reference is flagged as a stand-in.

At the Engineer level the left pane shows the form's own graph instead: a column of parameter
nodes (each with its slider), a column of part nodes, and the form node. A wire runs from a
parameter to each part that uses it, and from each part to the form. Sliders here and in the
Simple panel stay in step. Selecting a part (its title bar) lights its node, its wires and its
mesh. Scroll zooms, dragging the background pans, and Fit frames everything. The page has three
panes, the graph, the model and the summary, and the left and right panes each minimise with
the edge buttons (or `[` and `]`), also when the page sits inside the dashboard's frame.

At the top of the right pane, a "Form drawn" card shows the measured envelope, volume, part count,
triangles and mass as tiles, and a collapsed "Not assessed" card lists the checks the engine cannot
make for a coded form. Both open and close like the engine's own cards.

The right pane keeps what carries the person's controls and records, and labels what the engine
cannot say about this form:

- **Readiness**: gate 1 names the assumptions still to confirm. Gates 2 to 4 read "Not assessed for
  an authored form", because the engine decides them from checks of the enclosure.
- **Requirements**: the priorities and values, the situations it must handle, and "Confirm these
  assumptions". Each requirement's chip reads "Not assessed", never Met, and the header says so.
- **Options, Context, Models and scenarios, Manufacturing process** work as for any studio.
- The **Programme** tab works as for any studio.

It hides what asserts something about the placeholder enclosure that the engine solves for every
studio, which is not this product: validation in the background, assembly, where it lives, the
manufacturing verdict, product tests (a drop test of the placeholder), electronics and wiring,
checks, the bill of materials (a top shell and a lid the form does not have), part, cost, hand off,
and Readiness's parts table. Giving the engine no shape instead would break the Programme tab, so
the blocks are hidden and the verdicts relabelled.

The page also shows what was measured (size, volume of the shapes with overlaps counted twice,
parts, triangles and the mass if solid in the process material) beside a list of what was not
assessed: wall thickness and moulding, the fit of the electronics, vents and heat, the
manufacturing verdict and cost, assembly, product tests and the form's bill of materials. That list
never reads as a pass. A form that fails shows "Form not drawn" or "Form partly drawn" with each
part's error and gives no size.

The page publishes what it drew as `window.FG_AUTHORED`: `status` (`running`,
`ready` or `error`), `params`, `bbox`, `triangles`, `errors`, and for each part its
`kind`, `triangles`, `bbox`, `volume` and `error`. It dispatches an `fg-authored`
event on every change.

### Looking at it

An agent looks with `twin.imagine_look` (see `docs/mcp_spec.md`). It renders the
stored studio's engine page in headless Chromium inside the process: the page and
its assets are answered from memory with the gateway's own Content Security Policy
and every other request is aborted, so no gateway has to be running and it works in
the stdio plugin. It returns the status, each part's size and error and the
screenshots as images. `set` moves a parameter in the page and looks again, never
saved; `revision` names the head after a write, so a repeat is not answered from
the earlier result. It reads and writes nothing in the twin and is never held.
`playwright` and a Chromium build are not installed with MetaForge
(`pip install playwright pillow && playwright install chromium`): without them the
result is `unavailable` with that line, and the agent must say it could not look.
A studio from before the enclosure template was removed answers `not_authored` without starting a
browser.

`scripts/imagine_look.py` is the same look from a shell. It opens an engine page in
a real browser (it needs the `playwright` package and a Chromium build), waits for
`window.FG_AUTHORED`, prints each part's size and takes a screenshot:

```bash
python scripts/imagine_look.py --gateway http://127.0.0.1:8000 --project <id> \
    --out look --set arm_len=400
```

With `--set` it moves that parameter's slider and looks again, and reports what
changed, how much of the picture changed and every request the page made after
the change: the sandbox workers start from `blob:` URLs, so a change that redraws
the form shows no network request and no call to a model. It exits 0 when the form
is drawn, 2 when a part failed and 3 when the page published no authored form.

### What the verification service says

The revision carries the form's parts (see "The form in the revision"), and the
service adds two advisory findings: `form.enclosure_checks` (`not_assessed`) and
`form.confidence_mix` (every part is a `component`). Neither can change a
verdict, a gate or a level.

### Limits

- The electronics are not placed in an authored form, so the fit of the
  electronics is not assessed. There are no component meshes yet: a `component`
  is a box.
- Shapes compose and do not subtract: there are no boolean cuts.
- The page's measured size is not sent to the service, so no fit check runs.
- The look needs a browser. A client with none must say it could not look.

## Observability

`metaforge_imagine_studio_total{operation, outcome}` counts each `put`,
`event`, `validate`, `handover`, `rework` and `engine` call as `ok`, `duplicate`,
`refused` or `failed`, and a `validate` also as `invalid`, `unconfigured`,
`unavailable` or `error`; the `ImagineStudioTwinWritesFailing` alert fires when
more than a fifth of an hour's operations fail to write to the twin. Logs:
`imagine_studio_put`, `imagine_event_mapped`, `imagine_event_failed`,
`imagine_handed_over`, `imagine_handover_failed`, `imagine_reworked`,
`imagine_rework_failed`, and for validation `imagine_verification_unconfigured`,
`imagine_verification_unreachable`, `imagine_verification_credential_refused`,
`imagine_verification_failed`, `imagine_verification_request_refused` and
`imagine_verification_report_unreadable`. Spans: `imagine.*`, and
`imagine.verification.validate` for the call to the service.

## Authentication and the engine URL

An iframe cannot send a bearer token. With authentication off
(`METAFORGE_AUTH_MODE=off`, the local default) `engine_url` is the plain
gateway path. With a provider installed, `GET /studio` returns `engine_url`
with a `t` query parameter: a token signed with HMAC-SHA256 under the
gateway's link secret (`METAFORGE_LINK_SECRET`), scoped to the project and
expiring after ten minutes (`api_gateway/imagine/engine_token.py`). Its MAC
is domain-separated, so it is never a valid signed link and a signed link is
never a valid engine token. The auth middleware lets `GET .../imagine/engine`
and `GET .../imagine/engine/assets/{name}` through (`PUBLIC_GET_PATTERNS`);
the engine route checks the token itself and answers 401 without a valid one,
and the assets carry no project data. The page is served with
`Referrer-Policy: no-referrer`, so the token does not leave in a referrer.
Read the studio again to get a fresh URL. Nothing here names a provider.

## Known limits

- The enclosure template was removed (see "Authored forms"). The engine page still carries the
  enclosure solver and its checks in `engine/engine.js`, which no new studio reaches; that
  dead code has not been deleted yet, and the catalogue still lists the template's data.
- The concept assembly decision records each part's envelope in its text and
  the studio's handover record; a concept layout with sizes and positions is
  still drawn by `twin.render_concept_sketch`.
- The handover gate (`POST /handover`) still evaluates the original nine-item
  checklist in the gateway. The verification service decides the same items for
  the report, and a parity test (it needs the service's package on the path, and
  skips without it) keeps the two equal. They differ in one deliberate place: a
  whitespace-only part source or date counts as present in the checklist and as
  missing in the service. Moving the gate to consume the service's verdict is
  follow-up work.
- A part's source is checked for presence, not that it was read or says what is
  claimed. A template value such as `<order code>` still counts as present.
- `screening.stored` has resolver `person`, because only the studio engine in
  the browser produces a screening result today. It becomes `agent` once the
  screening rules run in the service.
- The `config_valid` check is the gateway's, not the service's (see
  Validation). While the service is unreachable the report omits those findings;
  `PUT /studio` still explains a config error.
