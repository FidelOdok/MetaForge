# Workflow Lifecycle (FORGE-539)

A design flow used to be an ordered list of gated phases: a template tailored
by a closed set of operations, run phase after phase, with a rework that sent
the run back to an earlier phase and re-ran everything after it. This page
describes what was added so a flow is a living, verifiable execution model:
a structured intent, a dependency graph with parallel and conditional
phases, capability coverage, a failure taxonomy, selective repair, workflow
validity tied to the digital twin, and a completion verdict.

The rule that shapes all of it is unchanged: **a model may suggest, code
decides and records, and a person approves.** Nothing here lets a tool
approve its own call.

## Ownership

| Role | Owner |
|---|---|
| Intent compilation, outcome decomposition, tailoring, failure diagnosis, patch proposals | A model (the gateway's generator, or the client's own model in caller mode). It only *proposes*, from a closed set of operations. |
| Applying and validating operations, the graph, readiness | Deterministic code in `orchestrator/design_flow/` |
| Workflow state: versions, phase states, events, the re-run set | The design-flow engine (Temporal) and the flow version store |
| Objective success and completion | Deterministic code reading the twin's requirement matrix |
| Approval of proposals, patches, gates | A person |
| Design and evidence truth | The digital twin |

## Structured intent (`intent.py`)

`compile_intent()` turns the request and its stated context into an
`IntentModel`: the goal type and object, the immediate request versus the
underlying objective ("calculate what motor I need" is an analysis serving a
selection), constraints sorted by category (each quantity categorised by its
own unit), directed quantities as measurable success criteria, preferences
kept apart from constraints, assumptions, and unknowns marked blocking or
not. It is deterministic: no model call, and no value appears that the
person did not state.

- `POST /v1/design-flows/intent`, MCP `flow.compile_intent`
- Every proposal carries it as `intentModel`.

## Flows as graphs (`graph.py`)

A phase may declare:

| Field | Meaning |
|---|---|
| `depends_on` | The phases it needs. Unset means "the phase before me", so every existing template and approved flow is the straight line it was. An empty list makes it a root. |
| `condition` | Run only when a fact holds: `fact == value`, `!=`, `in [a, b]`, `not in [a, b]`, joined by `and`. Facts come from the proposal's context (`route`, `target_maturity`, `loads_known`, `budget_stated`, ...) and are frozen with the version. A false condition **skips** the phase; skipped is never "done". |
| `outcome` | The intermediate outcome the phase establishes. |

New frozen fields are omitted from the content hash when unset, so earlier
approvals still verify. Tailoring gains `set_dependencies`, `set_condition`
and `set_outcome`. The invariants gain `graph-is-valid` (unknown, self or
cyclic dependencies, unreadable conditions); `deliverable-is-producible`
follows real dependencies and ignores conditional producers; a conditional
release gate does not count as a release gate.

**Execution.** On Temporal, a flow with any dependency or condition runs
behind the `forge-539-graph` patch: independent phases run as parallel
activities, gates are held one at a time in flow order, and the state query
gains `mode`, `running` and `skipped`. A straight-line flow records no marker
and replays exactly as before. The in-process engine (the test double) makes
the same decisions one phase at a time.

## Capability coverage (`capabilities.py`)

For every deliverable a phase requires or expects, coverage is `FULL`,
`PARTIAL`, `UNAVAILABLE` or `UNKNOWN`, from the tools that produce it
(`mcp_core.profiles.DELIVERABLE_TOOLS`), the live registry and adapter
health, and optionally an MCP profile. Each shortfall is a gap with a
severity (`BLOCKS_STEP`, `DEGRADES_CONFIDENCE`, `REQUIRES_USER_ACTION`,
`REQUIRES_NEW_CAPABILITY`) and workarounds that keep the requirement's
verification method intact. The report's status (`READY`,
`READY_WITH_WARNINGS`, `BLOCKED`) is the readiness gate; anything not
checked is listed in `limits`.

- `GET /v1/design-flows/{flow}/capabilities`, `GET /v1/design-flows/versions/{id}/capabilities` (`?profile=`), MCP `flow.capabilities`
- Every proposal, and the approval a person answers, carries it.

