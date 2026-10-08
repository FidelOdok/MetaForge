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
[Per-role model routing](robust-harness-design.md#per-role-model-routing-forge-477)).
Each call is recorded in the usage ledger under its role.

| Area | Code | Role | What the model does |
|---|---|---|---|
| **Design-flow phases** | `api_gateway/runs/*_handlers.py` (requirements, architecture, concept, mechanical, electronics, firmware, V&V, manufacturing) and `api_gateway/runs/flow_brain.py` (the generic ReAct phase brain) | `phase_brain`, `phase_brain:<discipline>` | The engineering work of every phase of a run: records the intent, needs and requirements, authors CAD, sets up and reads analyses, and produces each gate's deliverables. **Every phase of every run needs it.** |
| **Chat** (dashboard chat, `forge chat`) | `api_gateway/chat/harness_backend.py` | `chat`, `generator` | The tool-calling agent behind each chat turn |
| **Prompt-to-CAD** | `api_gateway/cad/routes.py` | `generator` | Turns a text prompt into geometry through a chat turn |
| **Server-side flow generation** | `api_gateway/design_flows/generate.py` | `flow_generator` | Tailors a template when `flow.propose` is called *without* caller operations, and suggests extra clarifying questions |
| **Requirement intelligence** | `api_gateway/requirement_intelligence/intent_interpreter.py`, `requirement_author.py` | `generator` | Interprets an intent into engineering entities, and drafts requirements |
| **Domain agents** (PydanticAI mode) | `domain_agents/{mechanical,electronics,simulation,firmware,supply_chain}/agent.py` | (agent run) | Chooses which skills and tools to call for a task. With no model configured each agent uses its fixed task dispatch instead. |

## 2. The knowledge models

These are direct OpenRouter clients in `digital_twin/knowledge/`, wired at
gateway start-up (`api_gateway/server.py`). They read `OPEN_ROUTER_API_KEY`,
not the harness key.

| Area | Setting | What the model does |
|---|---|---|
| **LightRAG** (`openrouter_lightrag.py`) | `LIGHTRAG_MODEL` | Extracts entities and relationships from documents during `knowledge.ingest`, and builds graph-aware answers for `knowledge.search` |
| **Property extraction** (`openrouter_property_llm.py`) | `PROPERTY_EXTRACTION_MODEL` | Reads typed values, with citations, out of datasheets (`knowledge.extract`, BOM population). Without it only the deterministic extraction tier runs. |
| **Component search by intent** | the same client as property extraction | `component.search_intent`: turns a goal such as "step 12 V down to 5 V for a flight controller" into parametric search bounds |

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

When the harness provider refuses a call (for example OpenRouter's
`402 in_flight_budget_exhausted` once an account is nearly out of credit),
the phase fails with `ProviderUnavailable` and the run stops at that phase.
Phases already approved stay in the twin, and a new run from the same
approved flow version reuses their current outputs.
