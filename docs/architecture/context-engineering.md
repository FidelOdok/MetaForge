# Context Engineering — Specification

> **Status:** P1.13 foundation shipped (MET-315 / 316 / 317 / 322 / 323).
> Wired into the live chat path per turn since MET-566 — see
> [Live chat wiring](#live-chat-wiring-met-566) below. The deposit side
> (what fills the memory tier the assembler reads) landed in MET-567 —
> see [Filling the memory tier](#filling-the-memory-tier-met-567).
> Keep this doc as the canonical reference; update inline rather than
> forking when extending the protocol.

## Why

Agents reason with the right context at the right time, attribute
every claim to a source, and never operate on stale or conflicting
data. Without an explicit protocol, every agent reinvents retrieval
and every reviewer guesses at provenance.

This spec pins down:

- The shape of a context request and response.
- How retrieval combines semantic, graph, and pivot sources.
- How role scoping, token budgets, conflicts, and staleness are
  enforced.
- Where attribution lives end-to-end.

## Public API surface

The L1 contract lives in `digital_twin/context/` and is exported via
`from digital_twin.context import ...`:

| Symbol                       | Role                                                                |
| ---------------------------- | ------------------------------------------------------------------- |
| `ContextAssembler`           | Orchestrator — `assemble(request) -> response`                      |
| `ContextAssemblyRequest`     | Pydantic model: agent_id, query, scope, work_product_id, budget, … |
| `ContextAssemblyResponse`    | Pydantic model: fragments, conflicts, sources, truncation flags     |
| `ContextFragment`            | Single attributed piece of context                                  |
| `ContextScope`               | enum: `KNOWLEDGE`, `GRAPH`, `WORK_PRODUCT`, `ALL`                   |
| `ContextSourceKind`          | enum: `KNOWLEDGE_HIT`, `GRAPH_NODE`, `GRAPH_EDGE`, `USER_INPUT`     |
| `Conflict`, `ConflictDetector`, `ConflictSeverity` | Cross-source disagreement model + detector  |
| `compute_staleness`          | 0–1 freshness score per fragment                                    |
| `get_role_knowledge_types`   | Per-role allow-list lookup                                          |

The assembler depends on `KnowledgeService` (Protocol — MET-346) and
`TwinAPI` (ABC — twin_core). Concrete backends (LightRAG, Neo4j) live
behind those interfaces.

## Assembly protocol

```
ContextAssemblyRequest
  agent_id: str                       # mechanical_agent / electronics_agent / ...
  query: str | None                   # required when scope ∋ KNOWLEDGE
  scope: list[ContextScope]           # default = [ALL]
  work_product_id: UUID | None        # pivot for GRAPH / WORK_PRODUCT
  knowledge_type: KnowledgeType | None
  knowledge_top_k: int = 5
  graph_depth: int = 1
  token_budget: int = 8000
  staleness_threshold: float = 1.0    # 1.0 = no filter; 0.5 ≈ < 30 days
  filters: dict
```

Order of operations inside `ContextAssembler.assemble`:

1. **Collect** — knowledge + work-product + graph collectors run based
   on `scope`. Each collector failure is isolated; one broken source
   does not break the assembly.
2. **Annotate staleness** — `annotate_cross_fragment_staleness` flags
   older duplicates of the same `source_id`; `compute_staleness`
   yields a per-fragment `[0, 1]` score.
3. **Filter staleness** — fragments above
   `request.staleness_threshold` drop out before ranking so they
   cannot steal slots from fresh ones.
4. **Rank** — composite priority `recency × authority × relevance`
   sorts the survivors.
5. **Enforce budget** — greedy keep-while-under-budget on the ranked
   list. Dropped fragments land in `dropped_source_ids`.
6. **Detect conflicts** — `ConflictDetector` runs on the kept set;
   `Conflict` rows surface in `response.conflicts` and
   `has_blocking_conflict` flips when an MPN identity mismatches.

## Retrieval strategy

Three sources, joined under a common `ContextFragment` shape:

| Source kind     | Collector                       | Filter signal              |
| --------------- | ------------------------------- | -------------------------- |
| `KNOWLEDGE_HIT` | `KnowledgeService.search`       | similarity_score (cosine)  |
| `GRAPH_NODE`    | `TwinAPI.get_work_product` / `get_subgraph` | structural distance |
| `GRAPH_EDGE`    | `TwinAPI.get_subgraph`          | structural distance        |
| `USER_INPUT`    | reserved (PRD, constraints)     | authority weight           |

Knowledge hits carry their cosine score directly; graph fragments
arrive without a similarity and use synthetic priority constants
(`_GRAPH_NODE_PRIORITY=0.95`, `_GRAPH_EDGE_PRIORITY=0.55`) so they
compete fairly with strong knowledge hits.

## Scoping rules — role-based (MET-316)

`digital_twin/context/role_scope.py` maps each agent role to a
`frozenset[KnowledgeType]`:

| Role                 | Allowed knowledge types                            |
| -------------------- | -------------------------------------------------- |
| `mechanical_agent`   | `DESIGN_DECISION`, `COMPONENT`, `FAILURE`          |
| `electronics_agent`  | `DESIGN_DECISION`, `COMPONENT`                     |
| `simulation_agent`   | `SESSION`, `FAILURE`                               |
| `compliance_agent`   | `CONSTRAINT`, `DESIGN_DECISION`                    |

Filter precedence inside `_collect_knowledge`:

1. `request.knowledge_type` — caller-explicit, wins always.
2. Role map via `request.agent_id` — narrows to the role's allow-list.
3. No filter — back-compat for unknown roles.

Role narrowing is post-applied: one `search` call with no server-side
type filter, over-fetched at `top_k * 4`, then trimmed against the
role allow-list. Avoids one round-trip per allowed type.

## Token budget management (MET-317)

Token counts come from `tiktoken` (default model `gpt-4o-mini`,
fallback `cl100k_base`, final fallback `len // 4`). Budget enforcement
is a greedy keep-while-under-budget walk over the ranked list.

Composite priority for ranking:

```
priority = recency × authority × relevance
```

| Component   | Source                                              | Default  |
| ----------- | --------------------------------------------------- | -------- |
| `recency`   | exponential decay on `metadata["created_at"]` (30d) | 1.0      |
| `authority` | source_kind weight (`user_input` 1.0 → `graph_edge` 0.5) | 0.5  |
| `relevance` | similarity_score                                    | 0.5      |

Tie-break: newer `created_at` first; smaller `token_count` first
(retains more fragments under tight budgets).

When the budget cuts, the assembler emits a `context_truncated`
structlog event with a `dropped_sources` per-source-kind tally that
MET-326 wires to a Prometheus counter.

## Attribution mechanism

Every `ContextFragment` carries:

- `source_kind` — origin enum.
- `source_id` — stable identifier (`work_product://<uuid>`, the
  knowledge `source_path`, or `graph://<node_id>`).
- `source_path` — file/URL when the source has one; always set for
  `KNOWLEDGE_HIT`.
- `heading`, `chunk_index`, `total_chunks` — citation breadcrumbs for
  knowledge hits (set by LightRAG via the `KnowledgeService` adapter).
- `work_product_id` — UUID when the fragment ties back to a Twin
  node.
- `knowledge_type` — coarse category (used by role scoping).

The invariant is enforced in tests
(`tests/unit/test_context_assembler.py::TestAttribution::test_every_fragment_has_source_id`):
**every fragment has a non-empty `source_id`.**

## Freshness policy (MET-323)

`compute_staleness(metadata)` returns a `[0, 1]` score (0 fresh, 1
stale) by combining three signals via `max`:

1. Explicit `metadata["superseded"]` truthy → `1.0`.
2. Age decay — `1 - exp(-ln(2) × age / 30d)`.
3. Cross-fragment shadowing — older duplicates of the same
   `source_id` carry `metadata["shadowed_by"]` ≥ 1 (each shadow
   contributes 0.5).

`request.staleness_threshold` (default `1.0` = back-compat) gates the
drop. `0.5` keeps roughly the last 30 days of decisions; `0.2` is
freshness-only.

The "auto-link old chunk superseded by new chunk" persistence is
deferred — LightRAG deletes the prior row on re-ingest (MET-307), so
callers set `metadata["superseded"]` explicitly when they have
business logic for it.

## Conflict resolution (MET-322)

`ConflictDetector` scans the kept fragment set for tracked fields
that disagree across sources sharing a grouping key (default `mpn`).

Severity matrix:

| Field                                          | Severity |
| ---------------------------------------------- | -------- |
| `mpn`                                          | blocking |
| `voltage`, `current`, `tolerance`, `material` | warning  |
| `package`, `footprint`                         | info     |

Field extraction order: `metadata[<field>]` first (authoritative —
populated by ingestion), then a colon-separated content regex
fallback. Markdown-table parsing is deferred.

Surfaced conflicts land in `response.conflicts: list[Conflict]`.
`response.has_blocking_conflict: bool` flips when at least one
`blocking` row exists — agent prompts can branch on it to refuse
action and escalate.

## Per-agent context specs

Per-skill expectations live next to the agent definition:

- **Mechanical** — `docs/agents/mechanical-context-spec.md` (MET-319).
- **Electronics** — `docs/agents/electronics-context-spec.md`
  (MET-332).

Each per-agent spec lists the Twin nodes, knowledge types, prior tool
results, and constraints required for every skill. They are the
canonical input for `RoleScope` map updates.

## Live chat wiring (MET-566)

The assembler runs on every harness chat turn via the layer-4 adapter
`api_gateway/chat/context_adapter.py`:

- **Wiring** — `init_context_assembler(twin, knowledge_service,
  collector)` is called from the gateway lifespan (mirroring
  `init_twin` / `init_mcp_bridge`). When LightRAG isn't configured the
  adapter is a no-op and chat behaves exactly as before.
- **Per turn** — `assemble_chat_context(query=user_message,
  window=context_window_for(provider, model))` runs a
  `KNOWLEDGE`-scoped request with a window-scaled fragment budget
  (`window // 20`, clamped to 1k–6k tokens). Best-effort: any failure
  logs `chat_context_assembly_failed` and the turn proceeds without
  retrieved context.
- **Rendering** — `render_context_block()` emits one line per fragment
  with its bracketed `source_id`, plus an explicit `WARNING` line when
  `has_blocking_conflict` is set and a truncation note when fragments
  were dropped (which also increments
  `metaforge_context_truncated_total` via the collector).
- **Placement** (`harness_backend._apply_turn_context`) — the
  *project brief* joins the layered system prompt on the native path
  (stable per thread, so provider prompt caches hit) and stays a
  leading `[project context]` history pair on the ReAct path; the
  *retrieved context block* is volatile per turn and is therefore a
  trailing `[retrieved context]` history pair on both paths, just
  before the user's message.
- **Requirements-discovery directive (MET-584)** — when the project
  has no `prd` or `constraint_set` work product, the brief instructs
  the agent to elicit quantified requirements and record them
  (`twin.record_constraint_set`) before substantive design work, and
  `twin.commit_geometry` soft-warns (never blocks) when geometry lands
  in an unconstrained project. Chat nudges; the design-flow gates
  (MET-582/583) enforce.
- **Geometry-commit directive (MET-615)** — both harness system prompts
  (native and ReAct) and the project brief now state explicitly that
  `freecad.*`/`cadquery.*` tools only write to a local, ephemeral
  adapter workspace, and that any turn which generates or modifies
  geometry must call `twin.commit_geometry` before the final answer.
  Before this, the only mention was a soft how-to clause in the brief;
  a full session could generate a CAD model and never persist it as a
  work product without the agent — or the user — noticing.
- **Telemetry** — `context.stats` gains a `retrieved_context`
  component; the `project_brief` component is now computed from the
  explicit brief text rather than sniffing the history pair.

The layered system prompt (native path) composes: identity/rules →
current date → tool-family capability summary (from the runtime's
registry) → project brief → response guidance (cite bracketed source
ids).

## Filling the memory tier (MET-567)

Retrieval is only half of context engineering: something has to *deposit*
what gets retrieved. Until MET-567 the deposit side was wired in one
place only — `MechanicalAgent` — and consolidation read from a fetcher
that a pgvector deployment never populates, so `agent_experiences` and
`memory.list_insights` were near-empty in every real deployment no
matter how much traffic the system saw. Five seams close that:

| Deposit path | Where | What lands |
| --- | --- | --- |
| Chat turn | `api_gateway/chat/experience_adapter.py` | one experience per **tool-using** turn: goal, tools used, failures, outcome |
| Completed session | `digital_twin/memory/session_bridge.py`, installed by `api_gateway/sessions/experience_bridge.py` | one experience per session: tool inventory, decisions, failures |
| Recorded decision | `api_gateway/twin/work_product_events.py` → `KnowledgeConsumer` | a `DESIGN_DECISION` knowledge chunk |
| Consolidation batch | `digital_twin/memory/consolidation/fetcher.py::PgVectorEventFetcher` | reads `agent_experiences` by time window in SQL |
| Consolidation cadence | `digital_twin/memory/consolidation/scheduler.py` | one BACKGROUND pass per interval |

Design points worth keeping:

- **Only tool-using turns are recorded.** A turn where the model just
  answered has no trajectory to learn from, and the design-flow
  handlers' one-shot extraction prompts (`max_steps=1`, no bridge)
  would otherwise flood the corpus with prompt-engineering noise.
- **One experience per session, not per event.** The lesson worth
  recalling is "how did this piece of work go"; per-event rows would
  bury it under individual tool calls.
- **Importance uses the same scorer everywhere.** Both new paths score
  through `digital_twin/memory/importance.py::score_importance` (via a
  synthetic `AGENT_TASK_*` event) rather than inventing their own
  formula, because the consolidation pass applies one `min_importance`
  floor across the whole corpus — a second formula would silently
  change what that floor means per deposit path.
- **The session deposit hooks the store, not the callers.** All three
  `complete_session` callers (REST route, `session.complete` MCP tool,
  sidecar idle rollover) go through the store, so
  `ExperienceBridgingSessionStore` wraps it once instead of leaving the
  next caller to rediscover the requirement.
- **Every deposit is best-effort.** A memory-tier failure never fails a
  chat turn, a session completion, or a decision recording.

`WORK_PRODUCT_CREATED` is the event `KnowledgeConsumer` has subscribed
to since MET-307 while nothing published one. `twin.record_decision`
now publishes it (returning `knowledge_indexed` in its result), which is
what makes a just-recorded decision findable via
`knowledge.search(knowledge_type=DESIGN_DECISION)`.

### Consolidation cadence

`ConsolidationScheduler` runs one BACKGROUND pass per interval from the
gateway lifespan. It is an asyncio task rather than a Temporal schedule
because the gateway runs no Temporal worker today
(`orchestrator/temporal_worker.py` registers the agent workflows only),
so a Temporal schedule would have left consolidation dormant — the exact
failure being fixed. The interval reuses the MET-454 spec's
`DEFAULT_INTERVAL_SECONDS` (30 min) so the two drivers of the same
pipeline cannot drift; each pass looks back twice the interval so an
experience written near a boundary still falls inside the next window.

| Env var | Default | Effect |
| --- | --- | --- |
| `METAFORGE_CONSOLIDATION_INTERVAL_SECONDS` | `1800` | Seconds between passes; `0` (or a negative value) disables the loop — set this when handing the job to a Temporal worker |

`MemoryClient` is now constructed with the `knowledge_service` in both
bootstraps (gateway and MCP sidecar). Omitting it left
`POST /v1/memory/search` and `GET /v1/memory/components/{name}`
answering 503 on every deployment.

## Observability hooks

- `context.assemble`, `context.collect_knowledge`,
  `context.collect_work_product`, `context.collect_graph` — OTel
  spans.
- `context_assembled` — structlog event with fragment count, token
  count, truncated flag.
- `context_truncated` — emitted when budget cuts; carries
  per-source-kind `dropped_sources` tally.
- `context_conflicts_detected` — emitted when the detector finds at
  least one disagreement; carries blocking flag and field list.

## Extension points

Adding a new source kind:

1. Add the enum value to `ContextSourceKind`.
2. Add a collector method on `ContextAssembler` and call it from
   `assemble` based on a new `ContextScope` value.
3. Update `_SOURCE_AUTHORITY` in `models.py` so the new kind ranks
   correctly under the budget.

Adding a new role:

1. Add a constant to `role_scope.py` and an entry to
   `_ROLE_KNOWLEDGE_TYPES`.
2. Add the per-skill spec under `docs/agents/`.

Adding a tracked conflict field:

1. Add the field name to `DEFAULT_FIELD_SEVERITY` in `conflicts.py`.
2. Update the per-agent spec for any role that should populate it
   from metadata at ingest time.

## References

- [Knowledge Ingestion Playbook](knowledge-ingestion-playbook.md) — how
  to add a new `KnowledgeType` end-to-end (MET-314).
- ADR-008 (planner repo) — L1 framework choice (LightRAG behind
  `KnowledgeService`).
- MET-312 — Context Engineering epic.
- MET-315 — Foundation (`ContextAssembler`).
- MET-316 — Role-based scoping.
- MET-317 — Token budgets + composite priority.
- MET-322 — Conflict detection.
- MET-323 — Staleness aging.
- MET-567 — Memory deposit paths + consolidation cadence.
- MET-569 — Tool-call hardening (see
  [robust-harness-design.md](robust-harness-design.md#tool-call-hardening-met-569)).