## Failure taxonomy (`failures.py`)

A phase that cannot run is classified (`transient`, `tool`, `data`,
`capability`, `design`, `authorization`, `environment`, `reasoning`,
`unknown`) and the class decides the response. Only a transient failure is
retried as-is; a design failure calls for replanning, a missing key for
fixing configuration. Temporal records the class on the failed result
(`forge-539-failure-class` patch); the in-process engine adds it to the error.

## Selective repair and patches (`patch.py`)

A gate's **rework** now re-runs the target and only what depends on it; for
a straight line that is every later phase, as before. A rework target must
be a phase the current one depends on.

A **patch** changes a running flow without starting it again: tailoring
operations plus phases the new information invalidates (a heavier payload
invalidates the mechanical design, not the electronics). The plan re-runs
every changed or invalidated phase and its downstream; everything else keeps
its result and approval. A patch carries the content hash of the flow it was
written against and is refused as stale if the run has moved on.

1. `POST /v1/runs/{id}/patches` (MCP `flow.patch`, `action: propose`) saves a
   new version and holds a `design_flow_patch` approval for a person.
2. `POST /v1/runs/{id}/patches/{version}/apply` (`action: apply`) is refused
   until that approval is given, then signals the change with its re-run set.
   The workflow applies it at its next gate: a graph keeps every untouched
   completed phase, a straight line keeps the prefix before the first touched
   phase.

## Lifecycle view and completion verdict (`lifecycle.py`)

Each phase gets four separate answers: `execution_status` (did it run),
`eligibility` (can it run now), `validity` (is its result current: `STALE`
when a twin item it approved has since been superseded, `POTENTIALLY_INVALID`
downstream of that) and `objective_status` (did its gate find the objective
met). The run gets a completion verdict against the requirement matrix:

| Classification | Meaning |
|---|---|
| `COMPLETED_VERIFIED` | Every mandatory requirement passes with current evidence, nothing is stale, every objective was met, no blocking gap. |
| `COMPLETED_WITH_WARNINGS` | Passing, with non-blocking limits (a waiver, a skipped phase, no requirements recorded at all). |
| `PARTIALLY_COMPLETED` | The phases finished but the intent is **not** satisfied: a requirement fails or has no current evidence, a result is stale, or a gap blocks. |
| `BLOCKED`, `IN_PROGRESS`, `FAILED`, `CANCELLED` | As named. |

`no_data` is never a pass. `GET /v1/runs/{id}/lifecycle` (MCP `flow.lifecycle`,
and `flow.verify_completion` for the verdict alone) returns the view, any
`limits` on what could be read, and a `nextStep` sentence every client
repeats verbatim.

## Plugin surface

| Piece | What it does |
|---|---|
| `flow.compile_intent`, `flow.capabilities`, `flow.lifecycle`, `flow.verify_completion` | Read-only lifecycle tools, identical over the in-process and remote (sidecar) bindings. The first three are on the `core` profile; the verdict is part of `flow.lifecycle`. |
| `flow.patch` | Propose (held) or apply (approved) a patch. Downstream-approved, like `flow.propose`. On `core`. |
| `workflow-lifecycle` skill | The reasoning contract, with a MetaForge binding table and the runtime gaps to report. |
| `intent-to-verified-design` skill | The procedure, using the tools above. |
| Hooks (Claude Code) | SessionStart gives the working rules; PostToolUse on `flow.propose`/`flow.patch` restates that the result is held for a person. Stdlib only; `METAFORGE_PLUGIN_HOOKS=off` disables. |
| Agents (Claude Code) | `metaforge-flow-planner` (plans and stops at the approval), `metaforge-run-verifier` (reports the verdict honestly). |
| `/metaforge:verify`, `/metaforge:replan` | New workflows; on Codex, every workflow ships as a `<name>-workflow` skill. |

## Runtime limits

Only the `ALL` join is supported; `ANY`, `QUORUM` and first-class loops are
not (iteration is a bounded rework). One attempt per phase per wave. A run
waits only at its gates (no pause), and there is no cancellation over MCP.
Tool side effects carry no idempotency keys, so an uncertain external effect
is not reconciled automatically.
