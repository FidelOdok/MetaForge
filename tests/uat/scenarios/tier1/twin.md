# Tier-1 — `twin.*` scenarios

Validates Cycle 3 / MET-382 — the Twin MCP tool group — from a
Claude-as-real-user perspective. Run on Cycle gates.

Every scenario assumes the MetaForge MCP server is connected, a
`TwinAPI` instance is wired into the bootstrap (so the `twin`
adapter is registered), and the five tools — `twin.get_node`,
`twin.thread_for`, `twin.find_by_property`,
`twin.constraint_violations`, `twin.query_cypher` — appear in
`tool/list`.

---

## Scenario: tool/list reports all five twin tools
Validates: MET-382 (registration)
Tier: 1

### Given
- A bootstrapped MCP server with the twin adapter enabled.

### When
1. Call `tool/list` (no filter).

### Then
- The result includes manifests for **all five** of:
  `twin.get_node`, `twin.thread_for`, `twin.find_by_property`,
  `twin.constraint_violations`, `twin.query_cypher`.
- Each manifest has `adapter_id == "twin"` and a non-empty
  `description` — the description is what the LLM uses to pick
  the tool, so it must be present.

---

## Scenario: get_node returns root + first-hop neighbours
Validates: MET-382 (`twin.get_node`)
Tier: 1

### Given
- A node id `<root_id>` known to exist in the twin (any
  `WorkProduct`, `Requirement`, or `BOMItem`).

### When
1. Call `twin.get_node` with `{ "node_id": "<root_id>" }`.

### Then
- Response shape is `{ "node": {...}, "neighbours": [...],
  "edges": [...] }`.
- `data.node.id == "<root_id>"`.
- `data.node` carries `name` (or other concrete-type fields) —
  the per-node serialiser preserves subclass fields.
- `data.neighbours` and `data.edges` are arrays (may be empty
  if the node is isolated, but they must exist).

---

## Scenario: get_node rejects malformed node_id
Validates: MET-382 (input validation)
Tier: 1

### Given
- The string `"not-a-uuid"`.

### When
1. Call `twin.get_node` with `{ "node_id": "not-a-uuid" }`.

### Then
- The JSON-RPC response carries an `error` field (no `result`).
- `error.code` is `-32001` (TOOL_EXECUTION_ERROR) or
  `-32602` (INVALID_PARAMS) — either is acceptable.
- The error message mentions `node_id` or `UUID`.

---

## Scenario: thread_for walks the digital thread
Validates: MET-382 (`twin.thread_for`)
Tier: 1

### Given
- A node id `<requirement_id>` for a Requirement that has
  downstream DesignElements / Tests / Evidence.

### When
1. Call `twin.thread_for` with `{ "node_id": "<requirement_id>",
   "depth": 3 }`.

### Then
- Response carries `nodes`, `edges`, `root_id`, `depth`.
- `data.root_id == "<requirement_id>"`.
- `data.depth == 3`.
- `len(data.nodes) >= 1` (at minimum, the root itself).
- Every edge has `source_id` and `target_id` referencing nodes
  in `data.nodes`.

---

## Scenario: find_by_property locates an indexed BOM item
Validates: MET-382 (`twin.find_by_property`)
Tier: 1

### Given
- A `WorkProduct` (or BOMItem-like node) with a known property
  value, e.g. `name == "drone-fc-pcb"`.

### When
1. Call `twin.find_by_property` with `{ "node_type":
   "WorkProduct", "property": "name", "value": "drone-fc-pcb",
   "limit": 5 }`.

### Then
- Response carries `nodes` (array) and `count` (int).
- At least one returned node has `name == "drone-fc-pcb"`.
- `count <= limit`.

---

## Scenario: find_by_property rejects malicious node_type
Validates: MET-382 (label injection guard)
Tier: 1

### Given
- A node_type containing characters disallowed by the label
  validator (regex `^[A-Za-z][A-Za-z0-9_]*$`), e.g. ``"WorkProduct
  ` MATCH (n) DETACH DELETE n //"``.

### When
1. Call `twin.find_by_property` with that node_type.

### Then
- Response carries an `error` (no `result`).
- The graph is **unchanged** — confirm via a follow-up
  `twin.query_cypher` `RETURN count{(n)} AS total` matching
  the pre-call total.

---

## Scenario: constraint_violations returns severity-ordered list
Validates: MET-382 (`twin.constraint_violations`)
Tier: 1

### Given
- A project with at least one open constraint violation
  (or none, in which case empty result is acceptable).

### When
1. Call `twin.constraint_violations` with `{ "branch": "main" }`.

### Then
- Response shape matches `ConstraintEvaluationResult`: it has
  fields `passed`, `evaluated_count`, and an array of
  violations (when not passed).
- If violations exist, each has `severity`, `rule_id`, and
  `message` fields.

---

## Scenario: query_cypher accepts read-only queries by default
Validates: MET-382 (`twin.query_cypher` read-only path)
Tier: 1

### Given
- Bootstrap with the default `twin_allow_mutations=False`.

### When
1. Call `twin.query_cypher` with `{ "cypher":
   "MATCH (n) RETURN count(n) AS total" }`.

### Then
- Call returns successfully.
- `data.rows` is an array of length 1; `rows[0]` carries `total`.

---

## Scenario: query_cypher rejects mutations when allow_mutations is off
Validates: MET-382 (mutation gate)
Tier: 1

### Given
- Bootstrap with default `twin_allow_mutations=False`.

### When
1. Call `twin.query_cypher` with `{ "cypher":
   "CREATE (n:UATProbe {marker: 'should-not-land'}) RETURN n" }`.

### Then
- The JSON-RPC response carries an `error` field.
- The error message references mutations / `CREATE` / a
  read-only restriction.
- A follow-up read query confirms the node was **not** created:
  `MATCH (n:UATProbe {marker: 'should-not-land'}) RETURN count(n)
  AS c` returns `c == 0`.

---

## Scenario: query_cypher mutation detector ignores property-name false positives
Validates: MET-382 (token-bounded mutation regex)
Tier: 1

### Given
- A read-only query that **mentions** `created_at` (a property
  whose name contains the substring `CREATE`).

### When
1. Call `twin.query_cypher` with `{ "cypher":
   "MATCH (n) RETURN n.created_at LIMIT 1" }`.

### Then
- Call returns successfully — the detector recognises that
  `n.created_at` is a property reference, not the `CREATE`
  keyword.
- `data.rows` exists (may be empty if no nodes have
  `created_at`, but the call did not trip the mutation gate).
