# Electronics Agent — Context Specification

> **Status:** P1.13 / P1.14. Companion to
> [`docs/architecture/context-engineering.md`](../architecture/context-engineering.md).
> The role's allow-list lives in
> `digital_twin/context/role_scope.py::_ROLE_KNOWLEDGE_TYPES` keyed by
> `electronics_agent`.

## Role allow-list

`electronics_agent` retrieves only these `KnowledgeType` values when
the caller does not pin one explicitly:

- `DESIGN_DECISION` — schematic topology, PCB stack-up, routing
  rationale.
- `COMPONENT` — datasheet excerpts, BOM rationale, supplier-quote
  metadata.

Out of scope by default (override with `request.knowledge_type`):
`FAILURE` (mechanical-side), `SESSION` (sim runs), `CONSTRAINT`
(cross-domain — owned by `compliance_agent`).

## Per-skill context contracts

### `run_erc` (electrical rules check)

Goal: invoke the KiCad ERC adapter against a schematic and return a
structured violation report.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `Schematic` (pivot), `Component`, `Net`, `PowerRail`                              |
| Knowledge    | `DESIGN_DECISION` (topology choices), `COMPONENT` (datasheet voltage / current)   |
| Tool results | Prior ERC runs on the same `Schematic.id` — `SessionRun` graph nodes              |
| Constraints  | Voltage limits, current capacity (queried via `twin_core.constraint_engine`)      |

```python
request = ContextAssemblyRequest(
    agent_id="electronics_agent",
    query="ERC violations for STM32F407 board, 3.3V rail",
    scope=[ContextScope.ALL],
    work_product_id=schematic_id,
    knowledge_top_k=8,
    graph_depth=1,
    token_budget=8000,
)
```

### `run_drc` (design rules check)

Goal: invoke KiCad DRC against a PCB layout and return manufacturing
violations.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `PCB` (pivot), `Layer`, `Footprint`, `Net`                                        |
| Knowledge    | `DESIGN_DECISION` (routing strategy), `COMPONENT` (footprint references)          |
| Tool results | Prior DRC runs for the same `PCB.id`                                              |
| Constraints  | Trace width, clearance, via diameter, stack-up                                    |

```python
request = ContextAssemblyRequest(
    agent_id="electronics_agent",
    query="DRC for STM32F407 PCB, 4-layer stack-up",
    scope=[ContextScope.ALL],
    work_product_id=pcb_id,
    knowledge_top_k=8,
    graph_depth=2,
)
```

### `power_budget`

Goal: validate that the supply rails carry the per-component current
draw with margin.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `PowerRail` (pivot), `Component` (with current draw metadata)                     |
| Knowledge    | `COMPONENT` (datasheet typ/max current), `DESIGN_DECISION` (PSU sizing rationale) |
| Constraints  | PSU capacity, derating policy                                                     |

```python
request = ContextAssemblyRequest(
    agent_id="electronics_agent",
    query="3.3V rail budget — sum of typical and peak currents",
    scope=[ContextScope.ALL],
    work_product_id=power_rail_id,
    knowledge_top_k=10,
    graph_depth=1,
    token_budget=8000,
)
```

### `validate_bom`

Goal: cross-check the schematic, BOM, and PCB for component identity
agreement.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `BOM` (pivot), `Component`, `Schematic`, `PCB`                                    |
| Knowledge    | `DESIGN_DECISION` (selection rationale), `COMPONENT` (alternates)                 |

This skill **leans on `ConflictDetector`** (MET-322): a BOM/schematic
MPN mismatch surfaces as a `blocking` conflict and the agent must
refuse to release the BOM until the user resolves it.

```python
request = ContextAssemblyRequest(
    agent_id="electronics_agent",
    query="BOM cross-check for revision 0.3",
    scope=[ContextScope.ALL],
    work_product_id=bom_id,
    knowledge_top_k=15,
    graph_depth=2,
    token_budget=12000,
)
response = await assembler.assemble(request)
if response.has_blocking_conflict:
    raise BomConflictError(response.conflicts)
```

## Conflict-detection fields

The electronics agent populates these `metadata` keys at ingest /
extraction time so `ConflictDetector` can compare across sources:

| Field      | Value example      | Severity (default) |
| ---------- | ------------------ | ------------------ |
| `mpn`      | `STM32F407VGT6`    | blocking           |
| `voltage`  | `3.3V`             | warning            |
| `current`  | `200 mA`           | warning            |
| `package`  | `LQFP-100`         | info               |
| `footprint`| `LQFP_14x14_100`   | info               |

The most common real-world break is schematic vs BOM disagreement on
`mpn` (a substitution applied to the BOM that never made it into the
schematic). MET-322 makes that a hard fail.

## Staleness expectations

Electronics decisions age faster than mechanical — supplier
availability shifts, component lifecycles change, and EOL
notifications make month-old datasheet excerpts unreliable.

Recommended thresholds:

- **Default** (`1.0`) for prototyping.
- **`0.5`** for ongoing design — drops superseded BOM entries and
  ~30+ day stale datasheets.
- **`0.3`** for production cut-over — only the freshest data; suspect
  anything older than ~15 days.

`MET-329` (supplier quote ingestion, P1.14) refreshes
`COMPONENT`-typed entries automatically; setting
`staleness_threshold=0.3` after that lands gives the agent a
near-real-time view of pricing and stock.

## Cross-references

- [Context Engineering spec](../architecture/context-engineering.md)
- [Mechanical Agent context spec](mechanical-context-spec.md) —
  mirror format for the ME side.
- `digital_twin/context/role_scope.py` — code that enforces this map.
- `digital_twin/context/conflicts.py` — MET-322 detector + severity
  table.
