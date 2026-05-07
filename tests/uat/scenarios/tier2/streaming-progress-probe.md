# Tier-2 — streaming progress probe (MET-410, F1d)

Validates: MET-410 (sub-deliverable F1d of MET-409).
Tier: 2
Run: `/uat-cycle12 --tier 2 --scenario streaming-progress`

Three scenarios that promote the ✅-tagged catalog rows
KB-PRG-001, KB-PRG-002 and KB-PRG-003 from `docs/uat/kb-test-plan.md`
§6 (Streaming progress) into executable tier-2 form. Each scenario
asserts a different facet of the MET-388 streaming-progress contract
on the MCP wire surface (multi-file ingest progress notifications,
request-id correlation, and `supports_progress` capability
advertisement on `tools/list`).

All three are backed by L1-B2 (PR #170, merged) — covered by
`tests/integration/test_knowledge_streaming_progress.py` — and should
record ✅ PASS on the next `/uat-cycle12 --tier 2 --scenario
streaming-progress` run.

> **Note:** Scenarios use unique source paths under
> `uat://kb/streaming-progress/<id-suffix>` so they do not collide
> with tier-1 fixtures or other tier-2 probe blocks. The probe focuses
> on the MCP wire shape (notification frame received, monotonically
> advancing `progress`, `request_id` round-trip, and the
> `supports_progress` boolean on the tool entry) rather than
> Loki/Prometheus telemetry — the streaming surface is wire-level
> rather than metrics-level.

---

## Scenario: KB-PRG-001 — multi-file ingest emits ≥ 1 progress notification
Validates: MET-388
Tier: 2

### Given
- The MetaForge MCP server is connected and `tools/list` includes
  `knowledge.ingest` (or `mcp__metaforge__knowledge_ingest`).
- The runner has wired a progress-notification sink that captures
  every `notifications/progress` frame for the in-flight tool call
  (per L1-B2, `tests/integration/test_knowledge_streaming_progress.py
  ::TestProgressOnMultiFileIngest::test_knowledge_ingest_emits_progress_per_file`).
  **If the runner cannot capture progress frames, the scenario
  records BLOCKED with the message `"progress sink not wired"` —
  not FAIL.**
- ≥ 5 distinct source paths under
  `uat://kb/streaming-progress/prg-001-file-{0..4}` and inline
  content for each.

### When
1. Call `knowledge_ingest` with a `files=[...]` batch of 5 entries
   (each with `content`, `source_path`, and
   `knowledge_type="design_decision"`) and a `request_id` of
   `"prg-001-batch"`.
2. Capture every `notifications/progress` frame received between
   the request being sent and the final tool-call response.

### Then
- Step 1 returns a success response whose `data.files_ingested == 5`
  and whose `data.chunks_indexed >= 5`.
- ≥ 1 progress notification is captured before the final response
  arrives — for a 5-file batch the expected count is exactly 5,
  one per file.
- The sequence of `progress` values is monotonically non-decreasing
  and every value falls in the half-open interval `(0.0, 1.0]`.
- The final captured progress value is `1.0` (or
  `pytest.approx(1.0)` to tolerate float drift) — the last
  notification fires before the success frame.

---

## Scenario: KB-PRG-002 — progress notifications carry the request id
Validates: MET-388
Tier: 2

### Given
- Same prerequisites as KB-PRG-001 (progress sink wired,
  `knowledge.ingest` listed in `tools/list`).
- A 3-file ingest batch under
  `uat://kb/streaming-progress/prg-002-file-{0..2}` with inline
  content.

### When
1. Call `knowledge_ingest` with the 3-file `files=[...]` batch and
   an explicit `request_id` of `"prg-002-batch"`.
2. Capture every `notifications/progress` frame for that tool
   call.

### Then
- Exactly 3 progress notifications are captured (one per file).
- Every captured notification carries `request_id == "prg-002-batch"`
  — the originating tool-call id round-trips through every frame
  so the client can correlate them to the in-flight request.
- No captured notification carries a null, empty, or mismatched
  `request_id` (e.g. no `request_id == None`, no `request_id ==
  ""`, no other batch's id).

---

## Scenario: KB-PRG-003 — supports_progress advertised on tools/list
Validates: MET-388
Tier: 2

### Given
- The MetaForge MCP server is connected.
- No prior session state is required — this scenario inspects only
  the `tools/list` surface.

### When
1. Call MCP `tools/list` at session start.
2. Locate the `knowledge.ingest` (or
   `mcp__metaforge__knowledge_ingest`) tool entry in the returned
   `tools` array.
3. Locate the `knowledge.search` tool entry as the negative-control
   sanity check.

### Then
- The `knowledge.ingest` tool entry exposes a `supports_progress`
  field whose value is the boolean `true` — the capability is
  explicitly advertised per the MET-388 contract (per L1-B2,
  `TestProgressCapabilityAdvertised::test_progress_capability_advertised_for_knowledge_ingest`).
- The `knowledge.search` tool entry exposes `supports_progress ==
  false` — only the long-running tool advertises the capability
  (sanity check; mirrors
  `TestProgressCapabilityAdvertised::test_search_does_not_advertise_progress`).
- If the `supports_progress` field is **absent** from the
  `knowledge.ingest` tool entry (rather than present-and-false),
  KB-PRG-001 records BLOCKED on the same run, not FAIL — per the
  master-plan note in §6.

---

## Acceptance — F1d block

- All 3 scenarios above appear under `## Scenario:` headings with
  `Validates`, `Tier`, `Given`, `When`, `Then` per the runner
  parsing contract (`.claude/agents/uat-validator.agent.md`).
- KB-PRG-001, KB-PRG-002, KB-PRG-003 record ✅ PASS on the next
  `/uat-cycle12 --tier 2 --scenario streaming-progress` run — all
  three are backed by L1-B2 (PR #170, merged).
- Master-plan rows in `docs/uat/kb-test-plan.md` §6 reflect the
  ✅ verdicts on the next plan refresh; the capability matrix at
  the top of the plan reports row 6 as `3 ✅ / 0 🔄`.
