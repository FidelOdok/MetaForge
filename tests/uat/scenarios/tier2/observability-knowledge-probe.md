# Tier-2 — observability knowledge probe (MET-410, F1e)

Validates: MET-410 (sub-deliverable F1e of MET-409).
Tier: 2
Run: `/uat-cycle12 --tier 2 --scenario observability-knowledge`

Three scenarios that promote the catalog rows KB-OBS-002,
KB-OBS-003 and KB-CTX-003 from `docs/uat/kb-test-plan.md` §9
(observability propagation) and §7 (per-call context) into
executable tier-2 form. Each scenario asserts a different facet of
the knowledge-base observability surface: a Prometheus counter
delta, Loki label promotion, and OTel span attribute propagation.

| Catalog id | Theme | Backing impl |
|---|---|---|
| KB-OBS-002 | Prometheus `knowledge_ingest_total` counter | NEW — counter not wired yet (scenario records BLOCKED at runtime until impl lands) |
| KB-OBS-003 | Loki carries `source_path` + `knowledge_type` labels | partial — structlog kwargs emitted; Loki label promotion not yet verified end-to-end |
| KB-CTX-003 | `mcp.actor_id` propagates to OTel span attributes | L1-B3 (merged), MET-387 — verified by `tests/unit/test_knowledge_call_context.py::TestOtelSpanAttributes` |

> **Note:** Scenarios use unique source paths under
> `uat://kb/observability/<id-suffix>` so they do not collide with
> tier-1 fixtures or other tier-2 probe blocks. The probe queries
> Loki and Prometheus directly via the Grafana datasources defined
> in `CLAUDE.md` (Prometheus UID `PBFA97CFB590B2093`, Loki UID
> `loki`, Tempo UID `P214B5B846CF3925F`).
>
> Per the master-plan note in §9, KB-OBS-002 records BLOCKED with
> the message `"knowledge_ingest_total counter not registered"` if
> `list_prometheus_metric_names` does not include the metric — not
> FAIL — until L1 wires the counter into `observability/metrics.py`.

---

## Scenario: KB-OBS-002 — Prometheus increments knowledge_ingest_total
Validates: MET-410
Tier: 2

### Given
- The MetaForge MCP server is connected and `tools/list` includes
  `knowledge.ingest` (or `mcp__metaforge__knowledge_ingest`).
- The Prometheus datasource (`PBFA97CFB590B2093`) is reachable from
  the runner via the Grafana MCP.
- A unique source path `uat://kb/observability/obs-002-counter` and
  inline content `"MetaForge marker — KB-OBS-002 counter probe"`.
- **If `knowledge_ingest_total` is absent from
  `list_prometheus_metric_names`, the scenario records BLOCKED with
  the message `"knowledge_ingest_total counter not registered"` —
  not FAIL.** This counter is NEW per the master-plan (the metric
  is not yet wired in `observability/metrics.py`).

### When
1. Read the baseline counter via PromQL:
   `sum(knowledge_ingest_total{service_name="metaforge-gateway"})`.
   Capture the scalar as `v0` (treat `NaN` / no-data as `0`).
2. Call `knowledge_ingest(content="MetaForge marker — KB-OBS-002
   counter probe", source_path="uat://kb/observability/obs-002-counter",
   knowledge_type="design_decision")` — this is the same shape as
   KB-ING-001 in §1.
3. Wait up to 15 s for Prometheus scrape, then re-read:
   `sum(knowledge_ingest_total{service_name="metaforge-gateway"})`.
   Capture as `v1`.

### Then
- Step 2 returns a success response whose `data.chunks_indexed >= 1`
  (sanity check that the ingest actually ran).
- The metric `knowledge_ingest_total` appears in
  `list_prometheus_metric_names` (i.e. it is registered).
- The counter delta `v1 - v0 >= 1` — the ingest call advanced the
  counter by at least one.
- If `v0 == NaN` AND `v1 == NaN` (i.e. the metric exists in the
  registry but never produced a sample), the scenario records
  BLOCKED with `"knowledge_ingest_total never scraped"` rather
  than FAIL — the impl gap is "registered but not incremented",
  not "assertion violated".

---

## Scenario: KB-OBS-003 — Loki carries source_path and knowledge_type labels
Validates: MET-410
Tier: 2

### Given
- The MetaForge MCP server is connected and `tools/list` includes
  `knowledge.ingest`.
- The Loki datasource (UID `loki`) is reachable from the runner
  via the Grafana MCP.
- A unique source path `uat://kb/observability/obs-003-labels` and
  inline content `"MetaForge marker — KB-OBS-003 label probe"`.
- The runner has a way to produce a current ISO-8601 timestamp
  bookmark (`t_before`) immediately before the ingest call, so
  the Loki query can be tightly bounded to this scenario's frame.

