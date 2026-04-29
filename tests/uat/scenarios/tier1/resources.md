# Tier-1 — MCP `resources/*` scenarios

Validates Cycle 3 / MET-384 — the read-only resources surface
exposed via `resources/list` + `resources/read` — from a
Claude-as-real-user perspective. Run on Cycle gates.

Every scenario assumes the MetaForge MCP server is connected and
the server registers at least one resource via `register_resource`
(adapter-side resource registrations land in follow-up tickets;
this corpus runs against whatever resources the live server
publishes).

URI scheme: `metaforge://<adapter>/<path>`. The default scheme
allowlist is `("metaforge",)` — anything else returns
`RESOURCE_NOT_FOUND` (-32004).

---

## Scenario: resources/list returns at least one manifest
Validates: MET-384 (`resources/list`)
Tier: 1

### Given
- A bootstrapped MCP server with at least one resource
  registered.

### When
1. Call `resources/list` with no params.

### Then
- Response shape is `{ "resources": [...] }`.
- `len(data.resources) >= 1`.
- Each manifest has `uri_template`, `name`, `description`,
  `mime_type`, and `adapter_id`.
- Every `uri_template` starts with `metaforge://` — server
  defaults to the canonical scheme.

---

## Scenario: resources/list filter by adapter_id
Validates: MET-384 (filter)
Tier: 1

### Given
- A server with resources registered under at least two
  distinct adapter_ids.

### When
1. Call `resources/list` with `{ "adapter_id": "<one_adapter>" }`.

### Then
- Every returned manifest has `adapter_id == "<one_adapter>"`.
- Calling with `{ "adapter_id": "definitely-not-real" }` returns
  `{ "resources": [] }` — empty, not an error.

---

## Scenario: resources/read returns content for a known URI
Validates: MET-384 (`resources/read` happy path)
Tier: 1

### Given
- A concrete URI matching one of the registered manifests, e.g.
  the URI template substituted with a real id.

### When
1. Call `resources/read` with `{ "uri": "<concrete_uri>" }`.

### Then
- Response shape is `{ "contents": [...] }`.
- `len(data.contents) >= 1`.
- Each content entry carries `uri`, `mime_type`, and at least
  one of (`text`, `blob_base64`).
- The first entry's `uri` matches the input URI exactly.

---

## Scenario: resources/read on unknown URI returns RESOURCE_NOT_FOUND
Validates: MET-384 (error code -32004)
Tier: 1

### Given
- A URI for an adapter that exists but a path no registration
  matches: e.g. `metaforge://<known_adapter>/does-not-exist/x`.

### When
1. Call `resources/read` with that URI.

### Then
- Response carries `error` (no `result`).
- `error.code == -32004`.
- `error.data.uri` echoes the input URI.

---

## Scenario: resources/read with missing uri returns RESOURCE_READ_ERROR
Validates: MET-384 (input guard)
Tier: 1

### Given
- A `resources/read` call without the `uri` param.

### When
1. Call `resources/read` with `{}`.

### Then
- Response carries `error`.
- `error.code == -32005`.
- `error.message` references "uri is required" or similar.

---

## Scenario: foreign-scheme URI is rejected
Validates: MET-384 (scheme allowlist)
Tier: 1

### Given
- A URI using a non-`metaforge://` scheme: e.g.
  `http://example.com/x` or `file:///etc/passwd`.

### When
1. Call `resources/read` with the foreign-scheme URI.

### Then
- Response carries `error`.
- The graph / filesystem is **untouched** — server did not
  follow the URI to a foreign target.
- Error code is `-32004` (RESOURCE_NOT_FOUND, since no adapter
  matches) or `-32005` (READ_ERROR if the scheme guard
  short-circuits).

---

## Scenario: tool/* methods are unaffected by the resources surface
Validates: MET-384 (regression — separate dispatch paths)
Tier: 1

### Given
- A live server with tools and resources both registered.

### When
1. Call `tool/list`.
2. Call `resources/list`.

### Then
- Step 1's `tools` list and Step 2's `resources` list have no
  overlap — tools and resources occupy distinct namespaces.
- Both calls succeed; neither's installation path interferes
  with the other.
