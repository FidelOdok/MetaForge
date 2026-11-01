---
description: Capture a product's system architecture (the named components, the discipline that owns each, and the interfaces between them) as a reviewable block diagram in the twin before detailed design starts. Use when the user asks for a block diagram, a system breakdown, "what talks to what", an interface list or interface control table, or when a design flow phase requires a system_architecture deliverable.
---

# define_system_architecture

Turn the user's description of a product into a component and interface map
a reviewer can check: every component named and owned by a discipline, every
interface between two declared components, and any measurable interface
property (a deflection, a current, a data rate) stated with its unit. The map
is recorded in the twin so later work (CAD, schematics, firmware) can be
traced back to it.

## When to use it

- "Draw the block diagram for the drone flight controller."
- "What are the subsystems of the arm and how do they connect?"
- "List the interfaces between the battery pack, the BMS and the motor driver."
- A flow phase lists `system_architecture` in its `required_deliverables`.

Not for:

- Recording one design choice (use `twin.record_decision`).
- The parts tree with mass and cost roll-ups (that is the product hierarchy;
  read `metaforge://twin/hierarchy/<project_id>`).
- Quantified product requirements such as total mass or battery life (use
  `twin.record_constraint_set`; an interface quantity is not a requirement).
- Geometry or wiring (use the CAD and electronics skills).

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Open the project | `project.open` | every profile |
| Record the architecture (preferred) | `twin.commit_system_architecture` | see below |
| Record the architecture (fallback) | `twin.record_document` | `core` |
| Record why it is shaped this way | `twin.record_decision` | every profile |
| Import a robot's structure from a URDF | `twin.import_urdf` | see below |

`twin.commit_system_architecture` and `twin.import_urdf` exist in MetaForge
but are registered only when the server is started with its system
architecture recorder wired in. The gateway's own chat harness has them; the
standard MCP sidecar currently does not, and no profile lists them. Check
your tool list. If they are missing, use the `core` profile fallback below
and tell the user what that fallback does not give them.

## Inputs you need before you start

Ask the user for anything missing. Do not invent components or interfaces to
make the diagram look complete; a gap the user has not decided is a gap.

| Input | Why it matters | Example |
|---|---|---|
| Project | The record is linked to it | `drone-fc` (id from `project.open`) |
| System name | Titles the record; the preferred tool names it "<system> Architecture" | `Quadcopter flight controller` |
| Components | Each needs a unique `name`; `discipline` and `description` are optional but a component with no owner is a review finding | `IMU`, electronics, "6-axis inertial sensor" |
| Interfaces | Each needs `from` and `to` naming declared components | `MCU` to `IMU`, `SPI` |
| Interface type | What crosses the boundary | `SPI`, `I2C`, `CAN`, `power`, `mechanical` |
| Interface quantities (optional) | A measurable property of the interface, with a unit the server recognises | `tip_deflection`, `mm`, `<=`, `0.5`, owner `mechanical` |

If the user gives a quantity without a unit, or a limit without saying
whether it is a maximum or a minimum, ask. Do not assume `<=`.

## Procedure

### 1. Orient

1. Call `health.check`. Note the active profile (`active` under `profile`) and whether
   `twin.commit_system_architecture` is in your tool list. Decide now which
   path you will take and tell the user if it is the fallback.
2. Call `project.open` with the user's project name as `query`. Several
   matches come back as an error listing them: ask which one.
3. Read `metaforge://twin/brief/<project_id>`. If an architecture has already
   been recorded, start from it and treat this as a revision rather than a
   fresh map. Read `metaforge://twin/requirements/<project_id>` so interface
   quantities line up with requirements that already exist.

### 2. Build the map with the user

1. List the components you understood from the user, each with a discipline
   (`mechanical`, `electronics`, `firmware`, `software`, `systems`, ...).
   Show the list and ask the user to confirm or correct it before recording.
2. List every interface as `from` -> `to` with an `interface_type`. Use the
   exact component names from step 1; a typo becomes a dangling interface.
3. For each interface the user cares about quantitatively, add a
   `quantities` entry: `metric`, `unit`, optional `limit` and `op` (`<=`,
   `>=` or `==`), `owner`, `discipline`. Only add `predicted` (with `value`
   and an `evidence` reference) or `measured` (with `value` and `source`)
   when the user or a recorded result actually supplied the number.
4. Check the map yourself before writing: every interface endpoint is a
   declared component, no component is isolated without a reason, and no
   two components share a name.

