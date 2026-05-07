# Knowledge Ingestion Playbook

> **Status:** Living document. P1.13 / P1.14 baseline (MET-307, MET-336,
> MET-346). Update in place when the contract changes.

## Audience

Engineers extending the L1 knowledge layer with a new
`KnowledgeType` — for example, adding a `test_result` channel so QA
fixtures become first-class context for the agents.

## What the pipeline looks like today

```
Twin event (WORK_PRODUCT_*) ──► EventBus ──► KnowledgeConsumer
                                                      │
                                                      ▼
                                           KnowledgeService.ingest()
                                                      │
                                                      ▼
                                          LightRAG (chunks → vectors)
                                                      │
                                                      ▼
                                           Postgres + pgvector
```

CLI ingests (`forge ingest <path>`) take a parallel path that bypasses
the bus and calls `KnowledgeService.ingest()` directly. Both paths land
in the same store and feed the same `KnowledgeService.search()` —
`ContextAssembler` neither knows nor cares which side a hit came from.

## Step-by-step: add a new `KnowledgeType`

The worked example below adds `test_result` so bring-up checklists,
fixture results, and HIL test logs become searchable knowledge.

### 1. Extend the enum

Edit [`digital_twin/knowledge/store.py`](../../digital_twin/knowledge/store.py):

```python
class KnowledgeType(StrEnum):
    DESIGN_DECISION = "design_decision"
    COMPONENT       = "component"
    FAILURE         = "failure"
    CONSTRAINT      = "constraint"
    SESSION         = "session"
    TEST_RESULT     = "test_result"   # ← new
```

The enum is re-exported from
[`digital_twin/knowledge/types.py`](../../digital_twin/knowledge/types.py)
— no change needed there.

### 2. Wire the consumer's auto-classification

[`digital_twin/knowledge/consumer.py`](../../digital_twin/knowledge/consumer.py)
maps `event.data["work_product_type"]` to the right enum value:

```python
_WORK_PRODUCT_TYPE_MAP: dict[str, KnowledgeType] = {
    "design_decision": KnowledgeType.DESIGN_DECISION,
    "component":       KnowledgeType.COMPONENT,
    "constraint":      KnowledgeType.CONSTRAINT,
    "failure_mode":    KnowledgeType.FAILURE,
    "session":         KnowledgeType.SESSION,
    "test_result":     KnowledgeType.TEST_RESULT,   # ← new
}
```

Producers should set `event.data["work_product_type"] = "test_result"`
when emitting `WORK_PRODUCT_CREATED` for QA artefacts. No new event type
is needed — `WORK_PRODUCT_*` covers any payload with a textual body.

### 3. Wire the CLI walker (optional)

If the new type has files on disk that should ingest via
`forge ingest <path>`, extend the inference table in
[`cli/forge_cli/ingest.py`](../../cli/forge_cli/ingest.py):

```python
_PATH_HINTS: dict[str, KnowledgeType] = {
    "/decisions/":  KnowledgeType.DESIGN_DECISION,
    "/components/": KnowledgeType.COMPONENT,
    "/tests/":      KnowledgeType.TEST_RESULT,   # ← new
}
```

Path hints are best-effort. CLI users can always pass
`--type test_result` to override inference.

### 4. Add a role allow-list entry

[`digital_twin/context/role_scope.py`](../../digital_twin/context/role_scope.py)
narrows knowledge by agent role. If `test_result` is relevant to a
specific agent (typically `firmware_agent` or `mechanical_agent`), add
it:

```python
_ROLE_TO_KNOWLEDGE_TYPES: dict[str, frozenset[KnowledgeType]] = {
    "firmware_agent":   frozenset({
        KnowledgeType.DESIGN_DECISION,
        KnowledgeType.COMPONENT,
        KnowledgeType.TEST_RESULT,    # ← new
    }),
    ...
}
```

Skip this if the new type should be visible to every agent — the
default behaviour when a role isn't listed.

### 5. Add validation rules (optional)

