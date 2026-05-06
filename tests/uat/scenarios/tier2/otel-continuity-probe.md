# Tier-2 — OTel trace continuity probe (Claude-driven, weekly)

Validates: MET-407. Multi-hop reasoning traces span every layer
(MCP → adapter → backend) without breakage when a real Claude
session runs the chain.
Tier: 2 (weekly probe)
Run: `/uat-cycle12 --tier 2 --scenario otel-continuity`

---

## Scenario

Give Claude a complex prompt that requires 4+ tool calls:

> *"Investigate why STM32H723VGT6 might fail thermal cycling.
> Use whatever tools you need."*

Claude is expected to (order may vary):

1. `twin.find_by_property` — locate the BOMItem.
2. `twin.thread_for` — find related TestExecutions.
3. `knowledge.search` — find prior `failure` chunks for thermal
   cycling.
4. `constraint.validate` — check current thermal-margin status.
5. Synthesize an answer with citations.

After Claude's response, the validator queries Tempo (or Jaeger)
for the trace tree and verifies properties below.

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

## Validator checks

After Claude completes the prompt:

- **Single root** — every tool call shares one root
  `correlation_id` / trace id.
- **Layer coverage** — for each tool call there are spans at the
  MCP layer, the adapter layer, and the backend (Postgres /
  Neo4j / MinIO) layer.
- **No orphan spans** — every span has a parent in the same
  trace tree.
- **Latency contiguity** — child span start times sit inside
  their parent's `[start, end]` window (no clock-skew breakage).
- **Attribute coverage** — `actor_id`, `session_id`, `project_id`
  are populated on the root span and inherited.

---

## Acceptance

- Probe spec written and runnable from `/uat-cycle12 --tier 2
  --scenario otel-continuity`.
- The probe passes against the dev stack.
- Report committed including a trace-tree screenshot or JSON
  dump under `docs/uat/uat-claude-driven-report-<date>.md`.
- Any orphan span or broken parent link → Linear bug filed.
