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
  battery's `capacity_mah` is a typed spec. Entries saved before these fields
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
  `lead_time_days`; and `confirmed`, false for a search snippet or a cached
  page. An offer is a dated reading, not a live price.
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
