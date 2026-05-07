# Tier-1 — event-driven ingest scenarios (KB-EVT)

Validates: MET-410 (sub-deliverable F1f of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario event-ingest`

Three scenarios that promote the §4 (Event-driven ingest) catalog
rows from `docs/uat/kb-test-plan.md` into the executable
`## Scenario:` shape. They exercise the Kafka → `KnowledgeConsumer`
path (`digital_twin/knowledge/consumer.py`) end-to-end from a
Claude-as-real-user perspective.

> **Note:** Every scenario in this file requires the Kafka broker
> and `KnowledgeConsumer` to be live. Match the BLOCKED-vs-FAIL
> discipline established in `tier1/ingest.md` HP-INGEST-10: if the
> broker isn't running, the scenario reports BLOCKED, not FAIL.
> KB-EVT-002 is authored ahead of an end-to-end test for the
> existing `_WORK_PRODUCT_TYPE_MAP` (consumer.py:29-35) auto-classify
> path. KB-EVT-003 depends on the L1-A6 supersede semantics being
> wired through the Kafka event handler. KB-EVT-004 depends on a DLQ
> behaviour that is not yet implemented — it will report BLOCKED
> until the consumer learns to route malformed events to a DLQ
> instead of silently dropping or poison-pilling the partition.

---

## Scenario: KB-EVT-002 — consumer auto-classifies by `work_product_type`
Validates: MET-307
Tier: 1

### Given
- The Kafka broker and `KnowledgeConsumer` are running
  (otherwise: BLOCKED).
- A unique `work_product_id` UUID and a unique
  `source_path = work_product://<that-uuid>` (the virtual URI the
  consumer derives via `_source_path_for`).
- A `WORK_PRODUCT_CREATED` event payload with
  `work_product_type="design_decision"` and a textual `content`
  field whose body contains a unique-token phrase (e.g.
  `evt-002-marker-D7`).

### When
1. Publish the `WORK_PRODUCT_CREATED` event onto the Twin event
   bus / Kafka topic the `KnowledgeConsumer` subscribes to.
2. Within 5 seconds, call `knowledge.search` for
   `evt-002-marker-D7` with filter
   `knowledge_type="design_decision"` and `top_k=1`.
3. Repeat the same search but with filter
   `knowledge_type="failure"`.

### Then
- Step 2 returns ≥ 1 hit whose `source_path` equals the
  `work_product://<uuid>` URI.
- Step 3 does **not** return that hit (auto-classify routed the
  payload via `_WORK_PRODUCT_TYPE_MAP` at `consumer.py:29-35` to
  `KnowledgeType.DESIGN_DECISION`, not `KnowledgeType.SESSION` or
  `KnowledgeType.FAILURE`).
- A `knowledge_consumer_indexed` log line in Loki carries
  `knowledge_type=KnowledgeType.DESIGN_DECISION` and the same
  `event_id` published in step 1.

---

## Scenario: KB-EVT-003 — update event supersedes prior ingest
Validates: MET-307
Tier: 1

### Given
- The Kafka broker and `KnowledgeConsumer` are running
  (otherwise: BLOCKED).
- A single `work_product_id` UUID reused across two events
  (so both resolve to the same `work_product://<uuid>` source
  path that the consumer's `delete_by_source` predelete keys on).
- Two distinct content payloads:
  - α = `"evt-003-alpha-marker — superseded payload"`.
  - β = `"evt-003-beta-marker — fresh payload"`.

### When
1. Publish a `WORK_PRODUCT_CREATED` event for the
   `work_product_id` with content α.
2. Wait until the consumer logs `knowledge_consumer_indexed` for
   the event id from step 1.
3. Publish a `WORK_PRODUCT_UPDATED` event for the **same**
   `work_product_id` with content β.
4. Within 5 seconds of step 3, search for `"evt-003-alpha-marker"`
   with `top_k=10`.
5. Search for `"evt-003-beta-marker"` with `top_k=10`.

### Then
- Step 4 returns 0 hits, OR every hit has
  `similarity_score < 0.5` at the `work_product://<uuid>` source
  (α has been retired by the predelete on update).
- Step 5 returns ≥ 1 hit whose `source_path` equals
  `work_product://<uuid>`.
- A `knowledge_consumer_predelete` log line in Loki appears for
  that `source_path` between steps 3 and 5, with a non-zero
  `deleted` count (consumer.py:96-100).

---

## Scenario: KB-EVT-004 — malformed event drops to DLQ, not poison-pill
Validates: MET-307, MET-385
Tier: 1

### Given
- The Kafka broker and `KnowledgeConsumer` are running
  (otherwise: BLOCKED).
- A DLQ topic configured for the consumer (otherwise: BLOCKED —
  DLQ behaviour is not yet implemented; the consumer currently
  logs `knowledge_consumer_error` and returns, without explicitly
  routing the bad message anywhere).
- Two events queued back-to-back:
  - A malformed `WORK_PRODUCT_CREATED` event missing required
    fields (e.g. no `content`/`description`/`name`/`summary`/`text`
    and no `properties`, so `_extract_content` returns `""`; or a
    non-dict `data` payload that breaks the consumer's contract).
  - A well-formed `WORK_PRODUCT_CREATED` event with content
    `"evt-004-survivor-marker"` and `work_product_type="component"`.

### When
1. Publish the malformed event.
2. Publish the well-formed event onto the same partition.
3. Within 10 seconds, search for `"evt-004-survivor-marker"`
   with `top_k=1`.

### Then
- Step 3 returns ≥ 1 hit (the well-formed event was processed —
  the malformed one did not poison the partition or stall the
  consumer).
- The malformed event lands on the DLQ topic with the original
  payload and an error reason (or, until the DLQ ships, a
  `knowledge_consumer_error` log line in Loki carries the
  malformed event's `event_id` and an `error` attribute).
- The malformed event's content is **not** searchable — no
  silent ingest of a payload the consumer could not classify.

---

## Acceptance

- All 3 scenarios PASS in a single `/uat-cycle12 --tier 1
  --scenario event-ingest` invocation when the broker, the
  `KnowledgeConsumer`, and the DLQ topic are all live.
- Scenarios report BLOCKED (not FAIL) when Kafka or the consumer
  is unavailable, matching the HP-INGEST-10 precedent.
- Report committed under `docs/uat/uat-claude-driven-report-<date>.md`.
