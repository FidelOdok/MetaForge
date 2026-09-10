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
brain) and an optional gate. Two flows ship today.

**`design_v1`** — the thin mechanical vertical (deterministic handlers drive the
mechanical phases for reliable geometry):

| Phase | Objective (summarised) | Gate |
|-------|------------------------|------|
| **Requirements** | Functional requirements, constraints, primary load/use case → twin | Requirements sign-off |
| **Detailed Design** | Author the critical subsystem geometry/schematic + rationale → twin | Design review |
| **Simulation & V&V** | Run FEA / ERC-DRC, extract the key result, record a verdict → twin | V&V sign-off |

**`hardware_v1`** — the full hardware/robotics lifecycle. Every phase is driven
by a **goal-driven deterministic handler** (see below) so each phase reliably
lands its real, typed deliverable in the twin:

| Phase | Objective (summarised) | Gate |
|-------|------------------------|------|
| **Requirements** | Functional reqs, environment, quantified constraints (mass/power/DOF/cost), motion/use cases → twin | Requirements sign-off |
| **System Architecture** | Subsystem decomposition, interfaces, mass/power/compute/cost budgets, actuation/sensing/compute/power selection → twin | Architecture review |
| **Mechanical Design** | Author + commit the load-bearing/motion-critical geometry, material + dimensions → twin | Mechanical design review |
| **Electronics Design** | Power budget, schematic topology, component selection, ERC → twin | Electronics review |
| **Firmware & Control** | Control loop, task/RTOS structure, pin map + drivers → twin | Firmware review |
| **Simulation & V&V** | FEA / kinematics / ERC-DRC / thermal, pass-fail verdicts vs requirements → twin | V&V sign-off |
| **Manufacturing Prep** | BOM + cost, fabrication outputs, assembly + bring-up plan → twin | Manufacturing readiness |

Select a flow with the `flow` id in the run request (`"flow": "hardware_v1"`).
A full `hardware_v1` run now commits **nine real, typed work products** —
`prd`, `documentation` (architecture budget), `cad_model`, `bom`, `pinmap`,
`firmware_source`, `test_plan`, `manufacturing_file`, and `design_decision`.
Adding or extending a phase is a data change in
`orchestrator/design_flow/spec.py`, not new control flow.

## Goal-driven deterministic handlers

The native ReAct brain reasons well but is unreliable at *reliably* producing a
specific typed artifact (it may author prose where a `cad_model` or `bom` is
required, or claim a result a tool never actually returned). So every
`hardware_v1` phase is routed to a **goal-driven handler** that follows a hybrid
pattern:

> the LLM extracts a small structured **spec** from the goal (its strength —
> reading intent), then a **deterministic** step authors the artifact and commits
> it through a recorder (the reliable path). The artifact is always goal-named,
> loadable, and consistent.

| Phase | Handler | Produces |
|-------|---------|----------|
| Requirements | `GoalDrivenRequirementsHandler` | `prd` + verifiable constraints (each with an acceptance method) |
| Architecture | `GoalDrivenArchitectureHandler` | `documentation` (per-subsystem numeric mass/power/cost budgets) |
| Mechanical Design | `GoalDrivenMechanicalHandler` | loadable `cad_model` (FreeCAD → STEP → MinIO) |
| Electronics | `GoalDrivenElectronicsHandler` | `bom` + closed numeric power budget |
| Firmware & Control | `GoalDrivenFirmwareHandler` | `pinmap` + `firmware_source` scaffold |
| Simulation & V&V | `GoalDrivenVVHandler` | `test_plan` + an honest verdict (deep analyses deferred, never falsely "compliant") |
| Manufacturing Prep | `GoalDrivenManufacturingHandler` | `manufacturing_file` + honest readiness (Gerbers deferred to Phase 2) |

Handlers share the pattern in `api_gateway/runs/*_handlers.py` and persist via
the recorders in `api_gateway/twin/` (`geometry_recorder`, `bom_recorder`,
`document_recorder`). A `HybridBrain` routes each phase to its handler and falls
back to the `ReActPhaseBrain` for any phase without one (`mech_v1` and the older
`design_v1` use different handler sets). Where a real analysis needs a
capability MetaForge doesn't have in Phase 1 (ERC/DRC on an authored schematic,
Gerber export), the handler records the result **honestly as deferred** rather
than asserting a compliance the tools never established.

## Eval flywheel and the honest scoreboard

The harness is matured by an **eval flywheel** (`evals/`): reference products are
driven through the *real* gated flow (`evals/run_scenarios.py`, auto-approving
each gate), and **correctness rubrics** score the engineering *outcome* — not
mere presence — of each phase against what a real deliverable must contain. The
rubrics deliberately score correctness, so a phase that records a `design_decision`
but no real content scores low.

Seven per-phase rubrics (requirements, architecture, mechanical, electronics,
firmware, V&V, manufacturing) plus one **cross-phase consistency** rubric
(digital-thread integrity — does the same device/interface/power thread through
every phase, and do the artifacts agree?) run over three reference products:
`l1_breakout` (IMU board), `l2_logger` (BME280 dual-bus logger), and
`l3_balancer` (a self-balancing robot controller). All three score identically:
**every phase at 1.0 except the electronics `erc_or_netlist` check** — which is a
genuine Phase-2 boundary (real ERC needs an authored schematic, a KiCad-write
capability), not a defect — with the digital thread intact end to end.

