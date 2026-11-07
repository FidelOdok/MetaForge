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
| MCP | `twin.imagine_get_studio`, `twin.imagine_put_studio`, `twin.imagine_handover_status` |
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
confirming an assumption. Both are idempotent per `op_id`. Each operation is
published to the project's operations log (`job:imagine-...`).

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
   assumption (then the item reads `assumed`).
3. `scenarios`: every scenario test confirmed by a person.
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
| page to host | `fg:height {px}` | the page's height |
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

- The concept assembly decision records each part's envelope in its text and
  the studio's handover record; a concept layout with sizes and positions is
  still drawn by `twin.render_concept_sketch`.
