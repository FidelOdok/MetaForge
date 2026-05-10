# Project Structure

> **Status:** Phase 1 (v0.1). The on-disk layout MetaForge expects
> when it works on your hardware design. Each file has one source of
> truth and one set of consumers — keep them in sync and the agents
> stay coherent.

When you run `python -m cli.forge_cli run … --work_product <wp>` or
drive MetaForge from Claude Code, the gateway and agents read from
and write to the structure below. Initialise it manually for now;
a `forge init` command is planned but not yet shipped.

## Layout

```
my-project/
├── PRD.md                      # Human intent (what you're building)
├── constraints.json            # Design rules and constraints
├── decisions.md                # Design decisions log (ADR-lite)
├── eda/
│   └── kicad/                  # Schematic + PCB files
├── bom/                        # BOM, alternates, costing
├── firmware/
│   └── src/                    # Firmware source + pinmap.json
├── manufacturing/              # Gerbers, pick & place, fab notes
├── tests/
│   └── bringup.md              # Bring-up + EVT/DVT checklists
└── .forge/
    ├── sessions/               # Agent session records (one per run)
    └── traces/                 # Execution traces (OTel exports)
```

## Per-file contracts

### `PRD.md`

The single human-authored statement of intent.

- **You write it.** Agents only read.
- Free-form Markdown, but the front matter is consumed by the
  Product Definition agent — keep `# Product`, `## Goals`,
  `## Non-Goals`, `## Constraints` as top-level sections.
- Reference IDs from `constraints.json` and `bom/` so the digital
  thread can resolve back to PRD lines.

### `constraints.json`

Machine-readable design rules. Schema is a JSON object keyed by
constraint id; each entry has `severity`, `domain`, and a
domain-specific predicate.

- **You write it.** Agents read and propose edits via the
  approvals workflow.
- Validated by the `constraint.validate` MCP tool (see
  [`capability-matrix.md`](capability-matrix.md)) before any
  agent commits a graph change.
- Example entry:

  ```json
  {
    "PWR-001": {
      "severity": "error",
      "domain": "electronics",
      "predicate": "vbus_current_ma <= 500",
      "message": "USB 2.0 host port limit"
    }
  }
  ```

### `decisions.md`

Append-only design-decision log. ADR-style entries (date, context,
decision, consequences) but lighter — one heading per decision.

- **You and agents both write here**, but agents always go through
  the approvals workflow first.

### `eda/kicad/`

KiCad project files (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`,
libraries). Phase 1 is **read-only** — the `kicad` MCP adapter
runs ERC/DRC and exports BOM/Gerber but does not edit the
schematic. Schematic write-back lands in Phase 2. See
[`capability-matrix.md`](capability-matrix.md) for the current
KiCad tool list.

### `bom/`

Bill of materials. The Supply-Chain agent consumes this and the
[Component-Engineering agent](https://github.com/MetaForge-HA/MetaForge/blob/main/docs/agents/electronics-context-spec.md)
proposes alternates. Format: `bom.csv` with columns
`{ref, mpn, manufacturer, qty, alt_mpns}` plus per-part
sourcing JSON in `bom/sourcing/<mpn>.json`.

### `firmware/src/`

Firmware source. The Firmware agent reads `pinmap.json`
(GPIO ↔ peripheral mapping) and proposes diffs to source files via
the approvals workflow.

### `manufacturing/`

Outputs ready for the fab. Mostly produced by agents (Gerber export
from KiCad, pick-and-place from layout). You only edit the
`manufacturing/notes.md` for vendor-specific instructions.

### `tests/bringup.md`

EVT / DVT / PVT checklists. The Testing & Reliability agent reads
this to plan validation runs and writes results back.

### `.forge/`

MetaForge's own bookkeeping. **Don't hand-edit** these files.

- `sessions/` — one folder per agent invocation. Holds the input
  manifest, the agent's plan, the tool calls it made, and the
  proposal it produced.
- `traces/` — OpenTelemetry exports for debugging. Cleared with
  `python -m cli.forge_cli traces clear` (when implemented).

You can `.gitignore` `.forge/traces/` to keep the working tree
small; keep `.forge/sessions/` in git so reviewers can replay an
agent's reasoning during PR review.

## Working example

The `examples/drone_flight_controller/` project in this repo is the
canonical reference. Read its `README.md` and inspect the layout —
it tracks the same conventions described here.

## Where this lives in the digital thread

Every file above shows up as one or more nodes in the Digital Twin:

- `PRD.md` → `Requirement` nodes (one per `## Goal`).
- `constraints.json` → `Constraint` nodes.
- `bom/bom.csv` → `BOMItem` nodes, linked to `Requirement` via
  `satisfies` edges.
- `eda/kicad/*.kicad_pcb` → `PcbLayout` work product.
- `firmware/src/` → `FirmwareModule` nodes.

Use `twin.find_by_property` or `twin.thread_for` (see
[`cli-reference.md`](cli-reference.md)) to walk the thread from any
of these.

## What's not in the project tree (yet)

- **`forge init` command** — not shipped; create the structure by
  hand.
- **`.forge/lock`** — multi-user serialization; Phase 3 only.
- **Schema validation on commit** — git-hook validation is planned
  but not wired. For now run `constraint.validate` manually.
