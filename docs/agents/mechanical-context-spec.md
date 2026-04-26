# Mechanical Agent — Context Specification

> **Status:** P1.13. Companion to
> [`docs/architecture/context-engineering.md`](../architecture/context-engineering.md).
> The role's allow-list lives in
> `digital_twin/context/role_scope.py::_ROLE_KNOWLEDGE_TYPES` keyed by
> `mechanical_agent`.

## Role allow-list

`mechanical_agent` retrieves only these `KnowledgeType` values when
the caller does not pin one explicitly:

- `DESIGN_DECISION` — bracket geometry, material choices, tolerance
  rationale.
- `COMPONENT` — fasteners, inserts, structural parts.
- `FAILURE` — observed failure modes (insert pull-out, fatigue
  cracks, thermal warping).

Out of scope by default (override with `request.knowledge_type`):
`SESSION` (sim runs — owned by `simulation_agent`) and `CONSTRAINT`
(cross-domain — owned by `compliance_agent`).

## Per-skill context contracts

Every skill below is exposed via a `ContextAssemblyRequest`. The
example construction at the end of each section is the canonical
shape downstream agent code should mirror.

### `validate_stress`

Goal: run a CalculiX FEA analysis against a CAD model and validate
results against safety factors.

| Source        | What                                                                               |
| ------------- | ---------------------------------------------------------------------------------- |
| Twin nodes    | `CADModel` (pivot), `Material`, `LoadCase`, `BoundaryCondition`                    |
| Knowledge     | `FAILURE` (same material), `DESIGN_DECISION` (material choice / safety factor)     |
| Tool results  | Prior CalculiX runs on similar geometry — reachable via `SessionRun` graph nodes   |
| Constraints   | Safety factor, max stress limit (queried via `twin_core.constraint_engine`)        |

```python
request = ContextAssemblyRequest(
    agent_id="mechanical_agent",
    query="von-Mises stress under 3g vibration on aluminium 6061 bracket",
    scope=[ContextScope.ALL],
    work_product_id=cad_model_id,
    knowledge_top_k=8,
    graph_depth=1,
    token_budget=8000,
    staleness_threshold=0.5,  # ignore decisions older than ~30 days
)
```

### `generate_mesh`

Goal: invoke FreeCAD to mesh a CAD model with sensible element
quality.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `CADModel` (pivot), `MeshSettings`                                                |
| Knowledge    | `DESIGN_DECISION` (similar meshing jobs reflected as decisions)                   |
| Tool results | Prior FreeCAD meshes for the same `CADModel.id` — graph traversal via edge type  |
| Constraints  | Element size, quality threshold (Jacobian, aspect ratio)                          |

```python
request = ContextAssemblyRequest(
    agent_id="mechanical_agent",
    query="meshing parameters for thin-wall aluminium bracket",
    scope=[ContextScope.GRAPH, ContextScope.WORK_PRODUCT],
    work_product_id=cad_model_id,
    knowledge_top_k=3,
    graph_depth=2,
)
```

### `check_tolerances`

Goal: validate that mating parts respect their assembly tolerances.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `Assembly` (pivot), `Tolerances`, `MatingParts` neighbours                        |
| Knowledge    | `DESIGN_DECISION` (tolerance choices), `COMPONENT` (selected fasteners / inserts) |
| Constraints  | GD&T standards applicable to the assembly                                         |

```python
request = ContextAssemblyRequest(
    agent_id="mechanical_agent",
    query="tolerance stack-up for SR-7 bracket assembly",
    scope=[ContextScope.GRAPH, ContextScope.KNOWLEDGE],
    work_product_id=assembly_id,
    knowledge_top_k=5,
    graph_depth=2,
)
```

### `generate_cad`

Goal: synthesise a parametric CAD model from a structured spec.

| Source       | What                                                                              |
| ------------ | --------------------------------------------------------------------------------- |
| Twin nodes   | `Spec` (pivot), prior `CADModel` revisions, `Material`                            |
| Knowledge    | `DESIGN_DECISION` (geometry rationale), `FAILURE` (avoid past mistakes)           |
| Constraints  | Manufacturing process, packaging envelope                                         |

```python
request = ContextAssemblyRequest(
    agent_id="mechanical_agent",
    query="parametric CAD for SR-7 mounting bracket, M3 fastener",
    scope=[ContextScope.ALL],
    work_product_id=spec_id,
    knowledge_top_k=10,
    token_budget=12000,
    staleness_threshold=0.5,
)
```

## Conflict-detection fields

The mechanical agent populates these `metadata` keys on its ingested
content so `ConflictDetector` (MET-322) can compare across sources:

| Field      | Value example         | Severity (default) |
| ---------- | --------------------- | ------------------ |
| `mpn`      | `M3-A2-70` (insert MPN) | blocking         |
| `material` | `Ti-6Al-4V`            | warning           |
| `tolerance`| `H7/g6`                | warning           |
| `package`  | `Helicoil 1185-3CN`    | info              |

When the schematic / BOM / datasheet disagree on any of these for the
same MPN, `response.has_blocking_conflict` flips; the agent should
refuse to act and surface the disagreement.

## Staleness expectations

Mechanical decisions age slowly — material selection from 6 months
ago is usually still valid. Recommended thresholds:

- **Default** (`1.0`) for early prototyping — keep everything.
- **`0.7`** during DVT — drop superseded entries but tolerate older
  decisions.
- **`0.4`** during certification — fresh-only; superseded or
  >60-day-old decisions need explicit override.

## Cross-references

- [Context Engineering spec](../architecture/context-engineering.md)
- [Electronics Agent context spec](electronics-context-spec.md) —
  mirror format for the EE side.
- `digital_twin/context/role_scope.py` — code that enforces this map.
