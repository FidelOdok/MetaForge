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
| `capabilities` | Tool ids it needs. A missing, unreachable or unserved tool is a **blocking** capability gap on that task |

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

`electronics_v1` writes `power_dissipation_w`, which `thermal_v1` reads, so
composing the two feeds the heat load from the power budget.

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
without a source (FORGE-586).

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
members write themselves. The phase brain iterates the loop and records each
pass as a new revision of the solved values. The stage gate reads that history
and judges convergence (`coupling.judge`): every value must change by less
than the tolerance between the last two passes. Fewer than two passes is
`NO_DATA`, and too many is `NOT_CONVERGED`; both block.

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

- The engine does not run a block's iterations itself. One block phase is one
  agent attempt, and convergence is judged from what that attempt recorded.
- Only `thermal_v1` and `electronics_v1` ship today. Aircraft, mobile-robot
  and packaging fragments need capabilities that do not exist yet
  (aerodynamics, trim, flight simulation, compression and drop testing).
- There is no optimisation capability.
- Fragment tasks attach to the stages a template has. A template without a
  stage cannot take a fragment that needs it.
- `TwinDesignValueReader` orders records by creation time, so a closed draft
  of a value still counts in a block's history.
