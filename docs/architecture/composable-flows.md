# Composable Design Flows

A design flow is assembled for each project from three layers. The forward
design lives in the Planner repo
(`docs/architecture/composable-design-flows.md`); this page documents what
is built.

| Layer | What it is | Where it lives |
|---|---|---|
| **Spine** | The gated stages every product goes through, defined once | `orchestrator/design_flow/templates/spine/spine_v1.yaml` |
| **Domain fragment** | One domain's tasks, with their data dependencies and acceptance checks | `orchestrator/design_flow/templates/fragments/<id>.yaml` |
| **Capability** | The tools and skills that do the work | the tool registry and skills |

The spine is the same for every product. The fragments change: a powered
board is the spine plus `electronics_v1` plus `thermal_v1`.

## The spine

`spine_v1` holds the stages intent, needs, requirements, feasibility,
architecture, concept selection, design, simulation (V&V) and manufacturing.
Each stage has a gate. The templates `design_v1`, `mech_v1` and `hardware_v1`
declare `extends: spine_v1` and pull stages with `- use: <id>`. A key next to
`use` overrides that key of the spine phase, and a key set to `null` removes
it. `tests/unit/test_task_contract.py` pins all three templates against a
golden snapshot taken before the spine existed. Their definitions and content
hashes are unchanged, so every approved flow version still verifies.

## The task contract

A fragment task becomes a phase with no gate, carrying:

| Field | Meaning |
|---|---|
| `fragment`, `stage` | Which fragment contributed it, and the spine stage whose gate reviews it |
| `inputs` | Twin items it reads, as `type:name` (`design_value:heat_load_w`). The type must be a revisioned definition type |
| outputs | Twin items it writes, declared as `outputs:` in the fragment. Each becomes a deliverable slot with a fixed item key (`VAL-HEAT-LOAD-W`) |
| `records` | Record types it must leave (`simulation_result`, `design_decision`), which are required at the stage gate |
| `checks` | Acceptance criteria: `value <op> number [unit]`, or `value <op> other_value`, so a limit can come from a sourced record and is never written into a fragment |
| `capabilities` | Tool ids it needs. A missing, unreachable or unserved tool is a **blocking** capability gap on that task. The task's phase always carries these tools in its allowlist, kept like deliverable tools whatever its discipline profiles hold, on both engines (FORGE-672) |

All of these fields are left out of the frozen content hash when empty
(`orchestrator/design_flow/frozen.py`). `orchestrator/design_flow/contract.py`
parses and validates them for the template loader, the version store and the
fragment registry alike.

## Fragments

`orchestrator/design_flow/fragments.py` loads and validates each fragment
when it is read. A fragment is refused, with the rule named, when any of
these holds:

- a task has no output or no objective;
- a task names a stage the spine lacks;
- two tasks write the same item;
- a check reads a value its own task does not write;
- a reference is malformed;
- a dependency cycle exists outside a block.

The fragments shipped today:

| Fragment | Tasks |
|---|---|
| `thermal_v1` | heat-load estimation → cooling architecture → heat-transfer analysis (`calculix.run_thermal`) → temperature verification |
| `electronics_v1` | component selection → power budget (`power.check_budget`) → schematic ERC on the person's own KiCad schematic (KiCad is read-only in Phase 1) → circuit simulation (`spice.run_simulation`) |
| `packaging_v1` | board material selection → RSC dieline (`packaging.rsc_dieline`) → manufacturing check against the converter's rules → compression strength (`packaging.box_compression`, a McKee estimate) → drop testing, recorded as evidence from a physical test (FORGE-672) |
| `mobile_robot_v1` | locomotion selection → energy closure (a coupled block: mass roll-up, drive sizing with `robot.size_drive`, battery sizing) → kinematics (`robot.diff_drive_kinematics`) → dynamics in simulation (`gazebo.run_simulation`) → navigation testing (FORGE-671) |

`electronics_v1` writes `power_dissipation_w`, which `thermal_v1` reads, so
composing the two feeds the heat load from the power budget.

`mobile_robot_v1` takes everything physical from the requirements as sourced
values: payload, slope, rolling resistance, speed, acceleration, runtime and
the required torque margin. Its navigation task names
`gazebo.run_navigation_scenario`, which no adapter provides yet, so any
proposal that composes the fragment carries that task as a blocking
capability gap until one does.

## Composition

`orchestrator/design_flow/compose.py` wires fragments into a flow:

1. **Task dependencies are derived:** the tasks that write a task's inputs
   (and its check limits), plus whatever the task's stage itself waits for.
2. **Each stage waits for its tasks.** A stage phase depends on the tasks
   attached to it, so its gate reviews them.
3. **External inputs are reported.** An input that no task writes, typically
   a limit such as the allowed junction temperature, comes back in
   `composition.external_inputs`. It must be recorded with its source before
   the task that reads it runs.
4. **Bad compositions are refused.** Composition fails, naming the reason,
   for:
   - a missing stage;
   - a missing required fragment;
   - a duplicate writer;
   - a fragment composed twice;
   - data that flows backwards across stages, which would be a cycle through
     the gates.

A model or caller asks for a fragment with the tailoring operation
`add_fragment` (value: fragment id, no phase, with a rationale). The model
picks from the registry; it cannot write tasks. A caller's request is checked
strictly, while a server-side model's unknown fragment is dropped.
`flow.compile_intent` returns `suggested_fragments`, which are whole-word
keyword matches plus the fragments those require.

## Impact follows data, not ordering

`FlowGraph.impact` (`graph.py`) holds the edges a change invalidates:

- a task reaches the tasks that read its outputs, and the stage gate that
  reviewed it;
