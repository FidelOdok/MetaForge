---
description: Decide, before any CAD tool runs, whether a proportions-and-topology design sketch must be made and approved by a person first, using five fixed rules (user asked for review, revision of built geometry, multi-part with moving joints, novel topology, four or more parts). Use when you are about to model or change a part or assembly with freecad or cadquery tools, when the user asks for a mechanism, linkage, limb, arm or multi-part assembly, or when the request changes geometry that is already committed.
---

# decide_sketch_needed

A gate that runs before CAD. Some work is cheap to get wrong in a sketch and
expensive to get wrong in CAD: a mechanism whose proportions are off, a
revision to a part other things depend on, a layout of many parts. For those,
a person approves a simple scaled sketch first. This skill applies five fixed
rules and returns "sketch needed" or "go straight to CAD", with the reasons.
It reads nothing and writes nothing; you apply the rules yourself from facts
you have checked.

## When to use it

Run it at the start of any CAD request, before the first `freecad.*` or
`cadquery.*` call:

- "Design a two-link leg for the quadruped." (joints: sketch)
- "Make the bracket 10 mm longer." (revision of built geometry: sketch)
- "Lay out the six parts of the drone frame." (four or more parts: sketch)
- "A 50 x 30 x 3 mm plate." (simple, new: straight to CAD)

Not for: making the sketch (`author_design_sketch`), or choosing between
design concepts (`twin.record_decision` with the alternatives).

## Tools and profile

The rules need no tool. The tools establish the facts.

| Step | Tool | Served on |
|---|---|---|
| Find the project | `project.open` | every profile |
| Is this geometry already built? | `twin.find_by_property`, `twin.get_node`, `twin.thread_for` | every profile |
| Is there a prior reference for this shape? | the project brief and hierarchy resources (below) | every profile |
| Log the gate decision | `session.log_event` | every profile |

Resources: `metaforge://twin/brief/<project_id>` and
`metaforge://twin/hierarchy/<project_id>`.

## Inputs you need before you start

| Input | How to establish it | Ask the user when |
|---|---|---|
| Built geometry this work changes (node ids) | Search the brief, `twin.find_by_property` by name, `twin.get_node` | The request names a part you cannot find, or several match |
| Number of distinct parts, links or bodies | From the request | The request is vague ("a gripper") |
| Moving joints between parts? | From the request | Unclear whether parts move relative to each other |
| Novel topology? (nothing comparable built in this project) | Brief and hierarchy | You cannot tell whether a similar part exists |
| Did the user ask for a sketch or review? | From the conversation | Never assume yes |

Do not guess any of these to reach the answer you would prefer. An
under-counted part list or a missed revision skips the gate.

## Procedure

### 1. Establish the facts

1. `project.open` with the user's project. Read
   `metaforge://twin/brief/<project_id>`.
2. For each part the request touches, look for a committed node by name
   (`twin.find_by_property` with `node_type: "WorkProduct"`, `property:
   "name"` and the part's name as `value`, or by reading the brief). Each one found is a
   **source node**: the request revises it.
3. If a source node exists, `twin.thread_for` on it shows what depends on it
   (assemblies, simulation results). Mention that to the user; it is why
   revisions are gated.
4. Count parts and note whether any joint moves (revolute, prismatic,
   slider, ball). Fixed fasteners are not moving joints.
5. Read `metaforge://twin/hierarchy/<project_id>` to see whether a
   comparable shape or mechanism already exists. Nothing comparable means the
   topology is novel.

### 2. Apply the rules

A sketch is needed if **any** of these holds:

| # | Rule | Reason to state |
|---|---|---|
| 1 | The user asked for a sketch or review | a person explicitly requested a sketch before this work |
| 2 | At least one source node exists | this revises N already-built work products; changing committed geometry needs an approved reference first |
| 3 | More than one part **and** moving joints | multi-body assembly with moving joints; topology and proportions must be reviewed before CAD |
| 4 | Topology is novel | no prior built reference for this shape or mechanism in this project |
| 5 | Four or more parts | assembly complexity warrants a layout and proportions check |

If none holds: no sketch. The reason is "simple, brand-new, low part count,
no moving joints, no novel topology: proceed directly to CAD".

List every rule that fired, not just the first.

### 3. Act on the answer

- **Sketch needed**: tell the user which rules fired, then go to
  `author_design_sketch`, passing the source node ids as its source nodes.
  Do not start CAD until a person has approved the sketch. Approving it is
  not your decision, and nothing in your tools lets you do it.
- **No sketch**: proceed to `generate_cad`, `generate_cad_ir` or
  `create_assembly`.
- Log the outcome with `session.log_event` (`type: "decision"`, a one-line
  `message` with the verdict and the rules that fired) if you started a
  session.

### 4. When the user wants to skip a required sketch

Say which rule requires it and why. If the user still says skip it, that is
their call: record it with `twin.record_decision` (`title` such as
"Proceed without design sketch for leg revision", `rationale` in the user's
words, `alternatives` including "author and approve a sketch first"), then
proceed. Never skip the gate silently.

## Checks before you report

- [ ] Searched the twin for existing geometry before calling it "new"
- [ ] Part count and joints came from the request or the user, not a guess
- [ ] Every fired rule is listed with its reason
- [ ] A required sketch was not bypassed without the user's recorded decision

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| Cannot tell whether the part exists | Several near-matching names | Ask the user which node; do not pick. |
| Brief or hierarchy will not load | No project bound, or project id wrong | `project.open` again; check `health.check`. |
| User says "just build it" after a sketch-needed verdict | Their authority | Record the override with `twin.record_decision`, then proceed. |
| `author_design_sketch` cannot record the sketch | Sketch tool not served | Follow that skill's fallback; tell the user nothing gates the CAD on the server. |

## Limits

- Coarse by design: five yes/no rules, no geometry analysis. A two-part
  static assembly of a new kind still fires rule 4 only if you judged the
  topology novel.
- The server does not enforce this gate. CAD tools run whether or not a
  sketch exists or is approved; the discipline is yours.
- "Novel" is a judgement against the project's own history; say what you
  compared against.
