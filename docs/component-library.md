# Reusing components and model assets

MetaForge searches existing project/library parts and complete commercial or
open hardware modules before proposing a custom design. Record why a reusable
module fails the requirements before choosing to build from individual parts.
This is agent guidance, not a claim that every proposed selection has passed
engineering verification. Missing specifications remain unknown.

## Existing catalogs

- `library.list_components`, `library.get_component`, and the register/update
  tools manage reusable entries with sourced specs and revision history.
  Besides mechanical families, an entry can be a `module` (a bought board or
  module, such as a display or a GNSS module), a `battery` or an `electronic`
  part (a single chip, button or connector). Such an entry also carries
  `manufacturer`, `mpn` (the maker's order code), `datasheet` (an http(s) link)
  and `interfaces` (`[{id, bus, dir}]`, `dir` one of `in`, `out`, `bi`); a
  battery's `capacity_mah` is a typed spec. An electronic part keeps its datasheet figures as typed specs too, one value each and a range as its min and max: `input_voltage_min_v`, `input_voltage_max_v`, `output_voltage_v` (V), `output_current_a` (the maximum continuous output) and `current_limit_a` (a switch or peak current limit) (A), `supply_current_ma` (mA), `switching_frequency_khz` (kHz), `power_w` (W), and `operating_temp_min_c` and `operating_temp_max_c` (C, which may be below zero). A minimum above its maximum is refused. Entries saved before these fields
  load with them empty. The body of a module, battery or electronic part is
  `length_mm` x `width_mm` x `height_mm`: `thickness_mm` (for plates and
  washers) is refused for these families. An older entry that named its height
  `thickness_mm` drops it when an update sends `height_mm`.
  An entry can also carry `form_code`, the agent-written code that draws the
  part once (mm, centred, x length, y height, z width; at most 6,000 bytes),
  which an Imagine studio places with `g.model(name)`. It is refused for the
  same words a studio part's code is. See
  [Imagine stage](imagine-stage.md#library-models-gmodelname).
- A supplier of an entry (`suppliers`, `[{supplier, sku, url}]`) can carry the
  `offer` read from it: `read_on` (`YYYY-MM-DD`) and `via` (the tool or page it
  came from), both required because prices go stale; `currency` (an ISO code)
  with `breaks` (`[{qty, unit_price}]`, quantities rising); `stock`;
  `lead_time_days`; `confirmed`, false for a search snippet or a cached
  page; and `pack` (`{amount, unit, note?}`) when the price is for a roll,
  reel, sheet or bag rather than one piece (units `m`, `cm`, `mm`, `m2`,
  `mm2`, `kg`, `g`, `l`, `ml`, `pcs`). An offer is a dated reading, not a live
  price.
- The Imagine studio links a real part to its entry with the part's `library`
  field, and the Imagine stage can list, read and register components, so a
  part researched in one project is found by the next. See
  [the Imagine stage](imagine-stage.md#real-parts-and-the-component-library).
- `component.search_parametric` searches typed specifications. Without
  `DATABASE_URL`, the gateway and local MCP entry point use SQLite at
  `~/.metaforge/component_catalog.db` (override with
  `METAFORGE_COMPONENT_CATALOG_PATH`). Structured search does not require an
  intent LLM. The catalog starts empty; this change does not invent seed parts.
- `component.search_intent` additionally requires the knowledge service and an
  intent LLM. Its complete-module and individual-part results stay separate.
- `simready.search`, `simready.inspect`, and `simready.import` retrieve upstream
  USD assets. These remain distinct from exact manufacturer-part records.

## Model representations on reusable entries

`library.register_component` and `library.update_component` accept an optional
`models` array. Old entries load with an empty array. Each representation has:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `model_id`, `version` | Stable model identity and immutable content version |
| `kind` | `cad`, `physics`, `emulation`, or `module` |
| `engine`, `engine_versions` | Required engine and optional exact allowed versions |
| `entrypoint` | One of the artifact paths |
| `usage`, `limitations` | How to use it and what it cannot establish |
| `artifacts` | Relative path, public HTTPS URL, SHA-256, exact byte size, license, source revision |
| `dependencies` | Exact `{component, model_id, version}` references |
| `status`, `status_reason` | Active, deprecated, or withdrawn, with reasons for inactive models |

A new model version is required to change content. Older versions must be
retained; mark an unsafe version withdrawn rather than deleting it. The
resolver rejects withdrawn dependencies and emits warnings for deprecated
ones. Current generic-model license policy still applies; this change does
not widen it to every external library license.

For example, after registering an approved model on a component, resolve it:

```json
{
  "ref": "motor",
  "model_id": "motor-physics",
  "version": "1.0.0",
  "engine": "mujoco",
  "engine_version": "3.0",
  "fetch": false
}
```

Pass that object to `library.resolve_component_model`. It returns the complete
resolved metadata, usage/limitations, artifact hashes and a deterministic lock
hash. Set `fetch: true` to verify and copy the assets to an isolated folder in
`ADAPTER_WORKSPACE_DIR` (default `/workspace`). Set `offline: true` with fetching
to prohibit upstream artifact downloads; catalog access may still use the
configured gateway. A miss or corrupt cached object is an explicit error.
The tool resolves files, never executes their code. Pass returned entrypoint
paths to an appropriate installed engineering adapter separately. It is an
on-demand tool (`chat_visible: false`); clients can discover it through tool
search without adding its schema to every default chat prompt.

All metadata, license, engine, dependency-cycle and total-size checks run
before downloads. The initial graph limit is 100 representations and 200 MiB.
The lockfile records the actual selected model definitions and artifacts; it
does not claim to discover undeclared runtime imports inside executable code.
A future replay must resolve the pinned metadata and check current withdrawal
policy, not silently select a newer model.

## Viewing a maker's model as a mesh

`GET /v1/library/components/{ref}/mesh` turns an entry's active STEP model into a mesh
the Imagine studio and the library viewer can draw. The gateway fetches the artifact once,
checks its size and sha256, takes the member named by the artifact's `path` from a zip,
and sends the STEP to the OCCT converter's `POST /faces` route.

That route reads the STEP through OCCT's XDE layer and gives every face the colour the
file states for it: the face's own style first, then the placed instance's, the part's,
and the enclosing assembly's. Colours are returned as the sRGB values the STEP writes. A
face with no stated colour comes back with `colour: null` and `colour_source: "none"`;
it is never painted a default grey. The converter's GLB route (`/convert`) keeps one
colour per part, so it is not used here.

The model's `up_axis` (`x`, `y`, `z` or their negatives; `z` when absent) is turned to the
studio's +Y, the mesh is centred, and a model over 20,000 triangles is reduced by vertex
clustering. Each part in the response carries `name`, `colour`, `colour_source`,
`positions` (mm) and `indices`. Meshes are cached by artifact digest, up axis and recipe
version under `METAFORGE_LIBRARY_MESH_DIR` (default `~/.metaforge/library-meshes`).

### A model inside a larger maker file

Some makers publish a part's 3D model only inside a larger file. Nordic ships its chips'
bodies inside the Altium libraries of its reference layouts, and Raspberry Pi its chips
only as named parts of a board's STEP. The artifact stays pinned to the maker's own HTTPS
file and its sha256, and says how to reach the model:

| Field | Meaning |
|---|---|
| `embedded` | Stream paths, walked in order through OLE compound files (an Altium IntLib, PcbLib or PcbDoc); each stream is inflated when it is zlib data. Nordic's nRF52840: `["PCBLib/0.pcblib", "Library/Models/6"]`. |
| `part` | The product name to keep from an assembly STEP, matched at any depth without regard to case. The converter's `/faces?part=` reads that product alone, at its own origin. |
| `extracted_sha256` | Optional pin on the STEP that comes out, so a changed library is refused. |

Meshes of different selections from one file are cached apart. An artifact with
`embedded` or `part` is for viewing only: `library.resolve_component_model` refuses it
whatever its licence, because the file it pins is not the part's model on its own.

The route answers 404 for an unknown entry and 422 with the reason when the entry has no
active STEP model, the file changed upstream, the embedded stream or named part is not
there, or the converter is down or refuses the file. Showing a model here does not clear its licence: a model whose licence MetaForge
does not accept is still refused by `library.resolve_component_model`, which is the route
into engineering tools and deliverables.

## Organisations: manufacturers, distributors, fabricators and test labs

A company is recorded once, however it is written. An organisation entry has a title (the
name it trades as), `roles` (one or more of `manufacturer`, `distributor`, `fabricator`,
`test_lab`), `aliases` (every other spelling, such as `TI` for Texas Instruments), a
`status` (`unreviewed`, `approved`, or `blocked` with a `status_reason`), `country` (ISO
3166 two-letter), the `regions` it serves (UK, EU, Europe, Asia, Americas, Oceania, Africa,
Global), `website`, `quote_url`, `certifications`, `contacts` (by role: a page or a shared
address, never a person's own details), `lifecycle_policy` and `connector` (the vendor
connector that quotes from it, when one exists). Every change is a new version.

Names are matched in one form: lower case, punctuation and a trailing legal form (Inc, Ltd,
GmbH and the like) removed, so `Texas Instruments Inc.` and `texas instruments` are the same
key; a short form such as `TI` is an alias. Two organisations may not answer to the same key.
`GET /v1/library/organisations/resolve?name=...` (and `library.get_organisation`, which
takes any spelling) returns the organisation a written name refers to.

Other entries point at organisations by id: a component's `manufacturer_org`, the `org` of
each offer in its `suppliers`, and a service bureau's machine instance `operator_org`. A
component is linked when it is written and its text resolves, and again when an organisation
it names is added later; the text fields stay as written. Export and import carry the
organisations first and rewrite the links to the receiving library's ids.

`POST /v1/library/organisations/migrate` builds organisations from the names the library
already holds: each component's maker, each offer's supplier, and the company in a bureau
instance's name (the text before `:`). Without `apply=true` it only reports what it would
create, merging spellings that match and a short list of well-known short forms, and lists
related names it left apart for a person to decide (`Adafruit` and `Adafruit (made by ...)`;
`ROHM` the semiconductor maker and `Rohm` the acrylic maker share a key only by accident of
spelling, which is why qualifiers are never stripped). With `apply=true` it creates them as
`unreviewed`, with no country guessed, the bureaus' recorded locations in their notes, and
links components, offers and bureaus. Running it again proposes only what is new.

Where organisations change what happens: a component selection records the maker's and
distributor's organisations and flags a blocked one (see the capability matrix), the BOM
identity uses the maker's canonical name, and the Imagine studio's cost never uses a blocked
seller's offer and prefers a seller serving the home region (UK, with the home currency GBP)
or everywhere; a part sold only by blocked sellers is left out of the estimate with a note
saying why. Code:
`twin_core/library/organisations.py`, `twin_core/library/organisation_migration.py`.

## Finding a component: the library first, then the distributors

`component.find` takes a need in a few words (a part's job and key limits, or a part
number) and searches, in one call, the library (with `library.search`) and every configured
distributor (DigiKey, Mouser, Nexar, Farnell, Arrow). It returns two ranked lists:

- `library`: matching library parts, each with the name to link it by, its maker and order
  code, and `blocked` when its maker or every seller is blocked;
- `distributors`: live results, in production first, then in stock, then with a datasheet,
  each with its order code, maker, description, stock, lead time, datasheet and product page,
  marked `in_library` when the library already holds that order code, and listing what is
  still `unknown` (size, pins, price).

- `web`: when the library finds nothing and the distributors fewer than three (`web: "auto"`,
  the default; `"on"` always, `"off"` never), pages from the configured web search
  (`BRAVE_API_KEY`) for the need plus "datasheet": each a title, address and snippet, marked
  as a `datasheet` when it is a PDF, with the maker, order code, size, pins and price all
  `unknown` until the page is read. A part found only on the web (a board its maker sells
  directly) is registered with `library.register_component`, every value sourced to the page,
  since `component.adopt` records a distributor's offer.

`searched` says what was searched and how each source answered (how many it found, or why it
could not: no credentials or no web search key, a timeout, an error). Nothing found is a
normal answer.
`region` limits library parts to those with a seller serving that region; `distributors:
false` searches only the library.

`component.adopt` registers a chosen distributor part in the library: its maker, order code,
title, datasheet and `family`, and the distributor's live price breaks and stock as a dated
offer (`read_on` today, `via` the distributor's API). Sizes and interfaces go in only when
the caller passes them with their source, read from the datasheet; nothing is guessed. A part
the library already holds is refused, with a pointer to the entry. It is a write, held for
the person's approval like any library registration.

`component.search_intent` also returns `library`: the library's own parts matching the
need, tried with the whole text and then its longest words alone. The find-component
playbook (`mcp_core/plugin_skills/find-component`) gives agents the steps: find, prefer the
library, read the datasheet, adopt once, record the choice. The Imagine stage carries
`component.find` in place of `library.list_components`, which listed the whole library.
Code: `tool_registry/tools/components/finder.py`.

## Shared entries and your own

On a gateway shared by several accounts (a hosted MetaForge), the library has
two layers: shared entries, curated for everyone and changed only by the
deployment (`POST /v1/library/{kind}/{id}/publish`), and each account's own,
which only that account sees and changes. Every listing, every read and every
search result carries `shared` (`true` for the shared layer, `false` for the
caller's own). An account may hold its own entry under a shared entry's name;
both are listed, each marked.

The dashboard's Library page marks each entry **Shared** or **Yours**, filters
by **All / Shared / Yours** with how many of each, and says on an entry what its
layer means: a shared one is read-only, and an account changes it for itself by
registering its own version under the same name. Components are picked by id,
so two entries with one name stay apart. A local MetaForge has one library and
every entry comes back shared; the page shows none of this there.

## Searching the library

`GET /v1/library/search?q=...` (and the `library.search` tool) searches every kind at once:
components (name, MPN, title, maker and every spelling of its organisation, suppliers and
their SKUs, notes), organisations (title, aliases, roles, country, regions,
certifications), materials, processes, machines (make, model, name, kind, processes,
location, and the operator's organisation), appearances and ergonomic limits. Every word of
`q` must match some field; a word matching a whole field word counts fully, one starting a
field word 0.7, one inside a field word 0.4, weighted by the field (an identifier such as a
name, MPN, SKU or alias above a title, a title above a linked name, a linked name above a
description). Results come best first, each naming the fields that matched, so a person can
see why it is there; nothing is fuzzy or learned.

`kind` (repeatable) narrows the kinds. `role`, `region` and `status` filter organisations and
what is linked to one: a component passes when its maker or a seller passes, a machine when
its operator does (your own in-house machines pass a region or role filter, not a status
filter), and kinds with no organisation (materials, processes, appearances, limits) drop out
under any of them. An organisation serving `Global` passes every region. A component whose
maker is blocked, or whose every seller is blocked, carries `blocked` with the reason. `limit`
is 50 by default and 200 at most. Code: `twin_core/library/search.py`.

## Saving and loading the whole library

The catalog and the machine library live in one SQLite file on the
gateway's machine (`METAFORGE_MACHINE_LIBRARY_PATH`, default
`~/.metaforge/machine_library.db`). `GET /v1/library/export` writes every
current entry of every kind (organisations, processes, materials, components,
appearances, ergonomic limits) and every machine as one JSON document
(`format: metaforge-library`, `version: 1`); `?history=true` adds each
entry's versions. A machine's connection carries only the name of the
environment variable that holds its credential, so the file holds nothing
secret.

`POST /v1/library/import` loads such a document and only adds: an entry
whose name is already there (the same kind and name for a catalog entry,
the same kind, make and model for a machine model, the same name for a
machine instance) is skipped and counted, never overwritten. An imported
entry starts again at version 1, recorded as made by the import; the
exported history stays in the file as the record. A machine instance's
`model_id` is pointed at its model in the new library. The answer lists
what was added, what was skipped and anything refused with the reason. The
dashboard's `/library` page has **Export** and **Import** buttons for both.

`GET /v1/library/engine/{name}` serves the drawing engine (`authored.js`)
that runs an entry's `form_code`, the same file the Imagine studio loads,
so a page that shows the library needs no project.

## Download integrity and cache

Shared `VerifiedAssetCache` objects live under the adapter workspace's
`.asset-cache`. Each object is keyed by SHA-256, verified on reuse, and copied
into jobs. A job cannot mutate the cached object through a hard link. Concurrent
requests in one worker coordinate per digest. HTTP downloads stream through
actual byte counters; declared sizes are not trusted as the only limit.
Completed files become visible atomically. Upstream errors, oversize bodies,
incorrect lengths and hash failures leave prior valid outputs intact.

HTTP sources are restricted to approved public registry/CDN host families;
each redirect is rechecked. Metadata is capped at 8 MiB; transient HTTP status
retries are bounded. The downloader does not attach provider credentials.
Private/gated provider authentication is outside this implementation.

SimReady imports use publisher/dataset/asset/full-revision identity rather
than sanitized display names and eight-character commit prefixes. Unique
staging directories prevent imports from deleting each other's work. Existing
legacy folders and catalog IDs are not renamed. Import metadata retains the
legacy library name for reconciliation; there is no automatic merge of
potentially ambiguous legacy entries. Different texture selections have
separate output folders. A per-import `model.lock.json` records the downloaded
USD/texture set; it is not a universal simulation dependency declaration.

## Discover installed KiCad assets

Two on-demand read tools inspect the already-installed adapter libraries:

- `kicad.search_library`: `{ "query": "Device:R", "limit": 10 }`.
- `kicad.describe_library_component`: `{ "symbol_id": "Device:R" }`.

Descriptions flatten inherited symbols, list pins and assigned footprints,
resolve configured 3D-model references, and report a source-file checksum.
Absent geometry is explicit: the adapter image deliberately keeps only selected
3D-model families. Symbols can be generic and are not proof of a purchasable
part or of simulation/emulation support. These tools read local supported
KiCad library files; they do not crawl GitLab or republish KiCad assets.

## Persistence and verification boundaries

Model resolution and workspace caching do not replace project persistence.
Continue to record imported deliverables with `twin.record_design_files`,
using `purchased_model` for SimReady imports, and handle any `store_error`
before treating binaries as durably stored. Existing MinIO paths stay intact.
Evidence must identify the selected lock hash, engine build and run inputs.
Verification findings do not silently edit a selected design.

## Remaining rollout work

Remote pinned KiCad synchronization and source-specific license handling,
reviewed GitHub Release publishing, automatic cross-catalog synchronization,
cache quota/eviction, multi-worker download coordination, and durable recovery
coordination across catalog/Twin/object-store writes remain separate rollout
steps. There is no new private registry, new emulator, or hosted storage
service in this change.
