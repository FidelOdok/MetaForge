# Tier-1 — multi-tool synthesis happy path (HP-SYN)

Validates: MET-417 (sub-deliverable 8/10 of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario synth-hp`

Ten happy-path scenarios that exercise multi-tool reasoning
chains. **This is where the external-harness moat (ADR-008) is
validated** — individual tools may all pass but composition is
the user-visible value.

Each scenario gives Claude a natural-language prompt and grades
both the tool-selection sequence and the final synthesized
answer. No scenario assumes a specific tool order beyond what's
called out in the **Then** clause; alternative orderings that
arrive at the same answer with the same trace shape PASS.

---

## Scenario: HP-SYN-01 — find_by_property → thread_for chain
Validates: MET-382
Tier: 1

### Given
- A seeded BOMItem for STM32H7 with downstream test/requirement
  edges.

### When
1. Prompt: *"Find the BOMItem for STM32H7 then walk its thread."*

### Then
- The trace shows ≥ 2 tool calls: a `twin.find_by_property`
  followed by a `twin.thread_for` against the matched node id.
- The final answer references both the BOMItem and at least
  one downstream Requirement or TestExecution.

---

## Scenario: HP-SYN-02 — find_by_property → knowledge.search (cross-store)
Validates: MET-382 + MET-346
Tier: 1

### Given
- A seeded BOMItem and at least one design-decision chunk that
  explains why that part was chosen.

### When
1. Prompt: *"Find this part and explain why it was chosen."*

### Then
- The trace shows a Twin lookup followed by a knowledge search.
- The final answer cites the design-decision chunk by source
  path.

---

## Scenario: HP-SYN-03 — constraint.validate → knowledge.search for remediation
Validates: MET-383 + MET-346
Tier: 1

### Given
- A project with one error-severity violation. A knowledge
  chunk documents the remediation pattern.

### When
1. Prompt: *"Why does this violation matter and how do we fix it?"*

### Then
- The trace shows `constraint.validate` followed by
  `knowledge.search` keyed on the violation type.
- The final answer combines the constraint detail with the
  knowledge-base remediation reference.

---

## Scenario: HP-SYN-04 — cadquery.create_parametric → twin.get_node round-trip
Validates: MET-340 + MET-382
Tier: 1

### Given
- (no specific seed)

### When
1. Prompt: *"Generate a 50×30×10mm aluminum part then show me its
   graph node."*

### Then
- The trace shows a CAD generation followed by a `twin.get_node`
  lookup.
- The Twin node properties match the generation parameters
  (dimensions, material).

---

## Scenario: HP-SYN-05 — knowledge.ingest → knowledge.search round-trip
Validates: MET-346
Tier: 1

### Given
- A markdown blob with a unique-token phrase.

### When
1. Prompt: *"Ingest this memo, then search for ‘<unique-token>’."*

### Then
- The trace shows ingest → search in the same session.
- The search finds the just-ingested chunk.

---

## Scenario: HP-SYN-06 — thread_for → constraint.validate (scoped)
Validates: MET-382 + MET-383
Tier: 1

### Given
- A Requirement with a downstream subgraph.

### When
1. Prompt: *"Walk the thread from this requirement, then validate
   constraints on what you found."*

### Then
- The trace shows `twin.thread_for` followed by
  `constraint.validate`.
- The validation scope (subgraph or filter) is informed by the
  thread output, not the whole project.

---

## Scenario: HP-SYN-07 — three-store synthesis
Validates: MET-382 + MET-346 + telemetry
Tier: 1

### Given
- A seeded failure scenario that touches twin nodes, knowledge
  chunks, and (mock) telemetry.

### When
1. Prompt: *"Investigate this failure: use graph, docs, and
   sensor data."*

### Then
- The trace shows ≥ 3 tool calls touching at least 3 distinct
  stores (twin, knowledge, telemetry).
- The final answer is internally coherent — no contradictions
  between sources.

---

## Scenario: HP-SYN-08 — citation chain (search → resource → MinIO)
Validates: MET-346 + MET-384
Tier: 1

### Given
- A PDF datasheet ingested into knowledge with citations
  preserved.

### When
1. Prompt: *"Search for thermal management notes, then open the
   source PDF for the top hit."*

### Then
- The trace shows `knowledge.search` followed by a
  `resources/read` (or download) on the source PDF.
- The MinIO presigned URL is returned and is reachable.

---

## Scenario: HP-SYN-09 — reconciliation: extract_requirements → propose
Validates: MET-351 (extract_requirements skill, ADR-009)
Tier: 1

### Given
- An SRS PDF available either via a local path or via
  `resources/read`. (If the skill is not deployed in the test
  env, this scenario reports BLOCKED.)

### When
1. Prompt: *"Extract requirements from this SRS PDF."*

### Then
- The trace shows the reconciliation skill being invoked.
- Requirement proposals are queued (visible in the
  `/approvals` route or via the proposal queue API) with
  citations and confidence scores attached.

---

## Scenario: HP-SYN-10 — context preserved across multi-hop chain
Validates: MET-386 + MET-387
Tier: 1

### Given
- A prompt that requires ≥ 5 tool calls.

### When
1. Run a complex prompt (e.g. *"Find this part, walk its thread,
   search docs about it, validate constraints, generate a CAD
   variant"*).

### Then
- All spans share a single root `correlation_id`.
- `ctx.session_id` is the same across every call in the chain
  (no session re-handshake).

---

## Acceptance

- All 10 scenarios PASS.
- Each scenario produces a coherent Claude answer with citations
  where applicable.
- No tool-selection errors (Claude picks the right tool from the
  description alone — no hand-holding).
- Report committed.
