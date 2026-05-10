# Tier-2 — OTel trace continuity probe (Claude-driven, weekly)

Validates: MET-407. Multi-hop reasoning traces span every layer
(MCP → adapter → backend) without breakage when a real Claude
session runs the chain.
Tier: 2 (weekly probe)
Run: `/uat-cycle12 --tier 2 --scenario otel-continuity`

---

## What's different from synthetic

A synthetic test could fake the trace shape. Claude-driven means:

- **Real LLM-paced sequencing** — gaps between calls reflect
  actual decision time, not a scripted sleep.
- **Real correlation_id propagation** across the model's
  "thinking" boundaries (each follow-up tool call must inherit
  the parent span context).
- **Real validator-side confirmation** that the user-visible
  answer is traceable end-to-end.

---

## Scenario: multi-hop reasoning trace stays intact end-to-end
Validates: MET-407
Tier: 2

### Given
- The MetaForge gateway is up with OTel enabled and Tempo (or
  Jaeger) reachable from the validator harness.
- The knowledge corpus contains at least one prior `failure`
  chunk for thermal cycling and a BOMItem for the
  `STM32H723VGT6` MCU (any tier-1 corpus seeded for
  electronics-vertical scenarios suffices).
- The Claude session is configured with the standard MetaForge
  MCP tool set: `twin.find_by_property`, `twin.thread_for`,
  `knowledge.search`, `constraint.validate`.

### When
1. Issue a single complex prompt to the Claude session that
   requires 4+ tool calls to answer:
   *"Investigate why STM32H723VGT6 might fail thermal cycling.
   Use whatever tools you need."*
2. Let Claude drive the tool sequence end-to-end. The expected
   shape (order may vary) is:
   - `twin.find_by_property` to locate the BOMItem.
   - `twin.thread_for` to find related TestExecutions.
   - `knowledge.search` to find prior `failure` chunks for
     thermal cycling.
   - `constraint.validate` to check the current thermal-margin
     status.
   - A synthesized answer with citations.
3. Capture the root `correlation_id` (or trace id) from the
   first tool call's response headers / metadata.
4. Query Tempo (or Jaeger) for the trace tree by that id and
   collect every span it returns.

### Then
- **Single root** — every tool call in the conversation shares
  one root `correlation_id` / trace id; there is no orphaned
  second root.
- **Layer coverage** — for each tool call there are spans at
  the MCP layer, the adapter layer, and the backend layer
  (Postgres / Neo4j / MinIO as appropriate for that call).
- **No orphan spans** — every span in the captured tree has a
  parent within the same trace tree (no `parent_span_id`
  pointing outside the tree).
- **Latency contiguity** — every child span's `start_time` sits
  inside its parent's `[start, end]` window; no clock-skew
  breakage.
- **Attribute coverage** — `actor_id`, `session_id`, and
  `project_id` are populated on the root span and inherited by
  every descendant (no descendant span is missing these
  attributes).
- The user-visible answer is traceable end-to-end — every claim
  Claude makes can be linked back to a span in the captured
  tree (no untraced "thinking" call).

---

## Acceptance

- Probe spec written and runnable from `/uat-cycle12 --tier 2
  --scenario otel-continuity`.
- The probe passes against the dev stack.
- Report committed including a trace-tree screenshot or JSON
  dump under `docs/uat/uat-claude-driven-report-<date>.md`.
- Any orphan span or broken parent link → Linear bug filed.
