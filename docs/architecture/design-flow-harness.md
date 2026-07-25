# Design-Flow Harness (Gated Lifecycle)

The design-flow harness turns a product goal into reviewable engineering
deliverables by walking a **gated lifecycle** — a sequence of *phases* with a
human **gate** between each. It is the spine that binds MetaForge's existing
run, gate, agent, and twin machinery into a single "design any product" flow.

Per [ADR-008](https://github.com/FidelOdok/MetaForge-Planner), the *reasoning*
inside each phase is delegated to the external harness (the ReAct loop driving
MCP tools); MetaForge owns the **gated spine** — sequencing, gates, and the
digital thread.

## Phases and gates

A flow is an ordered list of phases; each phase has an objective (handed to the
brain) and an optional gate. Phase 1 ships one built-in flow, `design_v1`:

| Phase | Objective (summarised) | Gate |
|-------|------------------------|------|
| **Requirements** | Functional requirements, constraints, primary load/use case → twin | Requirements sign-off |
| **Detailed Design** | Author the critical subsystem geometry/schematic + rationale → twin | Design review |
| **Simulation & V&V** | Run FEA / ERC-DRC, extract the key result, record a verdict → twin | V&V sign-off |

Adding a phase (Architecture, Digital-Twin consolidation, Release) is a data
change in `orchestrator/design_flow/spec.py`, not new control flow.

## How a run flows

```
POST /v1/runs {request: {goal, flow: "design_v1", project_id}}
      │
      ▼
DesignFlowExecutor.run(run_id)          # orchestrator/design_flow/executor.py
  for each phase:
      brain.run_phase(...)              # ReAct loop + MCP tools → artifacts in twin
      if phase.gate:
          store.request_approval(...)   # run → awaiting_approval  (SSE emits it)
          decision = await gate         # resolved by POST /v1/runs/{id}/approval
          approve → next phase
          reject  → run ends (rejected)
  store.complete(run_id, result)
```

The executor drives the existing
[`InMemoryRunStore`](https://github.com/FidelOdok/MetaForge) state machine, so
the run's status transitions stream over the existing `/v1/runs/{id}/events`
SSE and `/ws` surfaces, and pause/resume uses the existing approval endpoint. A
`GateCoordinator` bridges the async gate wait to the synchronous store
transition triggered by the approval route.

## Driving it from the CLI

```bash
# Start a gated design flow for a product goal, scoped to a project
python -m cli.forge_cli runs create --request-json \
  '{"goal": "quadruped robot leg able to carry 5 kg body mass",
    "flow": "design_v1",
    "project_id": "<uuid>"}'

# Watch phase/gate transitions stream (SSE)
python -m cli.forge_cli runs watch <run_id>

# When the run pauses at a gate, review and pass it
python -m cli.forge_cli runs approve <run_id>
# ...or hold the design
python -m cli.forge_cli runs reject <run_id>
```

A run is treated as a design flow only when it opts in with a `flow` id (or
`kind: "design_flow"`); a bare `{goal}` keeps the plain run semantics.

## Deliverable enforcement ("no work product silently missing")

Each phase declares `required_deliverables` — the work-product *types* it must
record into the twin (e.g. the Design phase requires a `cad_model`). At the
gate, a `GateEvaluator` (backed by the same project store the dashboard reads)
checks which of those types the phase actually recorded during its window:

- **All present** → the gate pauses for human sign-off, showing present/missing.
- **Missing** and the phase is `enforce_deliverables` → the run **fails at the
  gate** with the missing list, rather than silently passing. The Design gate
  cannot pass without a committed, viewable `cad_model`.

This makes completeness machine-enforced and quality human-judged: the machine
guarantees the deliverable exists in the twin; the human reviews whether it's
right.

## What's built vs. planned

**Built (Phase 1, thin vertical):** the `design_v1` 3-phase gated flow, the
executor + gate coordinator wired into `/v1/runs`, the ReAct phase brain,
per-phase deliverable enforcement via `GateEvaluator`, and SSE/CLI drive. Each
phase records its artifacts + decisions into the digital twin via MCP tools.

**Planned (next slices):** the full lifecycle (Architecture → Digital-Twin
consolidation → Release), per-discipline fan-out (a registry routing phases to
mechanical/electronics/firmware agents), weighted **gate-readiness** scoring via
`twin_core/gate_engine` (EVT/DVT/PVT), phase/gate linkage stored on twin nodes,
and a dedicated `forge design` CLI wrapper.

## Key modules

| Module | Role |
|--------|------|
| `orchestrator/design_flow/spec.py` | `Phase` / `Gate` / `FlowDefinition`, built-in flows |
| `orchestrator/design_flow/executor.py` | `DesignFlowExecutor`, `GateCoordinator`, `PhaseBrain` |
| `api_gateway/runs/flow_brain.py` | `ReActPhaseBrain` — per-phase reasoning via the chat harness |
| `api_gateway/runs/routes.py` | Launches the executor on a design-flow `POST /v1/runs` |