`KnowledgeConsumer._extract_content()` is the validation gate.
Drop empty payloads and reject dangerous content here, not at the
search layer:

```python
def _extract_content(self, data: dict[str, Any]) -> str:
    content = data.get("content", "")
    if data.get("work_product_type") == "test_result":
        # Reject empty test logs — they pollute the corpus.
        if not content.strip() or len(content) < 50:
            return ""
    return content
```

Keep validation declarative — never silently mutate content. Drop +
log; never edit-then-store.

### 6. Add embedding-quality smoke checks

[`tests/integration/test_retrieval_eval.py`](../../tests/integration/test_retrieval_eval.py)
runs against a labeled fixture; add 1–2 queries for the new type to
[`tests/fixtures/knowledge/retrieval_eval.json`](../../tests/fixtures/knowledge/retrieval_eval.json):

```json
{
  "id": "Q-TEST-RESULT",
  "agent_id": "firmware_agent",
  "query": "What was the bring-up result for the SR-7 board v0.3?",
  "relevant": ["tests/bringup/sr7-v03.md"]
}
```

The retrieval evaluator (MET-326) records precision/recall to
`metaforge_retrieval_*` histograms — if your new corpus regresses
mean precision >10% vs the 24h baseline, the
`RetrievalPrecisionRegression` alert fires automatically.

### 7. Cover the contract in tests

Minimum bar for a new knowledge type:

| Level | What | Where |
|-------|------|-------|
| Unit | Enum constant exists; consumer maps the string | `tests/unit/test_knowledge_consumer.py` |
| Integration | End-to-end ingest → search round-trip | `tests/integration/test_knowledge_event_flow.py` |
| Eval | At least one labeled query in the fixture | `tests/fixtures/knowledge/retrieval_eval.json` |

Run before opening the PR:

```bash
ruff check .
pytest tests/unit/test_knowledge*.py tests/unit/test_context_assembler.py -q
pytest tests/integration/test_knowledge_event_flow.py --integration -q
```

## Reference: published events

`KnowledgeConsumer` listens for these on the orchestrator event bus:

| Event | Action |
|-------|--------|
| `WORK_PRODUCT_CREATED` | Ingest the payload as a new `KnowledgeEntry` |
| `WORK_PRODUCT_UPDATED` | `delete_by_source(work_product://<id>)` then re-ingest — no orphan duplicates |

There is **no** `WORK_PRODUCT_DELETED` handler today — deleting a work
product does not cascade-delete its knowledge chunks. File a follow-up
issue if your new type needs delete semantics; it's a five-line
extension to the consumer.

## Validation rules in force

`KnowledgeService.ingest()` enforces these regardless of type:

- `content` must be non-empty after `strip()`.
- `source_path` is required; the consumer derives
  `work_product://<uuid>` for graph-sourced ingests.
- `metadata` keys must JSON-serialize; non-primitive values are
  stringified.

The `LightRAGKnowledgeService` adapter additionally enforces:

- Content is deduplicated by SHA-1 — re-ingesting identical content
  returns the same entry id without re-running embeddings.
- Embeddings are pre-warmed during `initialize()` so the first
  ingest doesn't time out on cold sentence-transformers weights.

## Embedding quality checks

The retrieval evaluator
([`digital_twin/context/retrieval_evaluator.py`](../../digital_twin/context/retrieval_evaluator.py))
is the canonical quality measure. Run it against the labeled fixture:

```python
from digital_twin.context.retrieval_evaluator import (
    RetrievalEvaluator, load_eval_set,
)

evaluator = RetrievalEvaluator(knowledge_service, k=5)
report = evaluator.evaluate_sync(load_eval_set("tests/fixtures/knowledge/retrieval_eval.json"))
print(report.as_dict())
```

Track the metrics over time:

| Metric | Histogram | What "good" looks like |
|--------|-----------|------------------------|
| precision@5 | `metaforge_retrieval_precision_at_k` | ≥ 0.4 average across the eval set |
| recall@5 | `metaforge_retrieval_recall_at_k` | ≥ 0.6 average |
| MRR | `metaforge_retrieval_mrr` | ≥ 0.5 |
| NDCG@5 | `metaforge_retrieval_ndcg_at_k` | ≥ 0.6 |

