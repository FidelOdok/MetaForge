# Robust Agent Harness — Design (MET-547)

Design doc for the production-grade agent harness: a unified LLM provider
pipeline, first-class MCP integration, and a portable `SKILL.md` skills system,
wrapping MetaForge's existing Planner → Generator → Evaluator loop and
gate-ledger enforcement. Synthesizes patterns from **Hermes** (Nous Research)
and **OpenClaw** (OpenClaw Foundation).

| Field | Value |
|:------|:------|
| **Status** | Accepted (supersedes the deferral in ADR-008 for the harness scope) |
| **Linear** | MET-547 |
| **Supersedes/revives** | MET-308 (in-house harness, deferred by ADR-008) |
| **Deciders** | Fidel (project lead — also ADR-008 decider) |
| **Builds on** | `orchestrator/harness/` (three-agent loop, MET-474/MET-475) |

---

## Why this doc exists

MET-547 originally linked a design doc on an unmerged working branch
(`claude/evaluate-knowledge-base-rIb9d`) that was never pushed to a reachable
remote — it 404s under both `FidelOdok/MetaForge-Planner` and
`MetaForge-HA/MetaForge-Planner`, and is absent from the local Planner clone.
This document replaces that phantom reference as the authoritative design, and
is grounded in three real sources: (1) the MET-547 issue body, (2) ADR-008, and
(3) the existing harness code in `orchestrator/harness/`.

## ADR-008 reconciliation

ADR-008 (Accepted, 2026-04-26) **deferred** MetaForge's in-house L3 ReAct loop
(MET-308) and L4 multi-agent DAG, choosing to let external harnesses (Claude
Code, Codex) act as the reasoning brain while MetaForge exposed L1 (knowledge)
+ L2 (MCP tools). ADR-008 explicitly reserved the right to revisit:

> "This ADR defers, doesn't cancel. A future ADR will revisit if dogfood
> reveals external-harness limits."

MET-547 invokes exactly that clause. The harness here is **not** a rejection of
the external-harness strategy — it is complementary:

- **External harness route stays intact.** Phase 2 ships `metaforge mcp serve`,
  keeping MetaForge drivable *by* Claude Code/Codex over MCP (the ADR-008 win).
- **In-house runtime is additive.** The provider pipeline + agent loop give
  MetaForge a *provider-agnostic* runtime it can run standalone (Anthropic,
  OpenAI, OpenRouter, local vLLM/Ollama) for the MET-524 live-generation
  orchestrator, where an external harness is not in the loop.
- **Bidirectional MCP** (client *and* server) means the same tool surface
  serves both directions.

Net: MetaForge is both drivable-by and capable-of-driving. This doc records the
decision to build the in-house runtime; the external-harness contract from
ADR-008 is preserved, not reversed.

---

## Existing foundation

`orchestrator/harness/` already implements the loop this wraps:

- `three_agent.py` — `ThreeAgentHarness.run()`: Planner → Generator → Evaluator
  with an iteration cap, gate verdicts (`GateResult`), and a typed
  `HarnessOutcome` (`passed` / `exhausted` / `errored`). Agents are narrow
  `Protocol`s; the orchestrator never imports concrete implementations.
