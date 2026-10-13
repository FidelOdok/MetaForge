---
name: rank_manufacturing_processes
description: Choose how a part will be made BEFORE designing it. Rank candidate manufacturing processes on fit (envelope, minimum feature, tolerance, material), availability on the route and cost at the stated quantity, using only sourced capability values, then record the ranking and the recommended route for the person to pick at the route gate. Use in a flow's Manufacturing Route & Process Selection phase, or when the user asks how a part should be made, which process suits a quantity, or in-house versus a vendor.
domain: manufacturing
---

# rank_manufacturing_processes

The process decides what the design is allowed to be: a laser-cut and welded
frame wants standard tube sections and weld access; a routed plywood top
wants sheet sizes and a cutter radius in every inside corner; a moulded
part wants draft and even walls. So the process is chosen first, from what
is actually available, and the design is then held to it. This skill does
the comparison in the open. The person makes the choice.

## When to use it

- The **Manufacturing Route & Process Selection** phase of a design flow (it
  runs before the design phase in `mech_v1`).
- "How should we make this, one-off or 500 a year?"
- "In-house on our router, or send it to a fabricator?"

Not for:

- Checking a finished design against its process: `check_manufacturability`.
- Whether tolerances on a drawing are holdable: `check_tolerance`.

## Inputs, and where they come from

| Input | Source |
|---|---|
| quantity, budget, route, machines, stock | the run's manufacturing context (in the phase brief); ask the person if missing |
| material class | requirements, or the person; never pick one silently |
| part envelope, minimum feature, tightest tolerance | requirements and the intent; a rough envelope is enough at this stage |
| each candidate's capabilities and costs | **sourced values**: a machine's spec sheet, a vendor's published limits, a standard. Record each as a `design_value` with its source (see the `source-engineering-values` skill) and pass the ids in `source_refs` |

Start from the machine library. The flow context lists the machines chosen
for the project (or, if none were chosen, the available ones); for each that
can run a process you are considering, `library.machine_limits` returns a
ready `candidate` (envelope, minimum feature, tolerance, materials,
availability, `source_refs`). Pass it on unchanged and add the costs you
sourced. A machine the person mentions that is not in the library is
recorded first with the `manage-machines` skill (held for their approval).

Pick 2 to 4 candidates that the route allows (the person's own machines for
`in_house`; supplier processes for `vendor`; both when undecided). A value
you could not source stays empty: the criterion it feeds reads `unknown`,
which is honest and visible at the gate. Never fill one in from memory.

## Tools

| Step | Tool |
|---|---|
| a library machine's capabilities, as a candidate | `library.list_machines`, `library.machine_limits` |
| a vendor's real price (connected vendors) | `vendor.list_connectors`, `vendor.quote` |
| where to send it when a vendor has no API | `vendor.handoffs` |
| look up capabilities, prices, lead times | `web.search`, `web.fetch`, `knowledge.search` |
| record each sourced value | `twin.record_engineering_entity` (entity_type `design_value`) |
| record the decision | `twin.record_decision` with `manufacturing_route` and `process_ranking` |
| hold the design to the chosen process | `twin.record_constraint_set` |

## Recording the decision

Call `twin.record_decision` with:

- `manufacturing_route`: `{route, process, material_class}` for the
  recommended candidate, or `{deferred: true, reason}` if the person cannot
  decide yet.
- `process_ranking`: the ranked list as returned (process, route, verdict,
  cost_at_quantity, reasons), and `recommended`.
- `alternatives`: the other candidates, each with why it ranked lower.

Then record the chosen process's limits (minimum wall or feature, maximum
envelope, tolerance class, stock sizes) with `twin.record_constraint_set`,
so the design phase works to them and its gate checks them.

The route gate shows the ranking and the recommendation. Approving it means
the person accepts the recommended process. To choose another, they retry the
phase with feedback naming it; record that one instead.

## Limits

- There is no built-in process capability data yet; every value is sourced.
- Cost is setup plus unit cost at the stated quantity. Finishing, logistics
  and scrap are not included; say so if they matter.

## Checklist

- [ ] 2 to 4 candidates the route allows
- [ ] every capability either sourced (with a `design_value`) or left empty
- [ ] ranking recorded with `process_ranking`, recommendation in `manufacturing_route`
- [ ] chosen process limits recorded as a constraint set