If your new type drags any of these below floor, the embedding model
or chunker is the suspect, not the eval set — fix the producer side
before relaxing the floor.

## Search filters

The `filters` argument on `knowledge_search` (and on the underlying
`KnowledgeService.search`) is pinned to a small, unambiguous contract
(MET-417, KB-SRC-014). Adding a new metadata key to your ingest
payload makes it filterable with no further wiring — but the
semantics are fixed and must not be re-litigated per caller.

**Pinned semantics:**

- **AND across keys, equality match.** `filters={"a": "x", "b": "y"}`
  returns only chunks whose metadata has `a == "x"` *and* `b == "y"`.
  There is no OR, no inequality, no range, no `LIKE`. Add a separate
  call (or compose at a higher layer) if you need disjunctive logic.
- **Unknown keys pass through as literal metadata equality.** A filter
  on a key the corpus has never seen yields zero hits with no error.
  This is by design — it lets producers stamp arbitrary structured
  metadata at ingest time and have it become filterable immediately
  without a schema migration. If you misspell a key, you get an empty
  result set; `tools/list` does *not* enumerate the legal filter keys.
- **Reserved keys.** `source_path`, `source_work_product_id`, and
  `project_id` are special-cased: they match the top-level encoded
  fields, not the user-extras blob. `knowledge_type` is a separate
  argument, not a filter key. Everything else lives at
  `$.metadata.<key>` (the `extra` dict you passed to `ingest`).
- **Allowed value types: `str` / `int` / `bool` / `None`.** Composite
  values (`dict`, `list`) are rejected at the adapter boundary with the
  MET-385 `invalid_input` error envelope, listing the offending field
  and type. `None` matches null / missing metadata; `bool` round-trips
  literally without coercing to `0` / `1`.
- **Pushed down to SQL on the pgvector path.** Filters become
  `AND c.file_path::jsonb->'x'->>'<key>' = $<n>` (or top-level
  `->>'src'` / `->>'wp'` for the reserved keys) so the LIMIT'd query
  doesn't starve filter matches off the top-k tail. The naive /
  in-memory backend post-filters the returned hits in Python with the
  same equality semantics.
- **Observability.** A successful search with filters emits
  `knowledge_search_filters_applied` with the sorted **keys** and the
  resulting hit count. Filter **values** are never logged — they may
  carry PII, project names, or actor handles.

Want a new filter? Just stamp the key into `metadata={…}` at ingest.
No code change required. If you need a non-equality operator, file a
Linear issue against MET-417 — that's a contract change, not a config
toggle.

## Failure modes to know about

- **Empty payloads silently skipped.** If a producer publishes an
  event with no `content`, the consumer logs `knowledge_consumer_skip
  reason=no_content` and exits cleanly. Add producer-side validation
  if you need a hard error instead.
- **Update without prior insert** is a no-op delete + a fresh insert
  — safe but wasteful. Producers should only emit `UPDATED` after a
  prior `CREATED`.
- **Same-content dedup hides intentional re-indexes.** If you rotate
  the embedding model and re-ingest, the SHA-1 dedup will refuse the
  re-write. Bump the namespace prefix or pre-delete by source first.

## Related

- [`docs/architecture/context-engineering.md`](context-engineering.md)
  — the consuming layer.
- [`docs/architecture/adr-008-external-harness-and-l1-framework.md`](https://github.com/MetaForge-HA/MetaForge-Planner/blob/main/docs/architecture/adr-008-external-harness-and-l1-framework.md)
  — why LightRAG is the L1 backend.
- [`docs/agents/mechanical-context-spec.md`](../agents/mechanical-context-spec.md)
  / [`electronics-context-spec.md`](../agents/electronics-context-spec.md)
  — per-agent allow-lists that constrain what the agent sees.