- `artifacts.py` — `ArtifactStore` (the by-name artifact contract between agents).
- `coding/` and `hardware/` — per-domain agent + gate stubs (agents currently
  stub the model call: `coding/agents.py` "the real coding generator would
  invoke a model; here we just …").

MET-547 makes those stubbed model calls real (via the provider pipeline), adds
MCP tools to the agents' toolbelt, and makes the loop configurable by
`SKILL.md` playbooks.

## Architecture

```
                 ┌─────────────────────────────────────────┐
                 │  ThreeAgentHarness (existing)            │
                 │  Planner → Generator → Evaluator loop    │
                 └───────────────┬─────────────────────────┘
                                 │ agents call model + tools
        ┌────────────────────────┼───────────────────────────┐
        ▼                        ▼                            ▼
┌───────────────┐      ┌──────────────────┐        ┌──────────────────┐
│ ProviderPipe  │      │ Central Tool      │        │ Skill Registry    │
│ resolver +    │      │ Registry          │        │ (SKILL.md loader) │
│ retries +     │      │ native + MCP      │        │                   │
│ fallback +    │      │ (client + server) │        │                   │
│ role slots    │      └──────────────────┘        └──────────────────┘
└───────┬───────┘
        ▼
 Anthropic · OpenAI · OpenRouter · local (vLLM/Ollama)
```

### Layering note (open design question)

`orchestrator/CLAUDE.md` forbids `orchestrator` importing `mcp_core`,
`tool_registry`, or `domain_agents`. The provider pipeline (pure LLM-runtime
concern, deps: stdlib + pydantic + the openai/anthropic SDKs + observability)
sits cleanly under `orchestrator/harness/providers/`. The **MCP tool registry**
(Phase 2) cannot live under `orchestrator` without violating that rule — it
belongs in a new top-level `harness/` package or in `mcp_core`. This is flagged
for resolution before Phase 2 lands; Phase 1 is unaffected.

## Synthesis — what we take from each framework

| From Hermes | From OpenClaw |
|---|---|
| Provider resolver over the verified provider surface (see below), `api_max_retries` + ordered `fallback_providers` | Auth-profile rotation (keep cache warm) + profile pinning |
| Role-based auxiliary model slots (planner/generator/evaluator/vision/compression) | `SKILL.md` markdown-playbook skills + registry |
| OpenAI-compatible REST + SSE, Runs API + `/approval` | WebSocket real-time streaming; bidirectional MCP |
| SQLite session ledger (FTS5) + context compression | Session write-lock + `before_tool_call` hooks; local-first Markdown artifacts |

### Provider surface (verified against Hermes docs, MET-549)

Hermes integrates **~35 provider ids** — but they collapse into a few **API
families**, which is what the adapter layer keys on:

- **OpenAI-compatible** (one adapter + `base_url`): openrouter, openai, deepseek,
  xai, novita, kimi/moonshot (±cn), zai/GLM, alibaba/dashscope (±coding-plan),
  minimax (±cn), huggingface, nvidia, arcee, gmi, xiaomi, tencent-tokenhub,
  opencode-zen/go, kilocode, stepfun, azure-foundry, and local runtimes
  (ollama, vllm, sglang, llama.cpp, lmstudio) + router proxies (litellm,
  clawrouter, custom).
- **Anthropic-native:** anthropic/claude.
- **Gemini-native:** gemini (google-genai).
- **AWS Bedrock (Converse):** bedrock.
- **Codex subscription (Responses API):** openai-codex — drives a ChatGPT
  Plus/Pro subscription with no API key by reusing `~/.codex/auth.json`
  (MET-550). See [Using a ChatGPT subscription with the harness](../harness-codex-subscription.md).
- **Deferred (later slices):** Google Vertex (OAuth2), GitHub Copilot, and the
  remaining OAuth portals (Nous Portal, qwen/minimax/xai OAuth, ollama-cloud).

Implemented as `orchestrator/harness/providers/registry.py` (`resolve_provider`)
+ four adapters in `adapters.py`; `default_invoke` dispatches by family.
Providers with account/region-specific endpoints read `base_url` from
`HARNESS_<ID>_BASE_URL` so no guessed URL ships. See MET-549.

## Phased plan

### Phase 1 — Provider pipeline + gateway
- `ProviderPipeline`: resolver, `api_max_retries`, ordered fallback chain **← first slice**
- Auth-profile rotation + per-session pinning (cache warmth)
- Role-based auxiliary model slots (planner/generator/evaluator/vision/compression)
- Gateway: OpenAI-compatible REST + SSE, `POST /v1/runs` + `/runs/{id}/approval`

### Phase 2 — MCP integration
- Central tool registry (native + MCP), `mcp_<server>_<tool>` naming
- stdio + HTTP/SSE + streamable-http transports; OAuth 2.1
- Per-server tool filtering wired to gate preconditions
- `metaforge mcp serve` — expose design state as MCP server (ADR-008 contract)

### Phase 3 — Skills + agent loop
- `SKILL.md` loader (bundled + optional) + skill registry
- Planner → Generator → Evaluator, each with a ReAct inner loop
- Session write-lock; gate-ledger precondition checks (`before_tool_call`)
- Approval flow (soft-gate waivers) via Runs API

### Phase 4 — State + hardening
- SQLite session ledger (FTS5) + Markdown artifact store
- Context compression + session lineage
- Cron/heartbeat re-validation
- WebSocket real-time streaming (10 Hz artifacts, per MET-524)

## Phase 1 component design — `ProviderPipeline`

Pure, transport-injected logic so it is fully unit-testable without network:

- `ProviderSpec` — one provider+model target (`name`, `model`, `api_key_env`,
  optional `base_url`, `weight`, free-form `extra`).
- `RetryPolicy` — `api_max_retries`, backoff base, the set of retryable
  conditions (429 / 5xx / timeouts).
- `RoleModelSlots` — maps each role (planner/generator/evaluator/vision/
  compression) to an ordered list of `ProviderSpec` (primary + fallbacks).
- `ProviderPipeline.complete(role, request, invoke)` — resolves the role's
  ordered candidates, and for each attempts up to `api_max_retries` with
  backoff; on a non-retryable error or exhausted retries, falls through to the
  next provider. Raises `AllProvidersFailedError` (carrying every attempt's
  error) only when the whole chain is exhausted. `invoke` is an injected async
  callable `(ProviderSpec, request) -> response`, so the SDK binding is a
  separate, swappable concern.

This satisfies the success criteria "same loop runs against any provider with
zero code change" and "automatic failover on 429: fall to next model, session
preserved."

### Provider/model pairs are checked before any call (FORGE-468)

A pair the provider's API family cannot serve is caught when the
configuration is resolved, not discovered as a 400 on every call.
`registry.model_family_mismatch(provider, model)` is a conservative family
check, not a model catalogue:

- `openai-codex` rejects a `vendor/model` slug (FORGE-93).
- A first-party family serves only its own vendor's models: `anthropic`
  rejects `gpt-*` / `o1` / `o3` / `o4` slugs, `openai-codex` rejects
  `claude-*` / `gemini*`, `gemini` rejects `claude-*` / `gpt-*`.
- A slug whose vendor is not plain from its prefix passes, and multi-vendor
  gateways (OpenRouter, vLLM, Bedrock and every other OpenAI-compatible
  provider) are never judged.

Where it applies: `load_provider_config` raises `ConfigError`;
`PUT /v1/harness/selection` answers 400; a stored selection is ignored on
read; and the chat harness's `provider_config_from_env` drops a mismatched
candidate from the chain with a `harness_provider_model_mismatch` warning
(and raises `InvalidModelError` if no candidate survives). Dropping the
primary counts as a fallback, below.

### Falling back is an alarm (FORGE-468)

Whenever a role is served by anything other than its primary, the harness:

- logs `harness_provider_fallback` at WARNING with `role`, `primary`,
  `primary_model`, `fallback`, `fallback_model`, `reason` and the primary's
  error (`primary_error`);
- increments `metaforge_harness_provider_fallback_total{primary, fallback,
  role, reason}`, where `reason` is `call_failed` (the primary was tried and
  failed), `model_mismatch` (the primary was dropped at config time) or
  `capability_unsupported` (the primary's family declined by design, e.g.
  Codex has no event-streaming adapter; logged at INFO, not WARNING);
- keeps the last occurrence, returned by `GET /v1/harness/providers` as
  `last_fallback` with a process-lifetime `fallback_count`.

The `HarnessProviderFallbackSustained` alert fires when any
`(primary, fallback, role)` keeps falling back for 15 minutes (excluding
`capability_unsupported`): at that point
the configured model is not the one producing output.

Callers that need to know which model answered wrap the call in
`provenance.capture_served()`; the flow generator uses it to record
`generatedBy` on every proposal.

### Token and cost accounting (FORGE-476)

Every model call the pipeline makes records one usage event: prompt,
completion and cached-input tokens, cost, provider and model, attributed to a
run id, a phase, a role and a caller. Attribution rides a context variable
(`providers/usage.py`, `usage_scope(...)`), so child tasks inherit it:

- `HybridBrain.run_phase` sets `run_id`, `phase` and role `phase_brain` for
  both engines (in-process and the Temporal worker);
- the flow generator sets role `flow_generator`;
- `run_chat_turn` and `run_chat_turn_streaming` default the role to `chat`
  when no caller set one.

Adapters report usage per family: Anthropic (`input_tokens` excludes cache
reads and writes, which are added back so `prompt` is always the full input),
OpenAI-compatible (`prompt_tokens_details.cached_tokens`), Codex (the
`response.completed` event), Gemini and Bedrock. A path that cannot see usage
(text-delta streaming) still records the call with unknown tokens; unknown is
never recorded as zero, and a model missing from
`providers/model_prices.json` has cost `null`, never `0`. Totals carry
`calls_without_usage` and `calls_unpriced`, so a cost with either non-zero is a
lower bound.

A total's `cost_usd` follows the same rule (FORGE-486): it is `null`, never
`0.0`, when no call in the total was priced, because a zero reads as "free" in
a live view and slips under any budget check. When only some calls were priced
it covers those, with `calls_unpriced` beside it. Subscription-billed
providers (`openai-codex`, Codex via ChatGPT) have no per-call price, so they
are not priced at zero either: their calls count in `calls_subscription` and
each total carries `billing` (`metered`, `subscription` or `mixed`). The
runaway-spend alert sums priced cost only and treats an unknown total as
neither zero nor over the threshold.

Events are stored in a SQLite file (`llm_usage.db` beside the run ledger, or
`METAFORGE_LLM_USAGE_DB_PATH`) that the gateway and the design-flow worker both
write. Where they read it:

- `GET /v1/runs/{id}` returns `usage`, and `GET /v1/runs/{id}/flow-state` (and
  so `flow.status`) returns `usage` on the run and on each phase: totals plus
  `by_phase`, `by_role` and `by_model`;
- `GET /v1/runs/usage/summary?window_hours=24` returns the trailing-window
  summary, and `health.check` includes it as `llm_usage_24h` (`available: false`
  with a reason when the store cannot be read).

Metrics: `metaforge_llm_tokens_total{role, provider, model, kind}` (`kind` is
`prompt`, `completion` or `cached_input`), `metaforge_llm_cost_usd_total{role,
provider, model}`, `metaforge_llm_calls_total{..., usage_reported}` and
`metaforge_llm_run_spend_exceeded_total{role}`. Per-run cost is not a label
(unbounded cardinality); instead the gateway bumps the last counter once when a
run's accumulated spend crosses `METAFORGE_RUN_SPEND_ALERT_USD` (default 5) and
logs `llm_run_spend_exceeded` with the run id. The `LlmRunawaySpendPerRun`
alert fires on it.

### Per-role model routing (FORGE-477)

One provider and model used to serve every role. A routing table
(`providers/routing.py`, data in `providers/model_routes.json`) now maps a role
to a `provider:model` route. Roles are `flow_generator`, `phase_brain`,
`phase_brain:<discipline>`, `gate_check`, `classification`, `summarisation` and
`chat`; the role is the one FORGE-476's `usage_scope` already attributes.

Routing is opt-in per deployment. The shipped `model_routes.json` has no
routes, so every role runs on the durable harness selection
(`PUT /v1/harness/selection`), then env, exactly as before. A suggested split
lives in `providers/model_routes.example.json` (it is not loaded): the cheap,
fast model (`claude-haiku-4-5-20251001`) for `flow_generator`, `gate_check`,
`classification` and `summarisation`, and the strong model (`claude-opus-4-8`)
for `phase_brain:mechanical` and `phase_brain:simulation`. Copy it, edit it to
providers the deployment has credentials for, and point
`METAFORGE_MODEL_ROUTES_PATH` at it. Today `gate_check` and `summarisation` make no
model call (gates and trajectory summaries are deterministic); the routes exist
so the first model-backed check or summary lands on the cheap model.

Precedence, highest first: an explicit per-call provider/model, the running
phase's own `model` (flow-generator input I5), the project's routes, the table
(file, then env), then the durable selection. For `phase_brain` the phase's
disciplines are tried first (`phase_brain:mechanical`), then `phase_brain`.

Configuration:

- `METAFORGE_MODEL_ROUTES_PATH` points at a replacement file of the same shape;
  `METAFORGE_ROUTE_<ROLE>=provider:model` overrides one role (role upper-cased,
  `:` and `-` as `_`, for example `METAFORGE_ROUTE_PHASE_BRAIN_MECHANICAL`).
- A project carries its own routes under `projects.<project_id>.roles` in the
  file. Chat turns and design-flow phases scoped to that project use them.
- A phase's `model` is a `provider:model` string on the flow phase. The
  generator can propose it with the `set_model` operation; it is part of the
  frozen flow (its hash is unchanged when unset, so older frozen flows still
  verify) and shows in the version diff.

Validation: every route, env override, project route and phase model is checked
with `model_family_mismatch` (FORGE-468) plus provider and role names. A bad
route raises `RoutingConfigError` and is never dropped quietly: the gateway
refuses to start on a bad table, a hand-edited or generated flow whose phase
model cannot work fails the `phase-model-routable` invariant (the generator
drops that one operation instead), and the Temporal worker treats it as a
non-retryable failure. A routed primary keeps the usual fallback chain behind
it.

Credentials: a route to a provider with no credentials on the deployment (the
same check `GET /v1/harness/providers` reports as `configured`) is refused, not
used and not skipped. At call time it raises `RoutingConfigError` naming the
role, route and provider; at gateway start it is logged as
`model_route_provider_unconfigured` without blocking boot, since a login can
arrive later; `GET /v1/harness/routing` marks each route `configured` and lists
`problems`, and `health.check` reports `model_routing.status: degraded` with
the same list.

Provenance: each resolution logs `llm_route_resolved` with role, source
(`phase`, `project` or `table`), provider and model, and the usage event for the
call carries the role and the provider/model that served it, so
`by_role` and `by_model` in FORGE-476's totals show the cheap and strong models
side by side. `GET /v1/harness/routing[?project_id=]` and `health.check`
(`model_routing`) show the effective routes; roles absent from the response run
on `default_provider` / `default_model`.

## Success criteria (from MET-547)

- Same agent loop runs against Anthropic, OpenAI, OpenRouter, and local
  (vLLM/Ollama) with zero code change.
- Automatic failover on 429: rotate profile → fall to next model, session preserved.
- Evaluator runs on a different provider than generator (bias independence).
- Any MCP tool server (simulators, CAD) callable via the central registry.
- MetaForge drivable BY an external harness (Claude Code) via `mcp serve`.
- Consequential tools enforce gate preconditions server-side (external-client safe).
- A `SKILL.md` skill composes tools + instructions without new code paths.

## Testing

Every module ships unit tests (Level 2) with in-memory doubles — the provider
pipeline uses a fake `invoke` to exercise retry/fallback/exhaustion paths with
zero network. Integration tests (Level 5) wire the pipeline into the existing
`ThreeAgentHarness` with a fake provider. `ruff` + `mypy --strict` clean.

## Risks & open questions

- **Layering** — where the MCP tool registry lives (Phase 2); see layering note.
- **ADR mirror** — this decision should be mirrored into the Planner ADR log as
  a formal successor to ADR-008.
- **Scope** — 50 points across 4 phases; sequenced so each phase is
  independently shippable behind the existing harness contract.

## Related

- ADR-008 — External Harness Strategy (the decision this revisits)
- MET-524 — MCP-Driven Live Product Generation Orchestrator (this is its runtime)
- MET-543 — Server API MCP client for simulators (tools this harness consumes)
- MET-474 / MET-475 — the existing three-agent harness this wraps

## Auth model (MET-551) — multi-credential + rotation + dead-token handling

Mirrors Hermes's auth surface:

- `CredentialStore` (`~/.metaforge/credentials.json`, or `METAFORGE_CREDENTIALS_PATH`; written `0600`) holds **multiple credentials per provider** with **dead-token blacklisting** (`mark_dead` / `healthy`).
- `ProfileRotor` pins one credential per session (cache warmth) and rotates on failure.
- `rotating_invoke(base, rotor, session_id, on_dead=...)` applies the pinned credential to the ProviderSpec and, on an auth/rate failure (401/403/429), rotates to the next healthy profile **before** the pipeline falls through to the next provider.
- `store_backed_invoke(base, store, provider, session_id)` is the glue: builds the rotor from the store's healthy credentials and, on a **terminal** 401/403, writes the credential back to the store as dead (a transient 429 rotates but is **not** blacklisted).

**Remote/headless auth (fidel-dev):** the harness runs where the gateway runs. Do the one-time `codex login --device-auth` on that host (or SSH-forward `:1455`, or copy `~/.codex/auth.json`); token refresh thereafter is pure HTTPS (no browser). `chmod 600` the auth file on shared boxes.

## Context compaction (MET-568)

The live loop manages its context window deterministically, in four layers:

- **Token-budgeted history** — prior chat turns are budgeted by tokens
  (`METAFORGE_HISTORY_TOKENS`, default 12k), newest kept whole; turns that no
  longer fit fold into a deterministic, content-preserving summary prepended
  as a leading context exchange (`summarize_turns`). Facts stated early in a
  long conversation survive as summary lines. The full history stays in the
  chat store, so the summary is recomputed per turn rather than persisted.
- **Within-turn folding** — a native tool-calling turn grows by an assistant
  `tool_calls` message plus tool results per iteration; past ~60% of the
  model's context window (`trace_token_budget`) older exchanges fold into one
  synopsis message (`compact_native_messages`). The ReAct path applies the
  same idea through `compress_trace` on its rendered trace.
- **Loud truncation** — oversized tool observations are capped with an
  explicit `…[truncated N chars]` marker (`truncate_observation`), never a
  silent slice, on both paths.
- **Post-turn telemetry** — `context.stats` is re-emitted after the loop with
  `phase: "final"` and a `trace_tokens` estimate, so the meter reflects what
  the turn actually consumed, not just the pre-loop snapshot.

All folding is deterministic (no model calls): free, instant, reproducible.
**Deferred by design** (documented here, not built): Letta-style context
paging, Zep-style temporal knowledge-graph queries in chat, and auto-memory
files — revisit when the deterministic layers prove insufficient in evals.

## Provider tool-array cap

Context compaction above budgets **tokens**. A provider also caps the
**number** of tools per request, which token accounting cannot see: OpenAI
rejects a `tools` array longer than 128 entries with

```
400 invalid_request_error / array_above_max_length
"Invalid 'tools': array too long. Expected an array with maximum length 128,
 but got an array with length 130 instead."
```

This is a request-*shape* limit, so it fires before the model reads a token —
every turn fails identically, including a bare `hello`, no matter how small
the prompt. It was reached in practice once the gateway registered 130 tools
(12 native + 118 MCP); adding any adapter pushes an OpenAI-family deployment
over it.

`providers.registry.max_tools_for(provider)` returns the cap by API family
(OpenAI: 128; Anthropic publishes none, so `None` = unbounded), and
`_tool_schemas(runtime, max_tools=...)` enforces it request-side.

Selection is **not** a tail slice. `all_tools()` is name-sorted, so slicing
deletes whole adapters alphabetically — `twin.*` and `web.*` go first, which
is exactly backwards. Instead:

- every **native** tool is kept (few, curated, session-critical — e.g.
  `chat.set_project_scope` and the skill layer);
- three more classes are protected the same way (FORGE-94), before the
  round-robin runs: any tool `search_tools` has **pinned**
  (`ToolRegistry.pin`/`pinned_names`); any tool whose name ends
  `_open_session` — a structural dependency for every other tool its own
  adapter registers, so dropping just the session opener while keeping its
  siblings is actively harmful in a way dropping any other single tool
  isn't; and any tool whose name ends one of `_SESSION_CRITICAL_VERBS`
  (`pad_sketch`, `pocket_sketch`, `revolve_sketch`, `loft_sketches`,
  `sweep_sketch`, `list_joints`) — protecting `open_session` alone wasn't
  enough (re-test 2026-09-25): the verbs that actually turn an open
  session's sketch into geometry, and the one that inspects joints just
  added, still sorted into the same dropped tail (a large adapter's own
  alphabetical tail is exactly where the round-robin's global budget ran
  out);
- **companion groups** (FORGE-518) are kept together: if any of
  `twin.stage_work_product_file`, `freecad.import_step`,
  `freecad.export_model`, `twin.commit_geometry`, `freecad.open_session` or
  `freecad.close_session` is kept, so are the rest of its group
  (`describe_step_file` rides with the stage/import pair), so the cap can never
  split a stage -> import or export -> commit round trip;
- the remaining budget is filled **round-robin across MCP origins**, so each
  adapter keeps a share and no capability vanishes wholesale; niche families
  (`gazebo`, `isaac_sim`, `omniverse_usd`) only receive what the other
  adapters leave over, so the cap bites them first;
- the drop is **loud** — `tool_schemas_truncated` logs the limit, counts, and
  the dropped tool names, and `chat_tools_dropped` repeats them at info,
  once per distinct drop set within a turn (FORGE-518).

Capping is a safety net, not a substitute for choosing. A deployment near the
limit should narrow the tool set deliberately (the dashboard's tool selector
sends `enabled_tools`; the forge CLI currently sends none and therefore always
ships the full registry).

### Domain scoping and dynamic discovery

`enabled_tools` is a *user's* explicit choice. `mcp_tools_from_bridge`'s
`domains` param is the analogous lever for a *caller* that already knows
which disciplines a turn belongs to: the design-flow executor passes
`domains=phase.disciplines` (`flow_brain.py`), and only the always-visible
core adapters (`twin`, `project`, `session`, `knowledge`, `memory`,
`constraint`, `web`, `component`, and the distributor adapters) plus each
named discipline's own tools are registered — `tool_ids_for_domains`
(`skill_registry/skill_context.py`) reuses the tool scope every skill already
declares via `tools_required`, so a mechanical-only phase never sees
`kicad.*`/`spice.*` schemas at all. `domains=None` (the default) registers
everything, unchanged from before this existed.

Plain project-scoped chat (FORGE-300) derives its own `domains` signal from
the project itself, no new field or migration required: the disciplines its
recorded work products already evidence (a `cad_model` implies mechanical, a
`schematic` implies electronics, ...) unioned with a keyword scan of the
project's own `description` for the brand-new-project case where nothing has
been recorded yet (`_infer_project_domains`,
`api_gateway/chat/routes.py`). Either signal being empty falls back to
`domains=None` — under-scoping costs nothing, so a false negative is always
preferred over guessing. Non-project ad-hoc chat still has no scoping signal
and stays unscoped.

Scoping down trades completeness for headroom, so a scoped turn can still
call `search_tools` — a native tool that searches the *full* catalog by
keyword and registers any match directly onto the live `ToolRegistry`. Since
`_tool_schemas` is recomputed every round-trip (not once before the loop), a
tool `search_tools` registers mid-turn is callable by the model on its very
next step. Its handler is built before `build_agent_runtime` constructs the
`ToolRegistry` it registers into, so it reads the live runtime out of a
mutable cell (`runtime_cell["runtime"]`, populated right after
`build_agent_runtime` returns) rather than closing over it directly.

**"Registered" isn't the same as "in the schema" (FORGE-94).** A tool
already present in the `ToolRegistry` — because an earlier `search_tools`
call added it, or it was in scope all along — is only reported as "already
available," never re-registered. But *registered* and *sent to the
provider this turn* are two different questions: `_select_tools` truncates
the outgoing schema array separately, per call, against the provider's
tools-array cap, and a merely-registered tool has no special protection
from that round-robin. Without pinning, `search_tools` could report a tool
as available on one turn and have the very next turn's cap silently drop it
again — a promise the model has no way to detect is false. The handler now
calls `runtime.tools.pin(full_name)` for every match, registered or
already-known, so the promise is actually kept.

## Surfacing turn failures

When a harness turn raises, the route emits a `notify_error` SSE event
carrying the exception text *before* `agent.done`. Without it the client saw
typing, then `agent.done`, then nothing — which the forge TUI reports as
`(no reply — the agent produced no output)`, with the real cause visible only
in gateway logs. `notify_error` had existed unused since MET-219 while the TUI
already handled the event; only the call site was missing. The error
`ChatMessageRecord` the route returns does not close this gap on its own — the
TUI renders streamed assistant deltas, not the returned record.

## Tool-call hardening (MET-569)

Four properties the tool-calling loop enforces, so a model's mistake costs a
message rather than an invocation:

- **Schema pre-validation** — `ToolRegistry.invoke` validates model-emitted
  arguments against the tool's declared `input_schema` before the handler runs
  (`orchestrator/harness/validation.py`). A missing `required` field, a wrong
  JSON type, or a value outside a declared `enum` raises `ToolValidationError`,
  which the loop returns as `{"status": "error", "error": "invalid_arguments",
  "validation_errors": [...], "hint": "...NOT executed"}` — the model
  self-corrects without an adapter round-trip or a half-applied side effect.
  Tools whose manifest declares no schema keep the permissive
  `{"type": "object"}` fallback and are not validated. `jsonschema` is used
  when installed; a built-in structural check covers required/type/enum
  otherwise.
- **Parallel batched execution** — a provider emitting several independent
  calls now has them executed with `asyncio.gather` instead of one at a time,
  with per-call exception isolation (one failure never cancels its siblings)
  and results reassembled in the model's original call order, which is how
  providers match results to call ids. A batch containing a
  `requires_approval` tool runs serially: concurrent approval prompts race for
  one approver, and a queued call can hit its deny-by-default timeout while
  the person is still answering the first.
- **Same-turn duplicate guard** (both loops) — calls are keyed by `(tool,
  canonical arguments)`. An identical repeat of an already-successful call, whether in
  the same batch or a later step of the same turn, reuses the stored
  observation and returns it marked `{"cached": true, ...}` without re-running
  the tool. Failures are never cached, so a genuine retry really retries, and
  the cache is turn-scoped, so the same call in a later turn is treated as a
  legitimate re-read of current state.
- **Structured error pass-through** (both loops) — a failed call returns a
  JSON envelope instead of the old `ERROR: <str(exc)>` line. `McpToolError` carries the
  adapter's own envelope (`payload`), so its status, message, and any hint
  survive into `details` and the model can distinguish "the container is down,
  stop trying" from "that argument was wrong, fix it".

### Which loop gets what

The dedup guard and structured errors are **loop-agnostic** and live in
`orchestrator/harness/tool_exec.py` (`TurnToolCache`, `cached_view`,
`error_content`), so both `run_native_tools` and `run_react` call in. They
originally shipped inside the native loop only, which meant any provider whose
adapter cannot parse native `tool_calls` — Gemini, Bedrock, anything else
`native_tools_enabled()` returns False for — fell back to ReAct and silently
kept the old behaviour. Losing a correctness property because of which
provider a deployment picked is a bad trade.

Schema pre-validation was always shared (it lives in `ToolRegistry.invoke`), as
are the gate declarations below. **Parallel batched execution stays
native-only** and always will: the JSON-ReAct protocol emits exactly one tool
call per step, so there is never a batch to parallelise.

| Property | Native | ReAct |
| --- | --- | --- |
| Schema pre-validation | ✅ | ✅ (shared registry) |
| Parallel batched execution | ✅ | n/a — one call per step |
| Same-turn duplicate guard | ✅ | ✅ (shared `tool_exec`) |
| Structured error pass-through | ✅ | ✅ (shared `tool_exec`) |
| Gate declarations | ✅ | ✅ (shared registry) |

### Gate declarations for chat tools

`ToolSpec.required_gates` had existed since Phase 1 with nothing declaring a
gate, so the mechanism was dead code and chat could always write. Persistent
twin and project mutations (`twin.commit_geometry`, `twin.record_decision`,
`twin.record_constraint_set`, `twin.record_document`, `twin.propose_change`,
`twin.stage_work_product_file`, `project.create/update/delete`) now declare
`twin_write` / `project_write`, and the chat runtime wires the evaluator that
has to exist alongside them — a gated tool with no evaluator never runs at all.

Both gates default to satisfied, so an existing deployment behaves exactly as
before; `METAFORGE_CHAT_TWIN_WRITES=0` / `METAFORGE_CHAT_PROJECT_WRITES=0`
give an operator a genuinely read-only chat surface. Reads and `freecad.*` /
`cadquery.*` authoring stay ungated — the latter only ever touch the adapter's
ephemeral workspace. The gate is a static precondition; the per-call human
decision for the same tools remains the separate "ask" tier
(`requires_approval`).

**Skill-layer tools too (FORGE-97).** A skill like `generate_cad` persists a
work product into the Twin internally — via
`domain_agents.shared.commit_geometry.commit_geometry`, a direct
`McpBridge.invoke("twin.commit_geometry", ...)` call — not as a separately
model-callable step, so the model calling `twin.commit_geometry` directly
paused for approval while calling `skill_mechanical_generate_cad`, which
commits the exact same kind of node, never did. `api_gateway/chat/skill_tools.
py`'s `_tool_for_registration` now gates any skill whose Pydantic input model
declares a `commit` field the same way — `required_gates=(GATE_TWIN_WRITE,)`,
`requires_approval=True` — schema-driven rather than a per-skill name list,
matching FORGE-81/82's `_declares_project_id` precedent, so
`generate_enclosure`/`create_assembly`/`generate_cad_ir` (today) and any
future commit-capable skill are covered automatically. Deliberately static,
like every other gate here: a call that happens to pass `commit=False` still
pauses — a false-positive prompt is a minor cost, not a silent bypass.

### Validate before pausing for approval (FORGE-222)

`HarnessRuntime.call_tool` runs the declared-schema check
(`orchestrator.harness.validation.validate_arguments`) BEFORE checking
`requires_approval`, not after. Before this a gated tool's arguments were only
validated inside `ToolRegistry.invoke`, which runs after the approval pause —
so a human could be asked to approve a call that was always going to be
rejected as invalid the moment it actually ran (live-observed: 13 large
Design IR documents approved one at a time, most then failing validation).
`ToolRegistry.invoke` still re-validates on the approved path too (cheap,
and keeps it a safe standalone entrypoint for other callers) — this only
moves *when* the very first check runs.

### Approval UI for the "ask" tier (FORGE-33)

`requires_approval` (`twin.commit_geometry`, `twin.record_decision`,
`project.create/update/delete`) pauses the run server-side
(`HarnessRuntime._await_approval`,
`api_gateway/chat/tool_approvals.py`'s `InMemoryRunStore`) and broadcasts a
`tool.approval_requested` SSE event (`run_id`, `tool`, `arguments`) on the
same `/v1/chat/threads/{id}/stream` the turn is already using. The resolution
endpoint (`GET`/`POST /v1/chat/tool_approvals[/{run_id}]`) was real from the
start, but until FORGE-33 no client consumed either — a paused call had no
UI anywhere and always hit the deny-by-default timeout
(`chat_approval_timeout_seconds`, 1800s default).

Two clients now do:

- **TUI** (`tui/src/components/ToolApprovalModal.tsx`) — an inline `[a]
  approve` / `[x] reject` prompt in the chat view itself, the same
  keypress convention `GateModal` already used for design-flow gate
  approvals (a different mechanism, `/v1/runs/{id}/approval`). The turn's
  own HTTP request stays open server-side while the human decides;
  submitting a decision doesn't reconnect anything, the same SSE stream
  just resumes delivering whatever comes next.
- **Dashboard** (`dashboard/src/pages/ApprovalsPage.tsx`, "PENDING TOOL
  CALLS" panel) — a polled list (`usePendingToolApprovals`, 5s interval;
  no chat UI exists in the dashboard to hook an inline prompt into, so
  this is deliberately async rather than live) alongside the existing
  Proposals panel, since both answer "what needs my decision right now."

Neither client changed the backend — the SSE event and REST endpoint were
already complete; the gap was entirely "no consumer."

### Approval tier in design-flow phase turns (FORGE-490)

The in-process hold above only works where something can answer it: the gateway,
which serves `/v1/chat/tool_approvals`. A design-flow phase turn runs on the
design-flow worker, a separate process whose `InMemoryRunStore` no dashboard,
plugin or person can see, so every `requires_approval` call it made used to time
out and be denied.

`run_chat_turn` and `HarnessRuntime` now take an explicit `approval_mode`:

- `"hold"` (default, dashboard chat and the TUI): unchanged. The call is parked
  and waits for a human.
- `"forward"` (set by `ReActPhaseBrain.run_phase`): no in-process hold. The call
  goes straight to the tool, which for an MCP tool is the sidecar, where the
  service-caller policy decides: in-scope writes run, refused ones come back
  refused, and human-authority tools go to the shared approval ledger. The run is
  already authorised by the flow-version approval and the phase gates.
  Only MCP tools are forwarded. A `requires_approval` tool that runs in-process
  (a native harness tool) would get no policy check at all, so in this mode it is
  refused: not run, not held. The model sees a tool error saying it is not
  available in an unattended design-flow turn, and
  `approval_tool_refused_unattended` is logged.

Any hold created in a process with no reachable approver (the gateway marks
itself reachable at startup; a worker never does) logs
`approval_hold_unreachable` at error level and increments
`metaforge_unreachable_approval_hold_total`; the `ApprovalHoldWithNoApprover`
alert fires on any increase.

### Tool-approval durability (FORGE-89)

`api_gateway/chat/tool_approvals.py`'s `InMemoryRunStore` was process-local
only — a gateway restart silently dropped any approval record, pending or
resolved, with no trace it ever existed. It now writes through to a
`SqliteRunLedger` (`orchestrator/harness/ledger.py`,
`default_tool_approvals_ledger_path()` — a separate file from the
design-flow run ledger, `~/.metaforge/tool_approvals_ledger.db` by default,
override via `METAFORGE_TOOL_APPROVALS_LEDGER_PATH`), wired in
`api_gateway/server.py` alongside the existing `/v1/runs` ledger and gated
behind the same `METAFORGE_RUNS_LEDGER_DISABLE` flag.

This deliberately does **not** mirror design-flow's `init_run_ledger()`,
which rehydrates non-terminal runs as resumable: a design-flow run's phase
executor can genuinely pick back up after a restart, but the chat turn that
was polling a paused `HarnessRuntime._await_approval()` coroutine cannot —
the coroutine (and the SSE stream feeding it) died with the process. So a
restored `awaiting_approval` row is marked `failed` with an
`"orphaned: gateway restarted while this approval was pending"` error
instead of `awaiting_approval` — the record persists for audit/history, but
nothing implies it is still actionable.

### Post-turn grounding guard (FORGE-98)

A chat agent can claim a design action was performed with **zero tool
calls** that turn — confirmed live: a turn made no tool calls at all (no
tool events in the gateway log), yet its final reply read "Assembled the
available parts... Added revolute joints J1 to J6... Part Count: 6
components." Nothing existed. `NATIVE_SYSTEM`
(`orchestrator/harness/native_tools.py`) already instructed the model
"Never claim an action was performed unless one of your tool calls actually
performed it" — that alone wasn't reliable enough with a weaker model
(`openai/gpt-4o` via OpenRouter in the repro) to prevent this, which is
exactly why a deterministic backstop exists alongside the prompt rule, not
instead of it.

`harness_backend._flag_if_unfounded_completion_claim(answer, steps)` runs on
the model's own final text (never on `summarize_trajectory`/the fallback
string, which are generated *from* the step trace and are inherently
grounded) in both `run_chat_turn` and `run_chat_turn_streaming`: if the reply
contains a completion verb (`assembled`, `created`, `committed`, `recorded`,
`generated`, `built`, `added`, `designed`, `exported`) and the turn made no
*successful* tool call (`ReActStep.tool_call is not None and error is None`),
it prepends a visible `⚠ No tool calls were made this turn...` warning. A
false positive costs one extra banner line; a false negative is the actual
bug this fixes — that asymmetry is why it leans toward over-flagging rather
than trying to parse intent. This is a cheap, provider-agnostic heuristic,
not a real claim-vs-evidence checker — it can't catch a turn that called a
tool but then reports the *wrong* numbers from it (also part of the same live
repro: a `create_parametric` call returned a default 1570.8 mm³ pin, and the
agent reported "150mm x 150mm x 20mm... created with the specified
features" anyway). Promoting `evals/judge.py`'s own LLM-graded `grounding`
transcript dimension into a live per-turn check would catch that class too,
at the cost of an extra judged model call per turn — tracked separately
(FORGE-104), not folded in here.

### Claim-specific grounding (FORGE-520)

The FORGE-98 check passes any turn with one successful tool call, so it
missed a live failure: a turn made many successful FreeCAD session calls
(`import_step`, `create_assembly`, `export_model`), never called
`twin.commit_geometry`, then replied that the assembly was saved and quoted
`assembly_4` (a FreeCAD session object id) as its node id. The same function
now runs two more deterministic checks after the FORGE-98 one, each with its
own banner from `orchestrator/design_flow/grounding.py`. Neither adds a model
call.

| Check | Fires when | Banner |
|-------|-----------|--------|
| No tool call (FORGE-98) | a completion verb and no successful tool call at all | `UNGROUNDED_BANNER` (the reply gets only this one) |
| Twin write | the reply claims a twin write and no twin write call succeeded this turn | `NO_TWIN_COMMIT_BANNER`: "No twin commit happened this turn..." |
| Node id | the reply quotes a node id no successful tool call this turn returned | `UNVERIFIED_NODE_ID_BANNER` plus the ids |

A **twin write claim** is a sentence with `committed` or `persisted`, or with
`saved`/`stored`/`recorded`/`written` when the reply also mentions the twin, a
node id or a work product, so "saved it to /tmp/shelf.step" alone is not one.
A sentence that is a question or carries a negation, plan or offer ("not
committed yet", "I will save it", "want me to commit it?") is skipped.

A **twin write call** is read from the gate classification: a tool whose
registry spec carries `GATE_TWIN_WRITE`, which covers the twin tools in
`_GATED_TOOL_IDS` and every commit-capable skill (one called with
`commit: false` does not count). When no registry is passed, the static
`_GATED_TOOL_IDS` set is used. A call only succeeds if it raised no error and
its result is not an error envelope (`status: "error"`, `success: false`).

A **node id** is any UUID in the reply, or an id-shaped token after `node id`,
`node_id`, `work product id` or `wp id`. It is grounded when it appears in the
arguments or result of a successful call this turn, or in the user's own
message. A FreeCAD session object id (`part_N`, `assembly_N`, `body_N`, and
similar) presented as a node id is never grounded, whatever returned it. A
node id carried over from an earlier turn is flagged too: the banner says it
was not verified this turn, not that it is wrong.

Only `UNGROUNDED_BANNER` changes a design-flow phase status (`is_ungrounded`);
the two new banners are chat-only. Each flag logs a `chat_ungrounded_claim`
warning with its `kind` and increments
`metaforge_chat_ungrounded_claim_total{kind="no_tool_call"|"twin_write"|"node_id"}`;
the `ChatUngroundedClaimsSustained` alert fires when one kind passes 5 in 30
minutes.

## Prompt caching (FORGE-478)

Providers cache by exact prefix, so every harness request is laid out as a
stable prefix followed by the volatile suffix, and the prefix is kept
byte-identical across the steps of a turn and across turns while its inputs
are unchanged.

| Part | Content | Stable? |
|------|---------|---------|
| Prefix | tool schemas (sorted by name, keys sorted), system prompt | yes |
| Suffix | conversation history, latest tool results, per-round `system_suffix` note | no |

The layout lives in `orchestrator/harness/providers/caching.py`:

- **Anthropic** gets explicit `cache_control` breakpoints on the last tool, the
  stable system block and the last message block (3 of the 4 allowed), so each
  step reads the previous step's history from the cache.
- **OpenAI and OpenRouter prefix caching** is automatic, so the
  adapters only guarantee the ordering and send a stable `prompt_cache_key`
  (a hash of system prompt and tools) as `extra_body` on OpenAI and OpenRouter
  endpoints. Self-hosted OpenAI-compatible servers do not receive it.
- The tools-array truncation note is a per-round `system_suffix`. Anthropic
  sends it as a second, unmarked system block; other families append it to the
  system text. It never alters the cached system block.
- Nothing time- or id-dependent may enter the prefix. Anything that varies per
  step belongs in the suffix.

Cached tokens are reported through the FORGE-476 accounting
(`providers/usage.py`): Anthropic `cache_read_input_tokens` and OpenAI
`prompt_tokens_details.cached_tokens` land in `cached_input_tokens`, priced at
the model's `cached_input` rate. Compare `cached_input_tokens` to
`prompt_tokens` in `GET /v1/runs/{id}` usage totals to see the hit rate.
Gemini, Bedrock and Codex Responses calls are not given cache markers yet.
