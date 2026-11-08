# The Imagine stage

Imagine is the first of Create's four stages (**Imagine, Develop, Check,
Make**). It holds intent and concept capture: the concept studio an agent
generates for each product, and the sketch tools. Develop, Check and Make are
driven by the digital twin (exact CAD, KiCad, firmware, simulation, DFM,
interface contracts, release); they show twin state and are not a second
editor.

The studio is **formgraph**, merged into MetaForge from idroneinnovations
formgraph 2.7.0 (idroneinnovations is a trading name of idroneimages ltd) and
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
| PUT | `/studio` | `{config}` | the same; a new studio revision. 422 with `errors` and `warnings` when the config fails the formgraph checks; 409 when handed over |
| GET | `/engine` | | the engine page with the project's config embedded (see below) |
| GET | `/engine/assets/{name}` | | the engine's own CSS and scripts: `engine.css`, `engine.js`, `three.min.js`, `OrbitControls.js`, `GLTFExporter.js` |
| POST | `/events` | `{event_id, kind, data}` | `{accepted: true, mapped_to: [refs], duplicate}`; idempotent per `event_id` |
| GET | `/handover` | | `{ready, items: [{id, label, status: "done"\|"missing"\|"assumed", detail}]}` |
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

1. Measure each validated part with `twin.evaluate_dfm` (it measures every
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
4. Record the fabrication outputs, then `twin.create_baseline`, a
   `release_approval` request with `twin.record_engineering_entity`, and
   `twin.create_release_package`. The baseline and the approval wait for the
   person: neither is a human-authority tool an agent may decide under
   delegation ([release packages](twin_schema.md#release-packages)).

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

`GET /handover` (and `twin.imagine_handover_status`) checks, in order:

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

## Observability

`metaforge_imagine_studio_total{operation, outcome}` counts each `put`,
`event`, `handover`, `rework` and `engine` call as `ok`, `duplicate`,
`refused` or `failed`; the `ImagineStudioTwinWritesFailing` alert fires when
more than a fifth of an hour's operations fail to write to the twin. Logs:
`imagine_studio_put`, `imagine_event_mapped`, `imagine_event_failed`,
`imagine_handed_over`, `imagine_handover_failed`, `imagine_reworked`,
`imagine_rework_failed`. Spans: `imagine.*`.

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

- The form catalogue (`api_gateway/imagine/formgraph/data/catalogue.json`)
  has one template, `enclosure`: a rounded, shelled box with a lid. It can
  stand for a lamp's base housing but not an arm, a hinge or a shade, and the
  engine labels every product's shell *Fuselage shell* (`engine/engine.js`,
  the parts panel and the readiness row), wording from the multirotor
  reference. Non-box products need more templates, or their made parts in the
  inline context.
- The concept assembly decision records each part's envelope in its text and
  the studio's handover record; a concept layout with sizes and positions is
  still drawn by `twin.render_concept_sketch`.
