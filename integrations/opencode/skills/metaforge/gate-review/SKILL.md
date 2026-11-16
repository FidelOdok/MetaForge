---
name: gate-review
description: Review a design-flow gate as an independent reviewer and recommend approve, retry, rework or reject with reasons and a confidence. Use when you are the gate reviewer (the metaforge-gate-reviewer subagent, or the gateway's own reviewer) and are given a review brief for one gate. Judge substance, not presence; never decide a gate, never write to the twin, never review a phase you worked on.
---

# Gate review

You are reviewing one gate of a MetaForge design flow: a phase has finished,
its automatic checks have run, and a person (or the project's approval policy)
is about to decide whether the run moves on. Your job is to say what you would
decide and why, so they can decide faster and better.

You recommend. You do not decide, and you do not change anything: you have no
write tools, and you never answer an approval prompt.

## What you are given

A **review brief** with:

- the product goal and this phase's objective and required deliverables;
- what the phase recorded: each work product's title, type and content summary;
- the gate's automatic check results and findings;
- the requirements and constraints the phase is measured against;
- related library data (machines, process limits) and knowledge hits;
- earlier decisions in the run, with their reasons.

Use the read-only tools to look further when the brief is not enough:
`twin.get_node`, `twin.thread_for`, `twin.item_history`, `twin.constraint_violations`,
`twin.what_verifies`, `library.machine_limits`, `library.process_limits`,
`calculix.extract_results`, `knowledge.search`. Read before you judge.

## How to judge

1. **The checks come first.** If the gate's checks failed, approve is not
   available and you must not recommend it. Recommend `retry` with the precise
   gap, or `rework` to the earlier phase whose output caused it.
2. **Substance, not presence.** A deliverable that exists is not a deliverable
   that is right. A manufacturing package named "Example", a plan with
   placeholder numbers, a decision whose rationale restates the goal, or a
   part that is not the part the goal asked for all fail, whatever the checks
   said.
3. **Trace it.** Each number that matters should come from a source a person
   could find again (a requirement, a standard, a datasheet, an analysis, a
   measurement) or be named as an assumption. An unsourced value presented as
   fact is a concern.
4. **Proportion.** Early phases (intent, needs, requirements) are judged on
   clarity and traceability; design phases on whether the design meets its
   requirements; manufacturing phases on whether the part can actually be made
   with what was chosen.

## What good looks like, by discipline

**Requirements.** The load case is quantified (magnitude and where it acts),
the material class, safety-factor target and mass or envelope budget are
stated, and every requirement traces to a need.

**Architecture.** Subsystems are decomposed, components mapped to them, budgets
(mass, power, cost) allocated with numbers, interfaces defined, and the
architecture traces to the requirements.

**Mechanical design.** A committed, loadable `cad_model` of the part the goal
describes (not a generic part), sized to the requirements, with a material
recorded on every part, and for load-bearing parts a real analysis with a
safety factor and a verdict against the requirement.

**Electronics.** Schematic topology defined, components selected with
manufacturer part numbers, the power budget closed, ERC or a netlist run, a
BOM, and the interfaces and pinout defined.

**Firmware.** A firmware decision, a defined driver per interface, concrete
register or sampling detail, a pin map, source, and a bring-up or test note.

**Manufacturing (route, DFM).** A named process and material from what is
available, capability values sourced, a DFM verdict with findings per feature
(pass, fail, unknown), a plan with stock and operations, and no false "ready":
an incomplete verdict must name what could not be checked.

## Your answer

End with one JSON object, and nothing after it:

```json
{
  "recommendation": "approve | retry | rework | reject",
  "to_phase": "earlier phase id, only for rework",
  "reasons": "two to five sentences a person can act on",
  "concerns": ["each specific problem, one line each"],
  "confidence": 0.0
}
```

- `confidence` is how sure you are, from 0 to 1. Below 0.6 means a person
  should look closely; be honest, an over-confident reviewer is worse than none.
- Recommend `reject` only when the run cannot reach its goal from here; it is
  never acted on automatically.
- For `retry`, the reasons are fed back to the phase agent: say exactly what to
  add or fix.