- a stage reaches the next stage.

The ordering edge that makes a task wait for the previous stage carries no
data, so impact does not follow it.

- `lifecycle.py` uses `impacted_by` for `POTENTIALLY_INVALID`.
- Rework (both engines) and patches use `affected`.

A flow without tasks has `impact = None`, which behaves exactly like
`children`, so every existing flow keeps its behaviour. In the
electronics + thermal flow, a new revision of `power_dissipation_w` makes the
power budget `STALE` and the thermal chain `POTENTIALLY_INVALID`. The ERC and
the ripple simulation stay `VALID`, because they never read that value. This
is Planner §21: invalidate only downstream dependencies.

## Checks at the stage gate

`orchestrator/design_flow/checks.py` evaluates every check of the tasks a
stage reviews. Each result is `PASS`, `FAIL` or `NO_DATA`:

- `NO_DATA` covers a value or limit that was never recorded, or is not a
  number.
- Both `FAIL` and `NO_DATA` block the gate. No data is not a pass.
- The stage gate also requires the tasks' declared deliverables.

Both engines evaluate the checks:

- **In-process:** `DesignFlowExecutor(value_reader=...)`.
- **Temporal:** the gate activity payload carries the attached tasks (only
  when there are any), and `GateCheck.check_findings` blocks.

The gateway reads design values through `TwinDesignValueReader`, which returns
the latest `design_value` record per name. The twin refuses to record a value
without a source (FORGE-586). Revisions a gate rejected, or that a run
abandoned (FORGE-525), are skipped. A run's own open drafts are kept, because
they are what its gate reviews.

## Optimisation

`optimize.suggest` (the `optimize` adapter, in-process, on the `simulation`
profile) searches one bounded design variable, ask and tell:

- **golden_section:** for a unimodal objective, to within a tolerance of the
  variable.
- **grid:** evenly spaced points.

It never evaluates an objective. The agent evaluates each suggested point with
the real analysis tool and passes back every evaluation (`x`, `objective`,
`feasible`, `source`). The tool returns the next point, or `converged` with
the best one. The evaluations are the state, so the same evaluations always
give the same answer. A point whose checks failed is `feasible: false` and is
never the best. If every point is infeasible, the status is `infeasible`
rather than naming one. The variable's bounds must name their source.

## Dashboard

`/flows/fragments` lists the fragments with each task grouped by the gate
that reviews it: what it reads and writes, its checks, its tools, and any
coupled loop. The page is read-only. A fragment joins a flow through a
proposal's `add_fragment`, which a person approves. The new-run page links
to it.

## Coupled blocks

A feedback loop, such as battery capacity → mass → hover power → battery
capacity, cannot be a graph edge. A fragment declares it as a `block`:

```yaml
blocks:
  - id: energy_closure
    stage: design
    members: [battery, mass, power]
    solves_for: [battery_capacity_wh, total_mass_kg, hover_power_w]
    tolerance: 0.01
    max_iterations: 20
```

The block compiles into **one** phase carrying a `coupling` contract, so the
outer graph stays acyclic. Its inputs are the members' inputs minus those the
members write themselves.

**The engine drives the passes.** Each attempt of a block phase is one pass:
the agent runs the member steps once and records the solved values as new
revisions. After each pass the engine reads the value history and judges it
(`coupling.judge_history`), then runs another pass, telling the agent which
pass it is and how far each value moved, until:

- the values converge: every one changes by less than the tolerance between
  the last two passes;
- they diverge: a value stops being finite, or the iteration overflows;
- `max_iterations` passes have been recorded;
- a pass records nothing new, which ends the loop because repeating it would
  not change the answer.

Both engines do this:

- **In-process:** `DesignFlowExecutor._iterate_block`.
- **Temporal:** the `judge_block` activity, behind the
  `composable-block-iteration` patch, so runs started earlier replay their
  single pass.

The stage gate then applies the same rule. Anything but convergence blocks,
with `NO_DATA` for fewer than two passes and `NOT_CONVERGED` or `DIVERGED`
otherwise.

The block's objective and the verdict say that a loop which does not settle
needs an engineering decision. Nothing relaxes a requirement to make it close.
`coupling.solve` runs the same fixed-point iteration deterministically over
plain functions. The tests use it on a closed-form energy loop.

## API and tools

| Surface | What changed |
|---|---|
| `GET /v1/design-flows/fragments` | The fragment registry (tasks, contracts, blocks) |
| `PhaseView` | Adds `fragment`, `stage`, `inputs`, `checks`, `capabilities` and `coupling` |
| Proposal response | `composition`: fragments, external inputs and derived task dependencies |
| `flow.list` | Adds `fragments`, and the task fields on task phases, identically over the in-process and sidecar bindings |
| `flow.propose` | Accepts `add_fragment` |
| `flow.compile_intent` | Adds `suggested_fragments` |

## Limits

- `thermal_v1`, `electronics_v1`, `mobile_robot_v1` and `packaging_v1` ship
  today. An aircraft fragment needs capabilities that do not exist yet
  (aerodynamics, trim, flight simulation), and the mobile robot's navigation
  testing needs a scenario-runner adapter.
- `packaging_v1` covers corrugated regular slotted containers. Its
  compression figure is a McKee estimate, which a compression test outranks.
  Its drop testing records a physical test; no drop simulation is built.
- `optimize.suggest` searches one variable at a time. Multi-variable and
  constrained optimisation are not built.
- Fragment tasks attach to the stages a template has. A template without a
  stage cannot take a fragment that needs it.
- `TwinDesignValueReader` cannot tell one run's open drafts from another's, so
  two runs iterating the same block on one project at the same time would
  read each other's passes.
