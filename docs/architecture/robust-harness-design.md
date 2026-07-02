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
