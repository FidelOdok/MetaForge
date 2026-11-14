# Where MetaForge uses a language model

Which parts of MetaForge call a language model, which settings drive each one,
and what still works when no model is available. Use it when you deploy
MetaForge (which credentials do I need?), when a provider runs out of credit
(what stops?), or when you change a model setting.

There are **three independent model configurations**. In the default
deployment all three bill the same OpenRouter account, so running out of
credit there stops all three at once.

| Configuration | Settings | Used by |
|---|---|---|
| [Harness model](#1-the-harness-model) | `METAFORGE_LLM_PROVIDER`, `METAFORGE_LLM_MODEL`, `METAFORGE_LLM_API_KEY`, plus optional per-role routes | Design-flow phases, chat, prompt-to-CAD, server-side flow generation, requirement intelligence, domain agents |
| [Knowledge models](#2-the-knowledge-models) | `OPEN_ROUTER_API_KEY`, `LIGHTRAG_MODEL`, `PROPERTY_EXTRACTION_MODEL` (each with a `*_FALLBACK_MODEL`) | Knowledge ingest and search, datasheet property extraction, component search by intent |
| [Memory consolidation model](#3-the-memory-consolidation-model) | `OPEN_ROUTER_API_KEY`, `CONSOLIDATION_MODEL` (and `CONSOLIDATION_FALLBACK_MODEL`) | Turning past agent experience into reusable insights |

## 1. The harness model

Everything here goes through the harness provider registry
(`orchestrator/harness/providers/`). The provider can be OpenRouter,
Anthropic, OpenAI, the OpenAI Codex subscription, Gemini, Bedrock or Ollama.
A role can be routed to its own `provider:model` (see
[Per-role model routing](robust-harness-design.md#per-role-model-routing)).
Each call is recorded in the usage ledger under its role.

| Area | Code | Role | What the model does |
|---|---|---|---|
| **Design-flow phases** | `api_gateway/runs/*_handlers.py` (requirements, architecture, concept, mechanical, electronics, firmware, V&V, manufacturing) and `api_gateway/runs/flow_brain.py` (the generic ReAct phase brain) | `phase_brain`, `phase_brain:<discipline>` | The engineering work of every phase of a run: records the intent, needs and requirements, authors CAD, sets up and reads analyses, and produces each gate's deliverables. **Every phase of every run needs it.** |
| **Chat** (dashboard chat, `forge chat`) | `api_gateway/chat/harness_backend.py` | `chat`, `generator` | The tool-calling agent behind each chat turn |
| **Prompt-to-CAD** | `api_gateway/cad/routes.py` | `generator` | Turns a text prompt into geometry through a chat turn |
| **Server-side flow generation** | `api_gateway/design_flows/generate.py` | `flow_generator` | Tailors a template when `flow.propose` is called *without* caller operations, and suggests extra clarifying questions |
| **Requirement intelligence** | `api_gateway/requirement_intelligence/intent_interpreter.py`, `requirement_author.py` | `generator` | Interprets an intent into engineering entities, and drafts requirements |
| **Domain agents** (PydanticAI mode) | `domain_agents/{mechanical,electronics,simulation,firmware,supply_chain}/agent.py` | (agent run) | Chooses which skills and tools to call for a task. With no model configured each agent uses its fixed task dispatch instead. |

### What a harness turn costs, and how it is bounded

A turn is a loop of model calls, and every call carries the whole prompt: the system
text, the conversation so far and the tool list. In a measured Imagine session a call
averaged about 65,000 input tokens, of which the tool list was about 28,000, and a turn
made several such calls. Caching makes most of that cheap to read, not free.

- **Cost is recorded per call.** A router that bills per call (OpenRouter) reports the
  cost it charged, and the adapter asks for it (`usage: {include: true}`). That reported
  cost is exact, so it is stored in place of a price-table estimate, and it covers
  models the table does not list. A call with neither a reported cost nor a table price
  records `None`, never zero.
  With a key brought to the router (BYOK) OpenRouter's `cost` is only its own fee, often
  0, and the real spend is `cost_details.upstream_inference_cost`; both are added, so a
  large call never reads as free.
- **The spend cap uses it.** `METAFORGE_CHAT_MAX_COST_USD` used to estimate cost from the
  built-in price table, so for a model missing from that table the cap was silently not
  enforced. When every call of a turn reports a cost, the loop sums them, shows the
  total on the result (`usage.cost_usd`) and stops at the cap. If some call reports no
  cost the total is left out rather than understated, and the cap falls back to the
  table estimate.
- **The tool list is measured.** The loop logs `native_tool_schemas` (tool count and
  approximate tokens) at its first step and whenever the list changes, for example when
  `search_tools` adds a tool. In a measured Develop turn the list was 60 tools and about 31,000 tokens: 39 stage tools
  (about 13,000) plus 21 native tools, 16 of them `skill_*` tools worth about 17,000 tokens
  (`skill_mechanical_generate_cad_ir` alone is about 7,300). Those are registered only when
  `METAFORGE_CHAT_SKILLS` is on, which is off by default; leave it off unless a turn needs them. A Design turn that names its Create stage gets that
  stage's set (`mcp_core.profiles.CREATE_STAGE_TOOLS`, 35 to 40 tools, about 8,000 to
  13,000 tokens); a turn that does not sends the person's own tool ids.

### Budgets: who a call is for, and whether it may be made

Every recorded call carries who it is for as well as what it cost: the run, phase,
role and caller, and also the **project** and the **verified caller** (the
`Principal.subject` from sign-in). On a gateway with sign-in, the authentication
middleware sets both for every request, so every model call the request makes, in
tasks it starts too, is attributed. A design-flow phase sets the project from its run,
because in the Temporal worker no request is in flight. A local gateway without
sign-in records neither, and that is fine: nobody meters a laptop.

Whether a call may be made at all is a seam, like authentication. An implementation
registers on the `metaforge.budget` entry-point group and `METAFORGE_BUDGET_MODE` names
it:

| `METAFORGE_BUDGET_MODE` | Behaviour |
|---|---|
| unset, `off` or `none` | No budget. Calls are recorded as above and never refused. |
| a registered name | Checked before **every** model call, and told what each call cost after it |
| a name nothing registered | The gateway and the design-flow worker **refuse to start** |

- **Refused before it is sent.** The check runs before the first provider is tried, on
  the plain, streaming and event-streaming paths alike. A refusal is not a provider
  failure, so it is never retried and never handed to the fallback model; the person
  sees the policy's own message, such as "Monthly AI budget reached for this account."
- **Fails closed.** If the policy cannot reach its own totals, the call is refused with
  "The AI budget could not be checked", not let through unmetered.
- **Recording never breaks a call.** A policy that fails to record is logged
  (`llm_budget_record_failed`) and the call's result is returned as usual.

Open core ships no policy. A hosted deployment supplies one that keeps a budget per
account and totals in its own database, shared by every gateway and worker process; the
local `llm_usage.db` file is per host and is not that total.

## 2. The knowledge models

These are direct OpenRouter clients in `digital_twin/knowledge/`, wired at
gateway start-up (`api_gateway/server.py`). They read `OPEN_ROUTER_API_KEY`,
not the harness key.

| Area | Setting | What the model does |
|---|---|---|
| **LightRAG** (`openrouter_lightrag.py`) | `LIGHTRAG_MODEL` | Extracts entities and relationships from documents during `knowledge.ingest`, and builds graph-aware answers for `knowledge.search` |
| **Property extraction** (`openrouter_property_llm.py`) | `PROPERTY_EXTRACTION_MODEL` | Reads typed values, with citations, out of datasheets (`knowledge.extract`, BOM population). Without it only the deterministic extraction tier runs. |
| **Component search by intent** | the same client as property extraction | `component.search_intent`: turns a goal such as "step 12 V down to 5 V for a flight controller" into parametric search bounds |

When the LightRAG model is a Claude model, its system prompt, which carries the
extraction instructions LightRAG repeats for every chunk, goes out marked with
`cache_control`. Claude behind OpenRouter caches only where it is told, so
without the marker every chunk paid for those instructions in full. The
breakpoint sits on that stable block, never on the end of the request: there it
would pay a cache write per chunk and never read one back. Other models get the
plain string. The property-extraction and consolidation clients are left
unmarked: their system prompts are a few dozen tokens, below the size Claude
caches at all.

The LightRAG UI container (`lightrag-ui`, the `lightrag` compose profile) is
separate: it uses a **local Ollama** model and embeddings, not OpenRouter.

## 3. The memory consolidation model

`digital_twin/memory/consolidation/` groups past agent experiences and writes
reusable insights (`memory.list_insights`). It runs in the gateway and in the
Temporal worker, and reads `CONSOLIDATION_MODEL` through `OPEN_ROUTER_API_KEY`.
With no key it uses a stub client: passes still run, but they synthesise
nothing.

## What needs no model

These are deterministic, and keep working with no provider and no credit:

- the intent compiler (`flow.compile_intent`);
- flow tailoring from **caller operations**, which is what the plugins send;
- flow invariants, capability assessment, gate checks, the lifecycle view and
  completion verdict, patches, and stalled-repair detection;
- every engineering tool: CAD, FEA, KiCad, SPICE, the power budget, firmware
  code generation and BOM risk scoring.

## Planning versus running

A plugin client (Claude Code or Codex) does its own reasoning, so **planning**
a flow through the plugin costs nothing on the server: the client writes the
tailoring, and MetaForge only validates and stores it. **Running** that flow
is different. Each phase is carried out by a phase brain on the harness
model, so a run needs a working harness provider for as long as it runs.

A run started in **client mode** (`intelligence: client`, see
[client intelligence mode](../architecture.md#client-intelligence-mode-and-gates-in-the-chat))
calls no model for its phases at all: each phase is posted as a task, and the
connected client does it with its own model and hands it back. Gates are then
answered by the person in the client's chat. Such a run needs no harness
provider and no credit.

When the harness provider refuses a call (for example OpenRouter's
`402 in_flight_budget_exhausted` once an account is nearly out of credit),
the phase fails with `ProviderUnavailable` and the run stops at that phase.
Phases already approved stay in the twin, and a new run from the same
approved flow version reuses their current outputs.