### 3a. Record it with `twin.commit_system_architecture` (if listed)

Call it with:

- `name`: `"<system_name> Architecture"`
- `system_name`: the system name
- `components`: `[{"name": ..., "discipline": ..., "description": ...}]`
- `interfaces`: `[{"from": ..., "to": ..., "interface_type": ...,
  "description": ..., "quantities": [...]}]`
- `project_id`: from step 1

Read the result: `node_id`, `component_count`, `interface_count` and
`dangling_interfaces`. A non-empty `dangling_interfaces` is a real gap, not a
formatting issue: the architecture was still saved, but the named endpoint
does not exist. Show the user each one and ask whether a component is missing
or a name is wrong. A rejected unit in `quantities` fails the whole call; fix
the unit and call again.

This tool has no `item_key` or `supersedes` argument, so every call creates a
new architecture node. Do not call it repeatedly to "fix" a typo without
telling the user that the earlier node remains.

If the user has a URDF for a robot and `twin.import_urdf` is listed, it
records links as components and joints as interfaces on the same path.
Pass the XML as `urdf`. Read `not_imported`: meshes are never imported, and
joints it could not map are listed there.

### 3b. Fallback with `twin.record_document` (when 3a is not available)

1. Write the architecture as markdown in `content`, in the same shape the
   preferred tool renders: a heading `# System Architecture: <system_name>`,
   a mermaid `graph LR` block with one node per component and one labelled
   edge per interface, a Components table (Name, Discipline, Description)
   and an Interfaces table (From, To, Type, Description, Quantities).
2. Call `twin.record_document` with `name` = `"<system_name> Architecture"`,
   `document_type: "documentation"`, `content`, `project_id`, and
   `metadata` carrying `system_name`, `components`, `interfaces`,
   `component_count`, `interface_count` and `dangling_interfaces` (the list
   you computed in step 2.4).
3. Tell the user plainly what the fallback does not do: it is stored as a
   documentation work product, not a `SYSTEM_ARCHITECTURE` one, so the
   product hierarchy view will not show these interfaces against its nodes,
   no server-side dangling-interface check ran, and interface quantity units
   were not validated by the server.

### 4. Record the decisions behind it

For each structural choice a reviewer might question (one MCU or two, CAN or
RS-485, a separate power board), call `twin.record_decision` with `title`,
`rationale`, `alternatives` as `[{"option": ..., "reason_rejected": ...}]`
using only alternatives the user actually considered, and `project_id`.

### 5. Report

- Which path recorded it, the `node_id`, and the component and interface
  counts.
- Every dangling interface and every component with no discipline.
- Interface quantities that have a limit but no predicted or measured value
  yet (these are open verification work).
- Anything not recorded on this connection and why.

## Checks before you report

- [ ] The user confirmed the component list and the interface list
- [ ] Every interface endpoint matches a declared component name exactly
- [ ] Every quantity has a unit, and a limit has an explicit `op`
- [ ] No predicted or measured value was supplied by you rather than the user
      or a recorded result
- [ ] `dangling_interfaces` read and reported, whichever path was used
- [ ] The user knows whether the record is a `SYSTEM_ARCHITECTURE` work
      product or the documentation fallback

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| `twin.commit_system_architecture` not in the tool list | The sidecar is not wired with the architecture recorder | Use the 3b fallback; tell the user what it lacks. |
| `twin.record_document` not in the tool list | Connected on a non-`core` profile | Ask the user to connect with `?profile=core`, or report the map without recording it. |
| "is not a recognized unit" | An interface quantity unit the server does not know | Ask the user for a standard unit (`mm`, `N`, `A`, `V`, ...) and call again. |
| "'components' must be a non-empty array" | Called before the component list was settled | Confirm components with the user first. |
| Non-empty `dangling_interfaces` | A typo or a missing component | Ask the user which; record a corrected version and say the old node remains. |
| Write held for approval | Writes on this connection need a person | Tell the user where it is waiting. Do not retry or reword the call. |
| Several projects match | Ambiguous project name | Ask the user which one; never pick. |

## Limits

- The map records structure and declared quantities. It does not check that
  an interface is electrically or mechanically feasible.
- Dangling interfaces are flagged, not blocked: an incomplete architecture is
  still saved for review.
- There is no revise-in-place: a corrected architecture is a new node.
- Interface quantities are not requirements. A requirement a gate must
  evaluate still belongs in `twin.record_constraint_set`.