### When
1. Capture `t_before = now()` (UTC, ISO-8601).
2. Call `knowledge_ingest(content="MetaForge marker — KB-OBS-003
   label probe", source_path="uat://kb/observability/obs-003-labels",
   knowledge_type="design_decision")` — capture the response and
   note the `trace_id` (if exposed) for cross-correlation.
3. Wait up to 10 s for Loki ingestion, then run LogQL:
   `{service_name="metaforge-gateway"} |= "knowledge_ingest" | json |
    source_path="uat://kb/observability/obs-003-labels"`
   over the window `[t_before, now()]`.
4. As a negative-control, run the same LogQL against
   `service_name="dashboard"` and confirm zero matches.

### Then
- Step 3 returns ≥ 1 log entry (the ingest call left a trace in
  Loki under the gateway service label).
- At least one matching entry exposes a non-empty
  `source_path` field whose value equals
  `"uat://kb/observability/obs-003-labels"` after JSON parsing.
- At least one matching entry exposes a non-empty
  `knowledge_type` field whose value equals `"design_decision"`.
- Step 4 (negative control) returns 0 entries — the labels are
  scoped to the gateway service, not leaking to other services.
- If the JSON pipeline parse stage finds **neither** field on
  any matching entry (rather than finding them with the wrong
  values), the scenario records BLOCKED with `"Loki label
  promotion pending — fields emitted by structlog but not parsed
  out of message"` — Loki label promotion is the open piece of
  this row per the master plan.

---

## Scenario: KB-CTX-003 — actor_id propagates to span attributes
Validates: MET-387
Tier: 2

### Given
- The MetaForge MCP server is connected and `tools/list` includes
  `knowledge.ingest` and `knowledge.search`.
- The Tempo datasource (UID `P214B5B846CF3925F`) and Loki
  datasource (UID `loki`) are reachable from the runner.
- The runner can attach the per-call header `X-Actor-Id:
  claude-uat-runner` to outbound MCP tool calls (per L1-B3, the
  call-context plumbing in `mcp_core/` round-trips this header
  into `current_context().actor_id`).
- A unique source path `uat://kb/observability/ctx-003-actor` and
  inline content `"MetaForge marker — KB-CTX-003 actor probe"`.

### When
1. Call `knowledge_ingest(content="MetaForge marker — KB-CTX-003
   actor probe", source_path="uat://kb/observability/ctx-003-actor",
   knowledge_type="design_decision")` with the per-call header
   `X-Actor-Id: claude-uat-runner`. Capture the response and the
   server-side `trace_id` if exposed (else fall back to the
   correlation_id on the response envelope).
2. Call `knowledge_search(query="KB-CTX-003 actor probe", top_k=5)`
   under the same `X-Actor-Id: claude-uat-runner` header.
3. Query Loki for OTel-emitted span records that match either
   trace via LogQL:
   `{service_name="metaforge-gateway"} | json | trace_id=~"<t1>|<t2>"
    | line_format "{{.body}}"` and extract every entry whose
   `scope_name` contains `tool_registry.tools.knowledge` (the
   adapter scope) — the runner reads
   `attributes.mcp.actor_id` off these entries.

### Then
- Step 1 returns a success response whose `data.chunks_indexed >= 1`.
- Step 2 returns ≥ 1 hit whose
  `source_path == "uat://kb/observability/ctx-003-actor"`.
- The ingest span (Step 1, scope
  `tool_registry.tools.knowledge.adapter`) carries
  `attributes.mcp.actor_id == "claude-uat-runner"`.
- The search span (Step 2, same scope) carries
  `attributes.mcp.actor_id == "claude-uat-runner"` — i.e. the
  attribute is present on **both** ingest and search spans, per
  the master-plan §7 contract.
- No matching span carries `attributes.mcp.actor_id == None`,
  empty string, or any other actor id (e.g. no leakage from a
  prior call).

---

## Acceptance — F1e block

- All 3 scenarios above appear under `## Scenario:` headings with
  `Validates`, `Tier`, `Given`, `When`, `Then` per the runner
  parsing contract (`.claude/agents/uat-validator.agent.md`).
- KB-CTX-003 records ✅ PASS on the next `/uat-cycle12 --tier 2
  --scenario observability-knowledge` run — backed by L1-B3
  (`tests/unit/test_knowledge_call_context.py::TestOtelSpanAttributes`).
- KB-OBS-002 records BLOCKED until the
  `knowledge_ingest_total` counter is registered in
  `observability/metrics.py` and incremented by the ingest path.
- KB-OBS-003 records BLOCKED until Loki label promotion of
  `source_path` and `knowledge_type` is verified end-to-end.
- Master-plan rows in `docs/uat/kb-test-plan.md` §7 + §9 reflect
  these states on the next plan refresh.