A second suite, `evals/run_chat_scenarios.py` (MET-570), applies the same
flywheel to the harness-backed **chat** surface: scripted multi-turn
conversations over `/v1/chat` scored for needle recall across turns,
project-brief adherence, context-window telemetry honesty, and tool-call
trajectory quality (duplicate/retry discipline, error rates, big-observation
survival). Scenarios declare `expected_today` for behavior known broken on the
current harness (e.g. facts beyond the 20-turn history slice, until MET-568
lands compaction), which reports as `xfail` and flips to `xpass` when the fix
ships — so re-running the identical baseline command measures each
context-engineering phase as it lands. See `evals/README.md` for the scenario
schema and rubric catalog.

Work-product **quality** (substance, not structure) is scored by an optional
LLM-as-judge pass, `evals/judge.py` (MET-571): it grades each run's twin work
products against the scenario's `definition_of_done` and attaches advisory
`judge` blocks to the report. Deterministic rubric checks remain authoritative
for pass/fail.

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
# The friendly way: start a gated flow for a goal and stream transitions
python -m cli.forge_cli design "quadruped robot leg able to carry 5 kg body mass" \
  --flow design_v1 --project-id <uuid>

# The explicit equivalent (what `design` wraps)
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

## Driving it from chat (MET-587)

The chat agent can start a flow itself via the `runs` MCP adapter:

- **`run.start_design_flow`** — goal + flow id (validated against the
  registry, default `hardware_v1`) + `project_id`; drives the same
  in-process path as `POST /v1/runs` and returns the run id + phase list.
- **`run.get_status`** — run id → lifecycle state, the gate reason it is
  paused on, error/result — so the agent can report progress in
  conversation.

Launching is deliberately **not** pre-gated: the flow pauses at every phase
boundary for human approval, so the gates themselves are the HITL
mechanism — the tool only queues work a human must repeatedly sign off.
Approvals stay where they always were (`POST /v1/runs/{id}/approval`, the
dashboard, or `forge runs approve`).

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

## Constraint-as-gate-criteria (MET-583)

Gate *criteria* were previously prose shown to the approver but never
evaluated. Now every gate also evaluates the project's recorded constraints
through the twin's constraint engine (`TwinConstraintChecker` in
`api_gateway/runs/gate_eval.py`):

- **Every gate** appends the real constraint state to its approval reason —
  `Constraints: OK (N evaluated)` or the violation/warning list — so the
  reviewer sees data, not just prose.
- **Gates with `enforce_constraints`** (the final gate of each built-in flow:
  V&V sign-off on `design_v1`/`mech_v1`, Manufacturing readiness on
  `hardware_v1`) fail-fast when any ERROR-severity violation applies to the
  run's project, with the same contract as a missing required deliverable.
- **Best-effort**: a broken or absent constraint engine reads as "unchecked"
  and never blocks or crashes a run.
- **Scoping**: the engine evaluates the branch, not the project — violations
  citing `work_product_ids` are filtered to the run's project; violations
  citing none are treated as global and always count.

The gate skeleton stays hardcoded (versioned code); the criteria come from
the project's own constraint data. The structured constraint-creation tool
(MET-582) is what fills that data from the Requirements phase; decision-derived
phase applicability (MET-585) is the planned complement.

## What's built vs. planned

**Built (Phase 1):** the `design_v1`, `mech_v1`, and full 7-phase `hardware_v1`
gated flows; the executor + gate coordinator wired into `/v1/runs`; goal-driven
deterministic handlers for every `hardware_v1` phase, committing nine typed work
products via the twin recorders; per-phase deliverable enforcement via
`GateEvaluator` (including a *loadable* `cad_model` gate); the ReAct phase brain
as fallback; SSE/CLI drive; and the `evals/` flywheel with eight correctness
rubrics over three reference products.

**Planned (Phase 2+):** real ERC/DRC and Gerber export on an authored schematic
(KiCad-write) to close the electronics `erc_or_netlist` gap; real CalculiX FEA on
a load-bearing part inside `hardware_v1` (today only `mech_v1` runs FEA;
`hardware_v1` V&V honestly defers it); weighted **gate-readiness** scoring via
`twin_core/gate_engine` (EVT/DVT/PVT); phase/gate linkage stored on twin nodes;
and a dedicated `forge design` CLI wrapper.

## Key modules

| Module | Role |
|--------|------|
| `orchestrator/design_flow/spec.py` | `Phase` / `Gate` / `FlowDefinition`, built-in flows |
| `orchestrator/design_flow/executor.py` | `DesignFlowExecutor`, `GateCoordinator`, `PhaseBrain` |
| `api_gateway/runs/flow_brain.py` | `ReActPhaseBrain` fallback + the decision backstop |
| `api_gateway/runs/{req,arch,mech,elec,fw,vv,mfg}_handlers.py` | Goal-driven deterministic phase handlers |
| `api_gateway/twin/{geometry,bom,document}_recorder.py` | Persist typed artifacts loadably (blob + `content_hash` + project link) |
| `api_gateway/runs/gate_eval.py` | `ProjectGateEvaluator` — deliverable enforcement (loadable `cad_model`) |
| `api_gateway/runs/routes.py` | Per-flow handler routing; launches the executor on a design-flow `POST /v1/runs` |
| `evals/run_scenarios.py`, `evals/*_rubric.py` | Eval flywheel: scenario runner + correctness rubrics |
| `evals/run_chat_scenarios.py`, `evals/chat_*_rubric.py` | Chat context-engineering evals: multi-turn scenarios + trajectory rubrics (MET-570) |
