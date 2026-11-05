# Digital Twin Graph Schema

> **Version**: 0.2 (Phase 1 — Implementation)
> **Status**: Approved — matches implementation
> **Last Updated**: 2026-03-03
> **Depends on**: [`architecture.md`](architecture.md)
> **Referenced by**: [`skill_spec.md`](skill_spec.md), [`mcp_spec.md`](mcp_spec.md), [`roadmap.md`](roadmap.md)
> **Implementation**: `twin_core/` (models, graph engine, versioning, constraint engine)

## 1. Overview

The Digital Twin is the **single source of design truth** in MetaForge. It is a versioned, directed property graph that captures every work product, constraint, relationship, and version in a hardware design.

All agents read from and propose changes to the Twin. No agent maintains its own persistent state — the Twin is the canonical record of what exists, what constrains it, and how it evolved.

> **v0.1 note**: The current implementation uses an in-memory graph engine (`InMemoryGraphEngine`). Neo4j integration is planned for v0.2+.

### Design Principles

1. **Graph-native**: Hardware designs are naturally graphs (components depend on each other, constraints span domains). A property graph captures this directly.
2. **Version-everything**: Every mutation creates a version record. The graph supports branching and merging like Git.
3. **Constraint-first**: Constraints are first-class nodes, not annotations. They are evaluated automatically on every proposed change.
4. **Domain-agnostic core**: The Twin schema is generic. Domain-specific semantics live in work product metadata and constraint expressions.

---

### 1.1 Base Types

All graph nodes inherit from `NodeBase`, which provides a UUID identifier and a `NodeType` discriminator. All edges inherit from `EdgeBase` with a typed `EdgeType` field.

#### NodeType Enum

```python
from enum import StrEnum

class NodeType(StrEnum):
    """Discriminator for graph node types."""

    WORK_PRODUCT = "work_product"
    CONSTRAINT = "constraint"
    VERSION = "version"
    COMPONENT = "component"
    AGENT = "agent"
```

*Source: `twin_core/models/enums.py`*

#### NodeBase

```python
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from twin_core.models.enums import NodeType

class NodeBase(BaseModel):
    """Abstract base for all graph nodes."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType
```

All node models (`WorkProduct`, `Constraint`, `Version`, `Component`, `AgentNode`) inherit from `NodeBase` and set `node_type` to a default value matching their type.

*Source: `twin_core/models/base.py`*

#### EdgeType Enum

```python
class EdgeType(StrEnum):
    """Types of directed relationships between graph nodes."""

    DEPENDS_ON = "depends_on"
    IMPLEMENTS = "implements"
    VALIDATES = "validates"
    CONTAINS = "contains"
    VERSIONED_BY = "versioned_by"
    CONSTRAINED_BY = "constrained_by"
    PRODUCED_BY = "produced_by"
    USES_COMPONENT = "uses_component"
    PARENT_OF = "parent_of"
    CONFLICTS_WITH = "conflicts_with"
```

*Source: `twin_core/models/enums.py`*

#### EdgeBase

```python
from datetime import UTC, datetime

class EdgeBase(BaseModel):
    """A directed relationship between two graph nodes."""

    source_id: UUID
    target_id: UUID
    edge_type: EdgeType
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)
```

*Source: `twin_core/models/base.py`*

#### Quantity

```python
class Quantity(BaseModel):
    """A physical value with a unit and optional uncertainty, backed by
    pint for real dimensional-consistency checking."""

    value: float
    unit: str
    uncertainty: float | None = None

    def to(self, unit: str) -> "Quantity": ...       # raises IncompatibleUnitsError on a dimension mismatch
    def compatible_with(self, unit: str) -> bool: ... # same-dimension check, no conversion
    def to_dict(self) -> dict: ...                    # {"value": ..., "unit": ..., "uncertainty": ...}
    @classmethod
    def from_dict(cls, data: dict) -> "Quantity": ...
```

Currency: a `Constraint.unit` that is an ISO 4217 code (`GBP`,
`USD`, `EUR`) is accepted as a currency label, not a pint unit
(`is_currency_code`). A currency limit is compared only with a value in the
same currency (`same_currency`); a different or unknown currency is reported
as not evaluated with a reason, never converted.

`GET /v1/twin/nodes/{id}` also resolves engineering entities (intent,
stakeholder_need, objective, ...) when no work product has that id: `type` is
the entity type and `properties` carries `entity_type`, `status`, `authority`,
`statement` and scalar metadata. It stays 404 when neither exists.

Not a new node type -- a conversion+validation primitive for values already
stored as plain `float`/`str` pairs in `Constraint.metadata`/
`EngineeringEntity.metadata` (a Budget's `system_total`/`unit`, an
Invariant's `limit`/`unit`, an Assumption's `value`/`unit`, ...). Any
`metadata["unit"]` a recorder accepts (`api_gateway/twin/
engineering_entity_recorder.py`) is validated as a real, parseable unit at
record time; `twin_core.consistency.metrics.compute_metric_total` uses it to
correctly convert a metric stored under a dimensionally-compatible-but-
different unit (`"mass_g"` when the budget's own unit is `"kg"`) instead of
silently treating it as absent, and raises `IncompatibleUnitsError` (a
`ValueError`) when a metric is found under a genuinely incompatible
dimension (`"mass_mm"`) -- callers (`twin_core.consistency.gates`) catch
that and degrade the affected check to `NOT_EVALUATED`, never a crash or a
silent pass. A small set of currency codes (`usd`/`gbp`/`eur`/`jpy`) are
each registered as their own mutually-incompatible dimension, since this
codebase does no real foreign-exchange conversion -- cross-currency data is
correctly rejected rather than converted at a bogus 1:1 factor.

*Source: `twin_core/models/quantity.py`*

---

## 2. Node Types

### 2.1 WorkProduct

An WorkProduct represents any design output: a schematic, BOM, PCB layout, firmware source file, test plan, simulation result, or manufacturing file.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.WORK_PRODUCT` |
| `name` | `str` | Yes | Human-readable name (e.g., `"main_schematic"`) |
| `type` | `WorkProductType` | Yes | Enum: see below |
| `domain` | `str` | Yes | Engineering domain (e.g., `"mechanical"`, `"electronics"`) |
| `file_path` | `str` | Yes | Relative path within the project directory |
| `content_hash` | `str` | Yes | SHA-256 hash of file contents |
| `format` | `str` | Yes | File format (e.g., `"kicad_sch"`, `"step"`, `"json"`) |
| `metadata` | `dict` | No | Domain-specific key-value pairs |
| `created_at` | `datetime` | Yes | Creation timestamp |
| `updated_at` | `datetime` | Yes | Last modification timestamp |
| `created_by` | `str` | Yes | Agent ID or `"human"` |

**WorkProductType enum**:

```python
from enum import StrEnum

class WorkProductType(StrEnum):
    SCHEMATIC = "schematic"
    PCB_LAYOUT = "pcb_layout"
    BOM = "bom"
    CAD_MODEL = "cad_model"
    FIRMWARE_SOURCE = "firmware_source"
    SIMULATION_RESULT = "simulation_result"
    TEST_PLAN = "test_plan"
    TEST_RESULT = "test_result"
    MANUFACTURING_FILE = "manufacturing_file"
    CONSTRAINT_SET = "constraint_set"
    PRD = "prd"
    PINMAP = "pinmap"
    GERBER = "gerber"
    PICK_AND_PLACE = "pick_and_place"
    DOCUMENTATION = "documentation"
```

The enum has grown since this list was written; `twin_core/models/enums.py`
is the full set. One of the later additions is `RENDER = "render"`
: studio renders of a model from `twin.render_work_product`. The
first view is the node's primary blob (`format` is `png`) and the others are
named blobs in `metadata.image_files` (served by
`GET /v1/twin/nodes/{id}/files/{filename}`). A `GENERATED_FROM` edge points
at the rendered model, and `metadata.source_content_hash` records which
revision of it was rendered, so a render of an older revision can be told
apart from a current one.

Two more are inputs from outside ([Files in and out](file-transfer.md)):
`GCODE = "gcode"`, G-code for one printer (`.gcode`, `.gco`, `.bgcode`),
which `library.dispatch_job` sends; and `IMAGE = "image"`, a picture (a
reference photo, a sketch, a screenshot), with its pixel size in
`metadata.width_px` and `metadata.height_px`.

`SKETCH_STROKES = "sketch_strokes"` is a person's freehand sketch drawn on the
Create stage. Its primary file is an SVG rendering (`format` is `svg`) and the
strokes themselves are the named file `strokes.json` (in
`metadata.sketch_files`, or inline in `metadata.strokes` when the blob store
was unavailable). Each stroke has a stable id, a tool, points in sketch units
(the shorter side of the drawing viewport spans one unit, origin at its
centre) and pressure when the pen reported it. The metadata always says
`authority: human_sketch`, `scale: unknown` and `plane_state: UNASSIGNED`: a
human sketch is not CAD, nothing reads a measurement from it, and it has no
plane until one is proposed and confirmed. `parent_revision` names the
revision it was drawn on top of, and `save_id` is the client's id for the
save, so a retried save returns the revision it already made. See
[the Create view](create-view.md#sketching-on-the-stage). Once the sketch
is placed on a plane, a later save records the plane's state and
`plane_ref` at the time of the save instead of `UNASSIGNED`; the payload can
never set either.

`SKETCH_PLANE = "sketch_plane"` is the plane a sketch is placed on. See
[Sketch planes](#sketch-planes) below.

`SKETCH_INTERPRETATION = "sketch_interpretation"` is the sketch compiler's
proposal for a sketch (fitted primitives and proposed constraints, all
estimated), and `PARAMETRIC_SKETCH = "parametric_sketch"` is the sketch a
person promoted from one, held for approval. See
[The sketch compiler](#sketch-compiler) below.

`GENERATED_SKETCH = "generated_sketch"` is an AI-generated concept sketch: a
proposal until a person accepts it, then a revision of a `GSK-*` item.
Always illustrative. See [Generated concept sketches](#generated-sketch)
below.

`MANUFACTURING_CONTEXT = "manufacturing_context"` is the manufacturing context
every generator reads, an `MFC-*` item. See
[The manufacturing context](#manufacturing-context) below.

**Pydantic Model**:

```python
from datetime import UTC, datetime
from uuid import UUID, uuid4
from pydantic import Field
from twin_core.models.base import NodeBase
from twin_core.models.enums import WorkProductType, NodeType

class WorkProduct(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.WORK_PRODUCT
    name: str
    type: WorkProductType
    domain: str
    file_path: str
    content_hash: str
    format: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str
```

*Source: `twin_core/models/work product.py`*

### 2.2 Constraint

A Constraint is a rule that must be satisfied across one or more work products. Constraints are first-class graph nodes evaluated by the Constraint Engine.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.CONSTRAINT` |
| `name` | `str` | Yes | Human-readable name (e.g., `"max_voltage_3v3"`) |
| `expression` | `str` | Yes | Constraint expression (see Constraint Language below) |
| `severity` | `ConstraintSeverity` | Yes | `ERROR`, `WARNING`, or `INFO` |
| `status` | `ConstraintStatus` | Yes | Current evaluation status |
| `domain` | `str` | Yes | Primary domain (e.g., `"electronics"`) |
| `cross_domain` | `bool` | No | Whether constraint spans multiple domains |
| `source` | `str` | Yes | Free-form string describing origin (e.g., `"user"`, `"agent"`, `"system"`) |
| `message` | `str` | No | Human-readable description of the constraint |
| `acceptance_criteria` | `str` | No | What must be true for this requirement to be considered met |
| `verification_method` | `str` | No | How it's verified (e.g. `"FEA"`, `"test"`, `"inspection"`) |
| `metric` | `str` | No | The property this requirement is about, e.g. `"tip_deflection"` |
| `operator` | `str` | No | Comparison against `limit` -- `"<="`/`">="`/`"=="`/`"<"`/`">"`/`"!="`. Default `"<="` |
| `limit` | `float` | No | The numeric limit `metric` is compared to |
| `unit` | `str` | No | Must be a unit `twin_core.models.quantity` recognizes |
| `target_node_type` | `str` | No | The kind of node `metric` is measured on, e.g. `"cad_model"` |
| `expected_evidence` | `str` | No | The kind of evidence that would verify this, e.g. `"simulation"` -- must be one of `twin_core.models.enums.EVIDENCE_TYPES` |
| `last_evaluated` | `datetime` | No | When the constraint was last checked |
| `metadata` | `dict` | No | Additional context |

`acceptance_criteria`/`verification_method` are real fields (not metadata
keys) so `twin_core.consistency.gates`'s G7 (Verification Readiness) gate
can check a critical requirement for both directly. Both fall back to the
identically-named `metadata` key when empty, for requirements
`RequirementAuthorAgent` wrote before these fields existed.

Gap G-A3: `metric`/`operator`/`limit`/`unit`/`target_node_type`
are a structured measured-key binding -- what property this requirement is
about, compared how, against what limit, on what kind of node -- as an
alternative to the opaque `expression` string. All five are optional and
blank/`None` by default, so every existing `expression`-only Constraint
stays valid unchanged (the same additive discipline `acceptance_criteria`/
`verification_method` already established). `twin.record_constraint_set`
accepts EITHER an `expression` OR a structured `metric`+`limit` binding per
entry -- when only the structured binding is supplied, a benign placeholder
expression (`"True"`) is recorded instead of requiring the caller to also
hand-write Python, since live pass/fail/no_data status for a requirement
comes from `GET /v1/requirements/matrix` (section 2.15, real Claim/Evidence
data), never from evaluating this expression. When present, the structured
binding also becomes a matrix row's `limitText` (e.g. `"tip_deflection <=
0.5mm"`), replacing the free-text `message`/`name` fallback with real,
machine-set data. Deliberately NOT wired into `expression` evaluation
itself, or into the evaluator/sensitivity/optimizer tools
automatically (those already take their own metric/limit kwargs directly)
-- reading a Constraint's structured fields into those tools automatically
is real, separable follow-up work, not this ticket's own minimal scope.

The dashboard's Requirements page (section "Evidence matrix")
gained a "+ New constraint" form writing exactly this binding via a new
`POST /v1/requirements/constraints` route (reusing
`api_gateway.twin.constraint_recorder` directly, the same validation every
MCP caller already goes through) -- create-only for this first cut, no
edit.

Gap G-A2: `expected_evidence` closes the requirement-to-
verification-linkage gap -- `verification_method` says *how* a
requirement is verified as free text, but nothing previously said *what kind
of evidence would actually count*. `expected_evidence` is validated against
`twin_core.models.enums.EVIDENCE_TYPES`, the exact same set
`twin.record_evidence`'s own `evidence_type` argument accepts, so a
requirement can never declare it expects a kind of evidence this codebase
has no way to record. Unlike `verification_method`/`target_node_type` (free
strings), this one is checked, because the entire point of the field is to
be compared against real recorded Evidence later, so a typo should fail
loud at write time. G7 (Verification Readiness) gained a fourth real
per-requirement check -- a critical requirement's `expected_evidence` must
be set, alongside its existing `verification_method`/`source`/
`acceptance_criteria` checks. The Requirements page's evidence matrix rows
now carry `verificationMethod`/`expectedEvidence`, and a row where *both*
are blank renders a red "not declared" badge (the matrix's `no_data`
status pill already covers "no evidence recorded yet" -- the new badge
covers the distinct, earlier gap of "this requirement never even said what
verification it expects"). Deliberately out of scope:
`measurement_method_defined` stays a G7 `NOT_EVALUATED` check -- no field or
metadata convention exists anywhere in this codebase for "measurement
method" as distinct from `verification_method`, and inventing one wasn't
this ticket's own ask; `TraceabilityAgent`/`TraceabilityCoverage`
(`api_gateway/requirement_intelligence/traceability.py`) is a
real, already-tested coverage engine for "requirement without verification"
that has no REST route or dashboard consumer today -- surfacing it is
separable follow-up work, not folded into this smaller, more literal fix.

```python
class ConstraintSeverity(StrEnum):
    ERROR = "error"       # Must be resolved — blocks commit
    WARNING = "warning"   # Should be resolved — does not block
    INFO = "info"         # Informational only

class ConstraintStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    UNEVALUATED = "unevaluated"
    SKIPPED = "skipped"   # Constraint not applicable to current state

class Constraint(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.CONSTRAINT
    name: str
    expression: str
    severity: ConstraintSeverity
    status: ConstraintStatus = ConstraintStatus.UNEVALUATED
    domain: str
    cross_domain: bool = False
    source: str
    message: str = ""
    acceptance_criteria: str = ""
    verification_method: str = ""
    # structured measured-key binding -- alternative to `expression`.
    metric: str = ""
    operator: str = "<="
    limit: float | None = None
    unit: str = ""
    target_node_type: str = ""
    # the kind of evidence expected to verify this requirement.
    expected_evidence: str = ""
    last_evaluated: datetime | None = None
    metadata: dict = Field(default_factory=dict)
```

*Source: `twin_core/models/constraint.py`, `api_gateway/twin/constraint_recorder.py`, `api_gateway/requirement_intelligence/routes.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.3 Version

A Version represents a point-in-time snapshot of the work product graph. Versions form a DAG (directed acyclic graph) that supports branching and merging.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.VERSION` |
| `branch_name` | `str` | Yes | Branch this version belongs to (e.g., `"main"`, `"agent/mechanical/stress-fix"`) |
| `parent_id` | `UUID` | No | Parent version (null for initial version) |
| `merge_parent_id` | `UUID` | No | Second parent (for merge commits) |
| `commit_message` | `str` | Yes | Description of changes |
| `snapshot_hash` | `str` | Yes | SHA-256 hash of the complete graph state at this version |
| `author` | `str` | Yes | Agent ID, `"human"`, or `"system"` |
| `created_at` | `datetime` | Yes | Version creation timestamp |
| `work_product_ids` | `list[UUID]` | Yes | Artifacts modified in this version |

```python
class Version(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.VERSION
    branch_name: str
    parent_id: UUID | None = None
    merge_parent_id: UUID | None = None
    commit_message: str
    snapshot_hash: str
    author: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    work_product_ids: list[UUID] = Field(default_factory=list)
```

*Source: `twin_core/models/version.py`*

### 2.4 Component

A Component represents a physical part used in the design (IC, resistor, connector, etc.) with supply chain metadata.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.COMPONENT` |
| `part_number` | `str` | Yes | Manufacturer part number |
| `manufacturer` | `str` | Yes | Manufacturer name |
| `description` | `str` | No | Part description |
| `package` | `str` | No | Physical package (e.g., `"QFP-48"`, `"0402"`) |
| `lifecycle` | `ComponentLifecycle` | Yes | Production status |
| `datasheet_url` | `str` | No | Link to datasheet |
| `specs` | `dict` | No | Key electrical/mechanical specifications |
| `alternates` | `list[str]` | No | Alternative part numbers |
| `unit_cost` | `float` | No | Per-unit cost in USD |
| `lead_time_days` | `int` | No | Estimated lead time |
| `quantity` | `int` | No | Quantity used in design |

```python
class ComponentLifecycle(StrEnum):
    ACTIVE = "active"
    NRND = "nrnd"               # Not recommended for new designs
    EOL = "eol"                 # End of life
    OBSOLETE = "obsolete"
    UNKNOWN = "unknown"

class Component(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.COMPONENT
    part_number: str
    manufacturer: str
    description: str = ""
    package: str = ""
    lifecycle: ComponentLifecycle = ComponentLifecycle.ACTIVE
    datasheet_url: str = ""
    specs: dict = Field(default_factory=dict)
    alternates: list[str] = Field(default_factory=list)
    unit_cost: float | None = None
    lead_time_days: int | None = None
    quantity: int = 1
```

*Source: `twin_core/models/component.py`*

### 2.5 Agent

An Agent node records which agent produced or modified work products. It connects the provenance chain from human intent through agent execution to work product output.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.AGENT` |
| `agent_type` | `str` | Yes | Agent discipline (e.g., `"mechanical"`, `"electronics"`) |
| `domain` | `str` | Yes | Engineering domain this agent covers |
| `session_id` | `UUID` | Yes | Current execution session |
| `skills_used` | `list[str]` | No | Skill IDs invoked during this session |
| `started_at` | `datetime` | Yes | Session start time |
| `completed_at` | `datetime` | No | Session completion time |
| `status` | `str` | Yes | `"running"`, `"completed"`, `"failed"` |

```python
class AgentNode(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.AGENT
    agent_type: str
    domain: str
    session_id: UUID
    skills_used: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str = "running"
```

*Source: `twin_core/models/agent.py`*

### 2.6 BOMItem

A BOMItem represents a single line item in a Bill of Materials, with procurement and AAS (Asset Administration Shell) compatibility fields.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.BOM_ITEM` |
| `part_number` | `str` | Yes | Manufacturer part number |
| `manufacturer` | `str` | Yes | Manufacturer name |
| `description` | `str` | No | Part description |
| `quantity` | `int` | No | Quantity used in design (default: 1) |
| `reference_designators` | `list[str]` | No | Reference designators (e.g., `["R1", "R2"]`) |
| `unit_cost` | `float` | No | Per-unit cost in USD |
| `specifications` | `dict` | No | Key-value specs (see AAS conventions in Appendix A) |
| `global_asset_id` | `str` | No | URN/IRI for AAS compatibility (see Appendix A) |
| `supplier` | `str` | No | Primary procurement/distributor source (distinct from manufacturer) |

```python
class BOMItem(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.BOM_ITEM
    part_number: str
    manufacturer: str
    description: str = ""
    quantity: int = 1
    reference_designators: list[str] = Field(default_factory=list)
    unit_cost: float | None = None
    specifications: dict = Field(default_factory=dict)
    global_asset_id: str | None = None
    supplier: str | None = None
```

*Source: `twin_core/models/bom_item.py`*

### 2.7 DeviceInstance

A DeviceInstance represents a specific manufactured unit (serial-number-level) of a product, used for field telemetry and after-sales tracking.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.DEVICE_INSTANCE` |
| `serial_number` | `str` | Yes | Unique serial number of the unit |
| `product_id` | `str` | Yes | Product identifier this unit belongs to |
| `firmware_version` | `str` | No | Currently running firmware version |
| `hardware_revision` | `str` | No | Hardware revision (e.g., `"rev-C"`) |
| `manufactured_at` | `datetime` | No | Manufacturing timestamp |
| `provisioned_at` | `datetime` | No | Provisioning/activation timestamp |
| `metadata` | `dict` | No | Additional key-value metadata |
| `global_asset_id` | `str` | No | URN/IRI for AAS compatibility (see Appendix A) |

```python
class DeviceInstance(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.DEVICE_INSTANCE
    serial_number: str
    product_id: str
    firmware_version: str = ""
    hardware_revision: str = ""
    manufactured_at: datetime | None = None
    provisioned_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    global_asset_id: str | None = None
```

`create_device_instance`/`get_device_instance`/`list_device_instances` CRUD (`twin_core/api.py`) and `twin.register_device_instance` (MCP tool) were added -- the model existed since before this epic but was never instantiable. See section 2.18.

*Source: `twin_core/models/device_instance.py`*

### 2.8 TwinModel

A TwinModel represents a product-level digital twin definition that aggregates work products at a specific version.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.TWIN_MODEL` |
| `product_id` | `str` | Yes | Product identifier |
| `version` | `str` | Yes | Product version (e.g., `"1.0.0"`) |
| `name` | `str` | Yes | Human-readable name |
| `description` | `str` | No | Product description |
| `created_at` | `datetime` | Yes | Creation timestamp |
| `metadata` | `dict` | No | Additional key-value metadata |
| `global_asset_id` | `str` | No | URN/IRI for AAS compatibility (see Appendix A) |

```python
class TwinModel(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.TWIN_MODEL
    product_id: str
    version: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)
    global_asset_id: str | None = None
```

*Source: `twin_core/models/twin_model.py`*

### 2.9 DesignElement

A DesignElement represents a logical design block (sub-assembly, module, functional block) with AAS-aligned parameters.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.DESIGN_ELEMENT` |
| `name` | `str` | Yes | Human-readable name |
| `element_type` | `str` | No | Type of element (e.g., `"sub-assembly"`, `"module"`) |
| `domain` | `str` | No | Engineering domain |
| `parameters` | `dict` | No | Key-value parameters (see AAS conventions in Appendix A) |
| `metadata` | `dict` | No | Additional key-value metadata |

```python
class DesignElement(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.DESIGN_ELEMENT
    name: str
    element_type: str = ""
    domain: str = ""
    parameters: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
```

*Source: `twin_core/models/design_element.py`*

### 2.10 HierarchyNode

A HierarchyNode is one position in a project's product hierarchy tree (product -> system -> subsystem -> assembly), added for gap G-B1. One generic node type discriminated by `kind` -- the same "common base, not eight bespoke classes" pattern `EngineeringEntity` already uses -- rather than a class per organizational level. A fabricated part or COTS component is deliberately NOT a HierarchyNode: it's the already-real `WorkProductType.CAD_MODEL` or `BOMItem` a leaf HierarchyNode links to via `REALIZED_BY`/`INSTANCE_OF`, never duplicated.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `UUID` | Yes | Unique identifier (inherited from `NodeBase`) |
| `node_type` | `NodeType` | Yes | Always `NodeType.HIERARCHY_NODE` |
| `kind` | `str` | Yes | One of `product` \| `system` \| `subsystem` \| `assembly` |
| `name` | `str` | Yes | Human-readable name, e.g. `"Upper Arm"` |
| `created_by` | `str` | No | Tool/agent that created this node |
| `metadata` | `dict` | No | Type-specific/derived fields (maturity, lifecycle risk, ...) |

```python
class HierarchyNode(NodeBase):
    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.HIERARCHY_NODE
    kind: Literal["product", "system", "subsystem", "assembly"]
    name: str
    created_by: str = ""
    metadata: dict = Field(default_factory=dict)
```

Rolled-up mass/cost are deliberately NOT cached fields here -- see `twin_core.consistency.hierarchy_rollup.compute_hierarchy_rollup`, computed live over the `CONTAINS` tree so a rollup can never go stale relative to its children.

*Source: `twin_core/models/hierarchy_node.py`*

### 2.11 InterfaceQuantity

A measurable property of an interface between two components -- e.g. "upper-arm tip deflection <= 0.5mm", owned by mechanical. Not a graph node type: `HierarchyNode`'s own design (above) never holds design content directly, and `twin.commit_system_architecture` already has a real, structured home for interfaces -- each `ArchInterface` (`{from, to, interface_type, description}`) between two named components, persisted as metadata on one `SYSTEM_ARCHITECTURE` work product. `InterfaceQuantity` is a value type embedded in that interface's own `quantities` list.

```python
class PredictedValue(BaseModel):
    value: float
    band: float | None = None
    tier: str = ""
    evidence: str = ""  # a reference string -- no MEASURED_BY/PREDICTED_BY edge writer exists yet

class MeasuredValue(BaseModel):
    value: float
    source: str = ""
    timestamp: str = ""

class InterfaceQuantity(BaseModel):
    metric: str
    unit: str          # validated via twin_core.models.quantity.is_valid_unit when non-empty
    limit: float | None = None
    op: str = "<="      # "<=" / ">=" / "==" -- free string, mirrors InvariantComparison's values
    owner: str = ""
    discipline: str = ""
    predicted: PredictedValue | None = None
    measured: list[MeasuredValue] = Field(default_factory=list)
```

`twin.commit_system_architecture` validates every interface's `quantities` through this model at record time (a bad unit or missing metric is rejected, not silently stored) and renders them into the work product's markdown. `GET /v1/twin/hierarchy` resolves interfaces per node by matching a `HierarchyNode.name` against each interface's `from`/`to` component name, surfaced to the Structure tab as `interfaces: InterfaceSummary[]` on each node.

*Source: `twin_core/models/interface.py`*

### 2.12 Tiered Evaluator

Not a node type -- a computation, invoked via `twin.evaluate_metric` (spec §30, "prefer the minimum sufficient fidelity; escalate only when evidence quality demands it"). Only `metric="tip_deflection"` is implemented: a tier-0 closed-form cantilever-beam estimate against a CAD `WorkProduct`'s own recorded `geometry_features.properties.bounding_box`, escalating to a real tier-2 `calculix.run_fea` call when the estimate's margin to a supplied `limit_mm` falls inside its error band.

```python
class Tier0DeflectionResult(BaseModel):
    metric: str = "tip_deflection"
    tier: int = 0
    value_mm: float
    band_mm: float
    limit_mm: float | None = None
    margin_mm: float | None = None   # limit - value; sign shows over/under
    escalate: bool = False           # |margin| < escalation_k * band_mm

def cantilever_tip_deflection_mm(
    *, length_mm, width_mm, height_mm, load_n, youngs_modulus_mpa
) -> float:
    """delta = F L^3 / (3 E I), I = w h^3 / 12."""
```

The band is a fixed prior (`band_fraction` of `limit_mm`, default 0.2) -- calibrated bands from real measurement residuals are Step 11's own scope (`digital_twin/calibration/`), not this one's. Every tier's result is recorded as `twin.record_evidence`, `valid_against` the work product's current revision -- tier-0 as `evidence_type="calculation"`, tier-2 as `"simulation"`.

Tier-2 escalation requires the caller to already have a generated mesh + resolved node sets (`tier2: {mesh_file, fixed_node_set, load_node_set, material, load_force_n}`) -- `twin.evaluate_metric` does not derive FEA boundary conditions on its own. Escalating with no `tier2` args returns a clear `{"attempted": false, "reason": ...}` rather than guessing node-set names for a part it's never seen a load case for (a real, currently-unsolved gap: the existing tooling gets a *human* to point-and-click node sets into a reusable `LOAD_CASE` work product, but there is no programmatic path from "a design changed" to "correct boundary conditions" for a genuinely novel part).

A general per-metric tier *registry* (tier 1 hand-calcs, other metrics) and an automatic "runs on every design change" hook are both deliberately out of scope for this ticket -- see `twin_core/prediction/evaluator.py`'s own module docstring for the full rationale. `InterfaceQuantity.predicted` (§2.11) already has `tier`/`evidence` fields anticipating this, but no `PREDICTED_BY` edge writer exists yet to connect a `twin.evaluate_metric` result back to an interface quantity automatically -- still a manual step.

*Source: `twin_core/prediction/evaluator.py`, `api_gateway/twin/metric_evaluator.py`*

### 2.13 Automatic Revalidation on ECT Commit

`EngineeringChangeTransaction` (`twin_core/models/engineering_change_transaction.py`) gained two fields on top of its existing state machine:

```python
# Plain dicts (RevalidationStep.model_dump(mode="json")), not a typed
# field -- avoids an import cycle with twin_core.consistency.impact.
revalidation_plan: list[dict[str, Any]] = Field(default_factory=list)
revalidation_result: dict[str, Any] | None = None
```

`analyze()` stores `ImpactEngine.analyse()`'s full pre-commit PREVIEW plan here (a projection -- nothing has changed yet). A successful `commit()` OVERWRITES it with the REAL plan, built from `StalenessEngine.propagate()`'s actual writes -- this is the fix for a genuine gap: the original `commit()` never called `StalenessEngine` at all, so no ECT-driven change ever marked anything stale for real until this. `commit()` propagates once per REVISE/SUPERSEDE/DEPRECATE/INVALIDATE operation in the patch (best-effort -- a propagation failure never fails an already-successful commit), unions the resulting `StaleMarking`s, and reuses `ImpactEngine.build_revalidation_plan` (now public, shared between the pre-commit preview and this real post-commit build) to turn them into the same `RevalidationStep` shape.

`twin.execute_revalidation_plan(ect_id)` (requires the ECT to be COMMITTED) is the "now actually re-run them" half: for each plan step naming a stale `engineering_entity` whose `entity_type == "evidence"` and whose `metadata["replay"]` is a `{tool_id, args}` dict, it looks `tool_id` up in an injected dispatch table and calls it again with `args` (`api_gateway/twin/revalidation.py`'s `make_revalidation_executor`). A successful replay records a NEW Evidence entity with `supersedes=<old id>` (the revalidation flow -- never a mutation of the old evidence). Everything else -- a non-evidence entity the impact walk reached (e.g. a Constraint), evidence with no `replay` recipe (hand-authored, or older than this field), or a `tool_id` the caller didn't wire in -- is reported in `manual_review_needed`, never guessed at.

`metadata["replay"]` is a new, optional, unvalidated passthrough on Evidence (`twin.record_evidence`'s `evidence_recorder.py`, stored opaquely -- this module never inspects its shape). Only a caller that actually knows how to re-invoke itself should set it; `api_gateway/twin/metric_evaluator.py` is the first (`{"tool_id": "twin.evaluate_metric", "args": {...the exact original top-level kwargs...}}`). It is NOT derived from `producer.tool`/`inputs` (those are often a descriptive Python dotted path, not an invokable MCP tool id -- guessing replayability from them would be wrong more often than right).

**Deliberately out of scope** (each a separable piece, no acceptance-criterion pressure to build now): property-level impact through `Constraint.dependencies` (that field doesn't exist yet -- explicitly deferred, confirmed still true during this ticket's own scoping); a dashboard UI showing the impact graph and re-run progress (zero existing ECT-related UI surface today -- new work from scratch).

*Source: `twin_core/transactions/ect.py`, `twin_core/consistency/impact.py`, `api_gateway/twin/revalidation.py`*

### 2.14 Sensitivity Analysis

Not a node type -- a computation, invoked via `twin.rank_sensitivity` (target lifecycle spec App. A "Solver" / step 15). One-at-a-time finite differences: perturb one parameter, recompute the metric, measure how far the margin (`limit - value`) moved.

```python
class ParameterSensitivity(BaseModel):
    parameter: str
    is_categorical: bool = False   # True for a discrete axis (e.g. material)
    baseline_value: float | str
    perturbed_value: float | str
    baseline_margin: float
    perturbed_margin: float
    sensitivity: float   # delta_margin/delta_parameter (continuous) or raw delta_margin (categorical)

class SensitivityRanking(BaseModel):
    metric: str
    baseline_value: float
    limit: float
    baseline_margin: float
    rankings: list[ParameterSensitivity]   # sorted by |sensitivity| descending
```

`metric="tip_deflection"` ranks `wall_thickness_mm` and `length_mm` against a new rectangular-hollow-tube deflection formula (`twin_core/prediction/evaluator.py`'s `hollow_tube_tip_deflection_mm`, `I = (W H^3 - (W-2t)(H-2t)^3)/12`) -- the existing solid-beam model (`cantilever_tip_deflection_mm`) has no wall-thickness concept at all, so this is a real, scoped extension, not free. `metric="mass"` ranks `wall_thickness_mm` (continuous, `hollow_tube_mass_kg`) and a caller-supplied set of candidate materials (required: library names, no default set) (categorical -- the actual margin delta from swapping material, not a derivative, since "per unit of what" means nothing for a discrete choice). Material density and elastic modulus come from the material library through `resolve_density_kg_m3`/`resolve_elastic_properties` (replacing the earlier built-in table) -- not a second material list.

Every ranking is recorded as `twin.record_evidence` (`evidence_type="calculation"`), `valid_against` the work product it was computed from.

**Deliberately out of scope**: a general "sweep any Design IR parameter" engine -- `twin_core/design_ir/` turned out to be a CAD-*authoring* intermediate representation (a sequence of FreeCAD/CadQuery operations), not an engineering-analysis parameter model, and the real yardstick arm's own geometry was authored via a raw `cadquery.execute_script` call rather than through Design IR at all, so there's no live Design IR document to sweep; tier 1 (torque) sensitivity (tier 1 still doesn't exist, and isn't needed for either named metric here); a dashboard tornado-chart UI (zero existing chart/ranking UI anywhere in `dashboard/src/` -- same scope cut the dashboard bullet in the revalidation section above already established).

*Source: `twin_core/prediction/sensitivity.py`, `api_gateway/twin/sensitivity.py`*

### 2.15 Evidence-Backed Requirement Matrix

Not a node type -- a join, computed live via `GET /v1/requirements/matrix`, over requirements (Constraint), claims (`twin_core.consistency.claims`), and the Evidence those claims cite.

```python
class EvidenceSummary(BaseModel):
    id: str
    method: str          # producer.tool
    tier: int | None
    value: float | None
    limit: float | None
    margin: float | None
    staleness: str        # current/stale/superseded/invalid/revalidated

class RequirementMatrixRow(BaseModel):
    requirementId: str
    requirementName: str
    limitText: str         # the requirement's own recorded text, e.g. "<= 4.5 kg"
    status: str            # "pass" | "uncertain" | "fail" | "no_data" | "stale" | "unverified"
    detail: str
    artefactIds: list[str]
    evidence: list[EvidenceSummary]
```

`twin_core.consistency.claims.list_claims_for_requirement` (new) is the one piece that was genuinely missing: `evaluate_claim` only ever evaluated a single KNOWN `(artefact, requirement)` pair; the matrix needs "what claims exist for this requirement" as its own lookup, which it gets by walking `requirement_id`'s incoming `CLAIM_EDGE_KIND` edges and re-using `evaluate_claim` per match (same live-computed status, no parallel logic).

Status is derived fresh on every call, same "never a stored, driftable boolean" discipline as `evaluate_claim` itself:

- `no_data` -- no claim recorded against the requirement at all, and no current design record or measuring tool result states its metric. The detail says which, and names the tool call that would close it, for example "run power.check_budget and record its result with twin.record_evidence, with 'total_supply_current' in it".
- `pass` / `fail` measured from the design -- no claim, but the requirement is a `metric` + `limit` binding and the current revision of a `pcb_layout`, `schematic`, `firmware_source` or `bom` records that metric. The detail starts "measured from the design:" and names the record and the value; `artefactIds` is that record. `simulation_result` numbers are never read this way: they verify a requirement only through a claim.
- `pass` / `fail` measured by a tool -- no claim, no design record states the metric, but a current result of a measuring tool does: `kicad.*`, `firmware.build`, `firmware.static_check`, `firmware.run_emulated`, `power.check_budget`, or `datasheet` evidence. The detail starts "measured by a tool:" and carries the basis (tool and version, typical or maximum figures, a stand-in board); `artefactIds` is that evidence. Any other tool's result (an FEA run, say) still counts only through a claim.
- `unverified` -- a SUPPORTED claim against a `metric` + `limit` requirement, with no number for that metric in the evidence it cites, in a design record or in a measuring tool's result. Nothing was compared to the limit, so it is not a pass: the completion verdict reads it as INCONCLUSIVE and a promotion as UNCERTAIN. When a number is found the claim is graded `pass` / `fail` against it, and the detail starts "claim graded against ...".
- `stale` -- a claim's cited evidence includes at least one entity whose current staleness is STALE/SUPERSEDED/INVALID, flagged even when the claim itself is still SUPPORTED by other current evidence (the ticket's own "flags stale evidence after a change" wording).
- `fail` / `uncertain` / `pass` -- claim SUPPORTED, no stale evidence; derived from the most current evidence's own margin. Reads either evidence result shape this codebase produces: the metric evaluator's tier-0/tier-2 (`value_mm`/`limit_mm`/`margin_mm`/`escalated`) or the sensitivity ranking's output (`baseline_value`/`limit`/`baseline_margin`, no `escalated` concept of its own). Negative margin is `fail`; a tier-0 result with `escalated=true` is `uncertain` (too close to call without a higher-fidelity check, the evaluator's own semantics); otherwise `pass`. With no margin to read, a free-text requirement's claim is still a bare `pass` with a note; a `metric` + `limit` requirement's claim is graded as above or reported `unverified`.

The evidence matrix also closed a small gap in the metric evaluator's own tier-2 evidence: the raw `calculix.run_fea` output had no `tier` key of its own (only inferable from `producer.tool` string matching) -- `api_gateway/twin/metric_evaluator.py` now persists `{"tier": 2, "metric": ..., **fea_result}` for tier-2 Evidence, so the matrix (and any future consumer) reads `tier` directly rather than guessing.

Dashboard: `RequirementsPage.tsx` gained an "Evidence matrix" section (below the existing requirements quality table, not a new page) with per-row status badges, an expandable evidence-detail row (method/tier/value/limit/margin/staleness), a client-side CSV/MD export (mirrors `BomPage.tsx`'s own `handleExportCsv` pattern -- no backend export route needed), and a Structure-tab link per row. That link is a plain `/twin` link, not a per-node deep-link: `TwinViewerPage.tsx` does support one (`?node=<hierarchy_node_id>`), but the matrix's own `artefactIds` are WorkProduct ids, not HierarchyNode ids -- no Constraint-to-HierarchyNode mapping exists yet to resolve one from the other, so this ticket does not fabricate that link; a real one is a separable follow-up.

*Source: `twin_core/consistency/claims.py`, `api_gateway/requirement_intelligence/matrix.py`, `api_gateway/requirement_intelligence/routes.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.16 MaturityGate

The first place in this codebase a gate genuinely REFUSES rather than only reports. `twin_core.consistency.gates`'s G3-G8 design-flow gates are a different, pre-existing concept -- per that module's own docstring, a FAILED status only informs a human reviewer, since `enforce_consistency_gate` doesn't exist. `MaturityGate` is its own `NodeType` (not a generic `EngineeringEntity` tag) because a promotion attempt has real, structured multi-field state worth typing, the same reasoning `EngineeringChangeTransaction` already used for itself.

```python
class MaturityLevel(StrEnum):
    CONCEPT = "concept"
    SIM_VALIDATED = "sim_validated"
    PHYSICALLY_VALIDATED = "physically_validated"
    RELEASED = "released"

class RequiredClaimDecision(StrEnum):
    PASS = "pass"
    UNCERTAIN = "uncertain"
    FAIL = "fail"
    WAIVED = "waived"   # FAIL, covered by an approved waiver -- still counts as
                         # satisfied, but recorded honestly, never relabelled "pass"

class RequiredClaimResult(BaseModel):
    requirement_id: UUID
    requirement_name: str
    decision: RequiredClaimDecision
    detail: str
    waiver_id: UUID | None = None

class MaturityGate(NodeBase):
    level: MaturityLevel
    required_claim_ids: list[UUID]
    results: list[RequiredClaimResult]
    promoted: bool = False
    blocked_reason: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    k: float = 1.0
    comment: str | None = None  # a human reviewer's own rationale
```

There is deliberately no `update_maturity_gate` -- each `twin.attempt_promotion` call persists a brand-new, immutable record (whether it promoted or was refused), so "why wasn't this promoted" always has a real, queryable history instead of one mutable, driftable record.

`attempt_promotion(twin, *, project_id, level, required_claim_ids, k=1.0, decided_by=None, comment=None, reject=False)` -- note `decided_by` is supplied by the *gateway* from the approval record, never passed through from a tool argument or request body (`api_gateway/requirement_intelligence/promotion.py`) reuses rather than re-derives:

- **Pass/fail decision** -- §2.15's own `build_requirement_matrix` status per required requirement id. `status="pass"` maps to `PASS`; `"uncertain"`/`"stale"`/`"unverified"` to `UNCERTAIN`; `"fail"`/`"no_data"` to `FAIL`. No second margin/band computation is invented here.
- **Waiver coverage** -- the existing `entity_type="waiver"` / `twin.approve_engineering_entity` mechanism, the same `list_engineering_entities(..., entity_type="waiver")` + `authority in APPROVED_AUTHORITY` pattern `gates.py`'s own G8 `_evaluate_waivers_check` uses (that constant is now public, promoted the same way `ImpactEngine._build_revalidation_plan` was promoted, since this is now a second real consumer) -- but scoped per-requirement here: only a waiver whose `parent_refs` names the SPECIFIC blocked requirement id unblocks it, since G8's own check is a blanket "any waivers outstanding" scan and a mass-specific waiver must never silently unblock an unrelated requirement.
- **Human authority** -- the person who *approved the call*, resolved by the server. This used to be a caller-supplied `decided_by` string carrying "the same agent-asserted-identity trust level every `created_by` field already carries", which was true and was the problem: on this tool the string is not a label attached to a write, it **is** the write. The model filled it, so the gate recorded whatever name the model chose, and over local stdio -- where writes are exempt from approval -- that was the only authority recorded at all.

    Now: `twin.attempt_promotion` is never exempt from approval, even locally; a caller that passes `decided_by` gets an error rather than a silent drop; and the dispatcher injects the approver under a reserved key it strips from client input first, so the name cannot be forged by imitating the convention. An approval that does not identify anyone is refused rather than recorded as anonymous. `decided_by` is still recorded on EVERY attempt, promoted or not.

    On a gateway running `METAFORGE_AUTH_MODE=off` there is no verified identity, so a dashboard click records `local:dashboard` with `verified=False`. That is an honest statement of what is known. It is not the same as a name nobody ever established.

Gap G-G4 closes the "gates evaluate real measured data and block on 'no data'" ask two ways: the evidence-gate logic above ALREADY blocked on `no_data` since the evidence gate shipped (live-validated against the real arm project's `no_data` requirements as part of this ticket); what was missing was the reviewer's own **veto**. `reject=True` refuses a promotion EVEN IF every required claim is `pass`/`waived` -- the evidence gate is a necessary condition for promotion, never a sufficient one that overrides a human's own judgement. The rejecting human is the approver, taken from the approval rather than the request. `comment` is a human-authored rationale, distinct from the system-derived `blocked_reason`, recorded on either outcome (a reviewer can leave context on an approval too, not only a rejection); when rejecting without an explicit comment, `blocked_reason` falls back to `f"rejected by {decided_by}"` rather than a bare `None`.

`twin.attempt_promotion` (MCP tool, `tool_registry/tools/twin/adapter.py`) is registered only when the gateway wires in a `promotion_attempter` callable (same optional-constructor-param / conditional-registration pattern every `twin.*` tool in this epic follows). `POST /v1/promotion/attempt` and `GET /v1/promotion` (`api_gateway/promotion/routes.py`) expose the same action and the (previously agent-only, unsurfaced) `twin.list_maturity_gates` read to the dashboard's new "Gate review" section on the Requirements page -- pick required claims, a maturity level and an optional comment, then Approve or Reject. There is deliberately no reviewer field to type into -- the reviewer is whoever is making the request.

**Deliberately out of scope**: any change to `gates.py`'s own G3-G8 advisory posture (`enforce_consistency_gate` still does not exist -- a much larger, separate architectural change than this narrower requirement-matrix-backed gate); rollback/undo of a granted promotion (`MaturityGate` has no such semantics); retrofitting `RunDetailPage.tsx`'s unrelated design-flow `gate-review` UI (a different, pre-existing, advisory-only mechanism -- G3-G8's summary string, not this per-requirement blocking evidence gate) into this one.

*Source: `twin_core/models/maturity_gate.py`, `api_gateway/requirement_intelligence/promotion.py`, `api_gateway/promotion/routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.17 Wall-Thickness Optimiser

Not a node type -- a computation, invoked via `twin.optimize_parameter` (target lifecycle spec App. A "Optimiser", step 10: minimise an objective subject to constraints). The ticket's own Jira scope says "over Design IR parameters", but the sensitivity work's own scoping already found -- confirmed still true here -- that `twin_core/design_ir/` is a CAD-*authoring* op sequence, never persisted onto a committed WorkProduct (the real arm's own "Upper Arm Link" carries `metadata["authored_by"] = "cadquery.execute_script"` and an empty `geometry_features.parameters` dict), not an engineering-analysis parameter model. This optimiser instead searches `wall_thickness_mm`, the one parameter the sensitivity ranking already found dominant for both deflection and mass margin, over the same hollow-rectangular-tube hand-calcs.

```python
class CandidateEvaluation(BaseModel):
    wall_thickness_mm: float
    mass_kg: float
    deflection_mm: float
    deflection_margin_mm: float
    stress_mpa: float
    safety_factor: float
    sf_margin: float
    feasible: bool

class OptimizationResult(BaseModel):
    status: str  # "optimal" | "infeasible" | "already_feasible_at_min"
    detail: str
    winner: CandidateEvaluation | None
    candidates: list[CandidateEvaluation]
```

Mass is the ticket's own stated OBJECTIVE, not a third constraint alongside SF/deflection -- the project's separate `moving_mass_budget` Constraint covers the whole assembly, not this one part in isolation, and no per-part budget allocation exists to check a share of it against (`BudgetAllocation.owner`/`.discipline` (section 2.11) records who owns an interface quantity, not a numeric per-part mass share). Both constraints are monotonically non-decreasing in wall thickness (thicker wall -> stiffer -> lower deflection; thicker wall -> lower bending stress -> higher safety factor) for a fixed outer envelope, so the minimum-mass feasible point is exactly the smallest wall thickness where both constraints first hold -- bisection is exact here, not a heuristic, and no general nonlinear optimiser (e.g. scipy.optimize) was reached for.

The safety-factor check is a real, new tier-0 hand-calc (`cantilever_max_bending_stress_mpa`, `sigma = M c / I` at the fixed end) -- no prior ticket in this epic computed stress at all, only deflection/mass. It needed a real yield-strength table, which didn't exist: `tool_registry/tools/cadquery/materials.py` gained `MATERIAL_YIELD_MPA` + `resolve_yield_mpa` (same name set, same "unrecognized material raises" discipline as the existing `MATERIAL_ELASTIC_MPA`/`resolve_elastic_properties`). "Confirmed by FEA" (the ticket's acceptance wording) is achievable only when a real `calculix.run_fea` mesh/load case is supplied for tier-2 escalation -- this ticket does not attempt automatic FEA boundary-condition derivation for a novel part (the same real, still-unsolved gap documented above).

Every search is recorded as Evidence, `valid_against` the work product (the staleness pinning). When a feasible winner is found, it's also recorded as a Decision via the EXISTING `twin.record_decision` mechanism (its own "alternatives" field) -- no new "Decision with alternatives" node type was built, since `record_decision` already is exactly that; each rejected candidate the bisection evaluated becomes one alternative, with the constraint that rejected it as its `reason_rejected`.

**Deliberately out of scope**: proposing the winning wall thickness as an actual geometry change via ECT (`ControlledEntityKind` only supports `constraint`/`engineering_entity` today, not `work_product` -- a nontrivial, separate widening of the transaction engine itself, not a quick follow-up, same honesty precedent as deferring `Constraint.dependencies` above); regenerating/committing the optimised geometry (no parametrized re-authoring script exists for the real arm part -- its original `cadquery.execute_script` source was never persisted with a substitutable `wall_thickness_mm` variable); any dashboard UI (no UI surface named in this ticket's own Scope, same precedent as the earlier tools in this epic).

*Source: `twin_core/prediction/optimizer.py`, `api_gateway/twin/optimizer.py`, `tool_registry/tools/cadquery/materials.py` (library figures), `tool_registry/tools/twin/adapter.py`*

### 2.18 Realise + Learn: Device Instances, Measurements + Calibration

The final step of this epic. `DeviceInstance` (spec section 2.7) already existed but had no CRUD or MCP tool -- `twin.register_device_instance` fixes that, persisting a real manufactured unit and an optional `INSTANCE_OF` edge to the design revision (a WorkProduct) it was built from, when the caller has one to name. `product_id` stays a free-text identifier exactly as already documented, never assumed to be a resolvable graph ref -- a unit can be registered before or without a born-digital design record on hand.

```python
async def register(
    *, serial_number: str, product_id: str,
    firmware_version: str = "", hardware_revision: str = "",
    manufactured_at: str | None = None, provisioned_at: str | None = None,
    design_revision_ref: str | None = None,   # WorkProduct name/id -- optional
    project_id: str | None = None, metadata: dict | None = None,
) -> dict
```

`InterfaceQuantity` (section 2.11) has no node id of its own -- embedded in `ArchInterface.quantities` inside a `SYSTEM_ARCHITECTURE` WorkProduct. `twin.record_measurement` appends a real `MeasuredValue` there (explicit `work_product_id`, or the project's one such document -- the one-per-project assumption section 2.11 already makes), and links the measuring `DeviceInstance` via a new `MEASURED_BY` edge to that WorkProduct (the edge names the document holding the quantity; the measurement's own `interface`/`metric` metadata disambiguates which one within it):

```python
async def record(
    *, device_instance_id: str, from_component: str, to_component: str,
    metric: str, value: float, unit: str = "mm", source: str = "", timestamp: str = "",
    work_product_id: str | None = None, project_id: str | None = None,
    predicted_value: float | None = None, predicted_tier: int = 0,
) -> dict
```

Residual computation needs a real predicted value. Rather than trusting a possibly-stale or absent `quantity.predicted` field, the caller supplies `predicted_value` explicitly -- e.g. from a `twin.evaluate_metric` call made just before this one, the natural, honest composition. When the quantity's own `predicted` is still unset, this call also backfills it (the first real prediction a quantity gets is whatever the caller most recently computed); an ALREADY-set `predicted` is never overwritten by a later measurement call. Omitting `predicted_value` records the measurement but skips calibration entirely -- never fabricates a predicted value to diff against.

**Calibration** (`digital_twin/calibration/store.py`, no twin/MCP dependency): a residual is `predicted - measured`, persisted as Evidence (`evidence_type="inspection"`, reusing the existing Evidence mechanism, not a new node type) keyed by `(metric, tier)`. `compute_calibrated_band` turns a list of residuals for one `(metric, tier)` into `band = k * stddev(|residual|)` (`k=2.0` default) once at least 3 samples exist -- below that, returns `None` so the caller's existing fixed prior stays in force, zero regression risk. This is deliberately running-stats, not full conformal prediction: the ticket's own acceptance wording ("the residual narrows the FEA band") only needs a real dispersion estimate that tightens with more measurements, not a coverage-guaranteed interval.

`twin.evaluate_metric` now takes an optional calibrated-band lookup: when a calibrated band exists for `(metric="tip_deflection", tier=0)`, it's converted to an equivalent fraction of `limit_mm` and used INSTEAD of the fixed `band_fraction=0.2` prior -- a full override once real measurement history exists, not a blend, since a real calibrated band is a strictly better estimate. The lookup scans EVERY calibration-residual Evidence entity across every project, on purpose: calibration is a prior for the NEXT project, not a per-project cache, matching the ticket's own acceptance wording ("a new project's prediction uses the calibrated band"). The response gains `band_source` (`"fixed_prior"` | `"calibrated"`) and `calibration_sample_count` so a caller can tell which was used.

**Deliberately out of scope**: job 1 of the ticket's own Jira Scope -- committing Gerbers as WorkProducts and a `kicad-cli pcb export pos` pick-and-place adapter method -- was NOT built. The real arm yardstick project has zero PCB/electronics artifacts anywhere in its 34 work products (it's a purely mechanical demo), and the ticket's own Acceptance criterion never exercises job 1 at all (only device registration, measurement recording, and calibration). Building it against synthetic Gerber bytes would be exactly the kind of fabricated demo this epic's own discipline avoids; filed as its own deferred follow-up instead. Also deliberately out of scope: any dashboard UI (no UI surface named in this ticket's own Scope, same precedent as the earlier tools in this epic).

*Source: `digital_twin/calibration/store.py`, `api_gateway/twin/calibration.py`, `api_gateway/twin/device_instance_recorder.py`, `api_gateway/twin/measurement_recorder.py`, `api_gateway/twin/metric_evaluator.py`, `tool_registry/tools/twin/adapter.py`*

### 2.19 DesignLoopIteration -- Closed Design Loop

Its own node type: one candidate value evaluated during one closed design loop run (`twin.start_design_loop`, gap G-G1, target lifecycle spec's "dual state machine: propose -> constraint engine -> commit/reject -> next iteration"). Does not reimplement the search -- composes the already-shipped `twin.optimize_parameter` (section 2.17) unchanged, which already runs a real propose -> evaluate -> revise -> repeat bisection to convergence or proven infeasibility, with an iteration budget (`max_iterations`, default 60). That tool's own gap was that its trace lived only inside one opaque Evidence `result` blob; this ticket persists EVERY candidate it evaluates as a real, queryable node instead.

```python
class DesignLoopIteration(NodeBase):
    id: UUID
    node_type: NodeType = NodeType.DESIGN_LOOP_ITERATION
    loop_id: UUID
    iteration_number: int
    work_product_id: UUID
    parameter_name: str        # e.g. "wall_thickness_mm"
    parameter_value: float
    metric: str                # e.g. "mass_kg" -- the objective being minimised
    objective_value: float
    constraints_status: dict[str, float]  # e.g. {"deflection_margin_mm": 0.12, "sf_margin": 0.4}
    feasible: bool
    status: str = "candidate"  # "candidate" | "converged" | "infeasible"
    is_winner: bool = False
    approved: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
```

Deliberately generic field names (`parameter_name`/`parameter_value`/`metric`/`objective_value`), not wall-thickness-specific -- the search itself generalized beyond `wall_thickness_mm` (gap G-G2) reusing this same node type, no schema migration needed. No `"exhausted"` status: `optimize_wall_thickness` silently accepts its best candidate within `max_iterations` rather than distinguishing "ran out of budget" from "converged" -- inventing that distinction here would claim precision the underlying algorithm doesn't have.

Each candidate is linked to the prior one via `EdgeType.SUPERSEDES` (the same edge the revalidation flow already uses for "this is the newer replacement of that") and to the requirement(s) it was evaluated against via `EdgeType.CONSTRAINED_BY`. The winning candidate is index-identified, not value-matched: `already_feasible_at_min` -> `candidates[0]` (the lower-bound check itself was already feasible), `optimal` -> `candidates[-1]` (`winner = eval_at(hi)` is appended right before the optimizer returns) -- matching by `wall_thickness_mm` VALUE would be wrong, since the final `eval_at(hi)` can legitimately re-evaluate to the exact same float as the immediately preceding loop-appended candidate.

`twin.get_design_loop(loop_id)` returns the full iteration timeline (the dashboard's "iteration timeline" requirement). `twin.approve_design_loop(loop_id, approved_by)` records a human's approval of the converged winner -- the "with a human approving at gates" half of this ticket's own yardstick line; raises when the loop never converged (no winning candidate to approve), the same honesty precedent as every other approval gate in this codebase.

`POST /v1/design-loop/start`, `GET /v1/design-loop/{loop_id}`, `POST /v1/design-loop/{loop_id}/approve` expose the same three actions to the dashboard's Requirements page ("Design loop" section, below the evidence matrix) -- reusing the SAME bound optimizer instance `twin.optimize_parameter` uses, so Evidence/Decision recording isn't duplicated between the MCP tool and the REST route.

**Deliberately out of scope**: duplicate-commit guards / infeasibility heuristics beyond what the optimizer itself already returns / cost-budget policy tuning; wiring gate evaluators to block the loop on evidence gaps; an eval harness; true multi-objective/Pareto-front search (the real scope here is single-objective-with-constraints, matching the ticket's own literal example -- see below). The loop mechanism runs synchronously to completion in one call (bisection converges in well under a second) rather than exposing a step-by-step "run one iteration" API -- there is no real mid-search moment a human needs to inspect; the real gate is the end-of-loop approval this ticket already builds.

#### Generalizing beyond wall_thickness_mm (gap G-G2)

`make_design_loop_starter`'s own `start()` used to read `c["wall_thickness_mm"]`/`c["mass_kg"]`/`c["deflection_margin_mm"]`/`c["sf_margin"]` directly off each raw candidate dict -- real, but only ever matching `make_wall_thickness_optimizer`'s own candidate shape, contrary to `DesignLoopIteration`'s own already-generic field names. `make_design_loop_starter` gained `parameter_name`/`metric`/`candidate_mapper` (a `dict -> (parameter_value, constraints_status)` function) to make that ingestion generic, defaulting to the EXACT prior wall-thickness mapping -- every existing caller (the MCP tool, the REST route) is unaffected; the full pre-existing `TestMakeDesignLoopStarter` suite passes unchanged as the regression proof.

Proven with a genuinely SECOND real optimizer, not a rename: `optimize_tube_height`/`make_tube_height_optimizer` (`twin_core/prediction/optimizer.py`, `api_gateway/twin/optimizer.py`) sweep `height_mm` with `wall_thickness_mm` held fixed, over the SAME hollow-tube hand-calcs `optimize_wall_thickness` already uses. Monotonic bisection is exactly as sound here as over wall thickness: for fixed wall thickness, increasing height strictly raises the section's moment of inertia (cubic in height) -- strictly lowering deflection and bending stress -- while ALSO strictly increasing cross-sectional area (the outer width exceeds the inner cavity's width for any positive wall thickness), so the minimum-mass feasible point is exactly the smallest height where both constraints first hold. `twin.start_tube_height_design_loop` (MCP tool) plugs it into the identical persistence/read/approve machinery via `make_design_loop_starter(twin, optimize=make_tube_height_optimizer(twin, ...), parameter_name="height_mm", candidate_mapper=tube_height_candidate_mapper)` -- `twin.get_design_loop`/`twin.approve_design_loop` need no tube-height-specific variant; they already operate on any `DesignLoopIteration` by `loop_id`.

The dashboard's "objective vs iteration chart" (its own Jira wording) is a small hand-rolled inline-SVG sparkline (`IterationSparkline`, `dashboard/src/pages/RequirementsPage.tsx`) added to the existing Design Loop section -- no chart library exists in this codebase, and a true Pareto-front multi-objective view is aspirational Jira framing for what the ticket's own literal example ("minimise mass subject to SF>=2 and deflection<=0.5mm") is actually single-objective-with-constraints. The sparkline and the iteration table's own column headers now read `parameter_name`/`metric` off the fetched iterations rather than hardcoding "wall_thickness_mm"/"mass_kg" text, so they render correctly for either loop kind without any dashboard-side branching.

*Source: `twin_core/models/design_loop_iteration.py`, `twin_core/prediction/optimizer.py`, `api_gateway/twin/design_loop.py`, `api_gateway/twin/optimizer.py`, `api_gateway/design_loop/routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.20 Decision records: evidence links + dashboard cards (gap G-G3)

`twin.record_decision` already persisted a `WorkProductType.DESIGN_DECISION` work product with real `alternatives` (structured option/reason-rejected pairs, never prose) and real `parent_refs` edges -- the ticket's actual gap ("Current state: Weak (decisions exist, often without evidence links)") was that nothing connected a Decision to the Evidence that backed it: the rationale string could *mention* a calculation, but no graph edge let a reader or a gate evaluator walk from one to the other.

`record_decision` gained an `evidence_refs: list[str] | None` parameter, resolved through the same `_ref_resolver.py` every other recorder in this package uses and linked via `EdgeType.SUPPORTED_BY` (previously declared, never used) -- a distinct relation from `parent_refs`' own `satisfies`, since "supported by evidence" and "satisfies a requirement" are different facts. `make_wall_thickness_optimizer`/`make_tube_height_optimizer` (section 2.17) now pass `evidence_refs=[out["evidence_node_id"]]` into their existing `decision_recorder(...)` call, so every design-loop-produced Decision links to its own Evidence for free.

The design loop itself (section 2.19) gained one more edge: when `start()` produces both a winning `DesignLoopIteration` and a Decision, it links them via `EdgeType.GENERATED_FROM` (also previously declared, never used) -- `decision -[GENERATED_FROM]-> winning_iteration`. A reader can now walk a converged loop's winner straight to the Decision it produced.

`GET /v1/decisions?related_to=<node_id>` (`api_gateway/twin/decision_routes.py`) answers "what decisions touch this node" for any node with an *incoming* edge from a Decision -- a hierarchy node (via `parent_refs`), an Evidence entity (via `evidence_refs`), or a `DesignLoopIteration` (via the new `GENERATED_FROM` link). It walks `twin.get_edges(node_id, direction="incoming")` and keeps only edges whose source resolves to a `DESIGN_DECISION` work product, filtering by source type rather than by a fixed edge-type allowlist -- `parent_refs`' own `relation` is caller-configurable (default `satisfies`, not fixed), so an edge-type filter would silently miss a Decision linked with a non-default relation. No new twin_core method was needed.

Dashboard: a shared `DecisionList`/`DecisionCard` component (`dashboard/src/components/shared/DecisionList.tsx`, no new dependency) renders at both places the gap's own dashboard-interaction line named -- "Decision cards linked from hierarchy nodes and iterations": the Structure tab (`StructureView.tsx`) shows decisions related to the selected hierarchy node in a local panel (tracked independently of the tab's existing selectedNode/inspector plumbing, which assumes a WorkProduct/CAD node shape a HierarchyNode doesn't have), and the Requirements page's Design Loop section (section 2.19) shows decisions related to a converged loop's winning iteration.

**Deliberately out of scope**: auto-linking a Decision to a HierarchyNode from the design-loop flow itself -- the loop has no `hierarchy_node_id` input today, and wiring one through is a separate, larger change to its signature. The generic `twin.record_decision(parent_refs=[hierarchy_node_id])` path already supports manual/agent-driven linking, sufficient to demonstrate on the arm project per this ticket's own definition of done.

*Source: `api_gateway/twin/decision_recorder.py`, `api_gateway/twin/optimizer.py`, `api_gateway/twin/design_loop.py`, `api_gateway/twin/decision_routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/components/shared/DecisionList.tsx`, `dashboard/src/components/viewer/StructureView.tsx`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.21 Loop quality guards: duplicate detection + iteration budget (gap G-G5)

The ticket bundles four things under "loop quality guards"; two were already real before this ticket. Grounding (a chat-harness safeguard flagging a reply that claims a design action with zero tool calls that turn) is general and unrelated to the design loop specifically -- confirmed already shipped, untouched here. Infeasibility detection is also already real: `status="infeasible"` (section 2.19) already exists and is already persisted/returned; this ticket added no new mechanism for it, only dashboard visibility (below).

The two real, previously-unshipped gaps were a duplicate-commit guard and iteration-budget visibility:

**Duplicate-commit guard.** `DesignLoopIteration` gained `loop_inputs_hash: str | None` -- a sha256 of `{work_product_id, **optimize_kwargs}` (sorted-key JSON, `record_decision` excluded since it toggles a side effect rather than changing the search), stamped on every iteration of a run. Same "identical inputs = the same real-world thing" precedent `decision_recorder.py` already established for Decision records, applied here since a design loop has no single work-product node of its own to hash content against. Before running the bisection, `start()` calls the new `twin.find_design_loop_by_inputs_hash(loop_inputs_hash)` (a one-line `list_nodes(node_type=DESIGN_LOOP_ITERATION, filters={"loop_inputs_hash": ..., "iteration_number": 0})`, mirroring `list_design_loop_iterations`'s own shape) -- a hit means this exact loop already ran, and `start()` returns that prior loop's already-persisted result (`{status, winner, loop_id, iteration_count, iteration_ids, duplicate: true}`) instead of spending a bisection and a whole new iteration subtree on a re-submission. The original loop's `status` (`optimal` vs. `already_feasible_at_min` vs. `infeasible`) is reconstructed from the stored iterations alone -- no winner means infeasible; a winner at `iteration_number == 0` means already-feasible-at-min; otherwise optimal -- exactly mirroring the same index-based logic `start()` already uses to mark the winner in the first place (section 2.19).

**Iteration-budget visibility.** `max_iterations` (the bisection's real budget, already a parameter on `twin_core.prediction.optimizer.optimize_wall_thickness`/`optimize_tube_height`, default 60) is now threaded through `api_gateway/twin/optimizer.py`'s wrapper functions and echoed back in the response alongside the already-present `iteration_count`. "Tokens" (the ticket's own dashboard wording, "Loop health panel (tokens, iterations, failures)") is dropped rather than implemented: this loop makes zero LLM calls -- pure deterministic bisection math -- so there is no real per-loop token cost to attribute; `iteration_count`/`max_iterations` is the honest, measurable budget a dashboard can actually show.

Dashboard: `LoopHealthPanel` (`dashboard/src/pages/RequirementsPage.tsx`) renders in the Design Loop section right after a run -- a status pill, `iteration_count / max_iterations`, and a "duplicate of an earlier run" badge when the guard fired. Built from the mutation's own last result rather than the persisted iteration timeline, since `max_iterations`/`duplicate` are properties of one invocation, not persisted loop state -- there is nothing honest to show after a page reload.

**Deliberately out of scope**: any token/cost tracking on the design loop (no real LLM calls inside it to measure); rebuilding infeasibility detection (already shipped); a generic cross-tool "duplicate call" framework (scoped narrowly to the design loop, matching the narrow, per-recorder precedent rather than inventing shared middleware).

*Source: `twin_core/models/design_loop_iteration.py`, `twin_core/api.py`, `api_gateway/twin/design_loop.py`, `api_gateway/twin/optimizer.py`, `api_gateway/design_loop/routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.22 Concept generation and trade study (gap G-B2)

`twin_core.consistency.gates`'s G5 (Concept Selection) already evaluated whatever it found: "Trade study performed" is a per-decision PASS once a recorded `twin.record_decision` has a non-empty `alternatives` list -- that module's own docstring says explicitly it never builds the "Decision Agent" (target lifecycle spec section 26.12) that would GENERATE alternatives, run the trade study, and select one. This ticket is exactly that missing half, and needed almost no new machinery.

**Concept generation** needed no new tool at all. A "candidate architecture" is a `concept_option` `EngineeringEntity` (a new `EngineeringEntityType` member alongside the existing `intent`/`objective`/... set, recorded via the already-generic `twin.record_engineering_entity`) -- its `metadata` carries `criteria_scores` (e.g. `{"mass_kg": 1.8, "cost_usd": 340, "risk": 3, "performance": 7}`) and `evidence_backed_criteria` (which of those keys came from a real measured source rather than an assertion). Only mass has a real measured source anywhere in this codebase today -- a committed CAD work product's own `metadata.geometry_features.properties.mass_kg`; cost has no real source (no price field exists in `tool_registry.tools.cadquery.materials`, and no live distributor lookup was wired in for this ticket), and risk/performance are inherently subjective in general. This module reports which criteria are asserted rather than pretending otherwise -- the dashboard visually distinguishes a grounded cell from an asserted one, same discipline the evidence matrix already uses for `no_data` vs `pass`.

**The trade study + selection** (`api_gateway/twin/trade_study.py`) is the one genuinely new piece. `weighted_score`/`score_concept_options` are pure functions computing `sum(weight * criteria_scores[criterion])` over the caller-supplied weights (sign encodes minimize/maximize -- a criterion to minimize needs a negative weight so a higher weighted_score always means better). `make_trade_study_selector`'s `select(...)` fetches the named `concept_option` entities, scores them, and records the selection as a real `twin.record_decision` -- reusing that mechanism completely unchanged rather than inventing a second "Decision-like" node type (same precedent section 2.20 already established). Every non-selected option becomes one real `alternatives` entry with its weighted score as `reason_rejected` -- not prose, and not empty, so G5's "Trade study performed" check now actually passes instead of reporting `NOT_EVALUATED` (confirmed live: recording two options, calling `twin.select_concept`, and re-evaluating G5 on the same project flips that exact check from `NOT_EVALUATED` to `PASS`). The Decision is also linked to the selected option via `EdgeType.GENERATED_FROM` (previously declared, reused by the design-loop-winner precedent for the identical "this Decision was generated from evaluating this real node" relationship) -- no new edge type either.

New MCP tool `twin.select_concept` (concept GENERATION stays `twin.record_engineering_entity` with `entity_type="concept_option"` -- there is no deterministic "architecture generator" algorithm to build, and none is specified anywhere in the Planner spec). `GET /v1/trade-study/options`, `POST /v1/trade-study/options`, `POST /v1/trade-study/select` expose the same three actions to the dashboard's "Concept trade study" section (below the feature library) -- reusing the SAME bound `engineering_entity_recorder`/`concept_selector` callables `bootstrap_tool_registry` wires up, so a concept_option or Decision created via the dashboard and via an agent's own MCP call are indistinguishable afterward.

Dashboard: a table with criteria as rows, options as columns, an editable weight per criterion row (weighted scores recompute client-side from the already-fetched `criteria_scores` as weights change -- only the FINAL committed weights get baked into the Decision's rationale at selection time), a "+ Add option" form, and a "Select concept" action requiring a rationale.

**Deliberately out of scope**: a live-editable weight grid with per-cell evidence re-fetch (weights are client-side state, recomputed from data already fetched once); an LLM-driven "generate candidate architectures" action (generation stays a chat-agent/CLI action recording `concept_option` entities one at a time); grounding risk/performance in any real measured source (none exists in general).

*Source: `twin_core/models/engineering_entity.py`, `api_gateway/twin/engineering_entity_recorder.py`, `api_gateway/twin/trade_study.py`, `api_gateway/trade_study/routes.py`, `tool_registry/tools/twin/adapter.py`, `tool_registry/bootstrap.py`, `mcp_core/annotations.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.23 Requirement-driven component selection (gap G-C1)

`twin.record_component_selection` (`api_gateway/twin/component_recorder.py`) has always been presence-only: it persists whichever candidate part a caller already decided on as a `BOMItem`, with zero comparison against what the design actually needs -- no margin, no rationale trail, no Decision. This ticket is the missing "requirement-driven" half, in a new sibling module (`api_gateway/twin/component_selection.py`) rather than a mode bolted onto `trade_study.py`, because the comparison shape is different: a servo's rated torque clearing a required torque with margin is a per-spec threshold/pass-fail check with a real numeric distance, not `trade_study.py`'s weighted-sum score across criteria.

`check_spec_margin(required, actual, op)` is a pure function comparing one real candidate spec value against one required threshold (`">="` or `"<="`); `margin` is always signed so a positive value means "passes with this much headroom" regardless of direction. `check_candidate_against_requirements(specs, required_specs)` runs it for every named requirement against one candidate's specs, reporting a missing spec as a failing, marginless check (distinguishable from "spec present but fails") rather than silently skipping it.

`make_component_selector(twin, *, decision_recorder, component_recorder)`'s `select(...)` takes 2+ candidates (each an mpn + caller-asserted `specs: dict[str, float]`, e.g. `{"torque_kg_cm": 25.0}`), the `required_specs` the design needs (`{"torque_kg_cm": {"op": ">=", "value": 20.0}}`), and which `selected_mpn` was chosen. It computes every candidate's margins, records the selection as a real `twin.record_decision` -- reusing that mechanism completely unchanged (same precedent as `trade_study.py`) -- with every non-selected candidate's margin summary as its `alternatives` entry, then persists the *selected* candidate as a real `BOMItem` via the existing `component_recorder` (no new persistence path). The Decision is linked to the new BOMItem via `EdgeType.GENERATED_FROM` (same "this Decision was generated from evaluating this real node" relationship sections 2.20 and 2.22 already established). Unlike a hard gate, a candidate that fails a requirement can still be selected -- `selected_meets_requirements` surfaces the outcome honestly rather than the tool refusing; an engineer may have a documented reason (e.g. reduced duty cycle) to accept it anyway.

New MCP tool `twin.select_component`. `POST /v1/component-selection/select` exposes the same action to the dashboard's BOM-page "Select component" action, reusing the SAME bound `component_selector` callable `bootstrap_tool_registry` wires up. Unlike `trade_study`'s `GET`/`POST /options`, there is no candidate-listing route here -- a candidate is an mpn + specs supplied inline at selection time, not a pre-recorded graph entity to browse beforehand.

**Honesty note** (same discipline `trade_study.py`'s own docs already established for concept-option criteria scores): the specs compared here are **caller-asserted real datasheet values** -- a human or agent reads a real, published number off a real datasheet and types it in. Nothing in this codebase parses a datasheet PDF or scrapes a distributor page for these numbers; that ingestion pipeline does not exist. `digital_twin/catalog/taxonomy.py`'s `CATEGORY_REGISTRY` already declares typed, range-queryable fields for categories like `servo` (`torque_kg_cm`, `voltage_range`) and `motor_driver` (`v_in_max`, `i_out_max`), but has no seeded sample data -- a live selection needs real numbers supplied at call time.

**Deliberately out of scope**: any real datasheet-parsing/ingestion pipeline (none exists anywhere in this codebase; a separate, much larger effort); routing the margin check through `twin_core.constraint_engine` (that engine evaluates work-product-scoped `Constraint.expression` strings via `eval()` against a graph-derived context -- the wrong shape and over-scoped for a lightweight per-candidate numeric comparison).

*Source: `api_gateway/twin/component_selection.py`, `api_gateway/component_selection/routes.py`, `tool_registry/tools/twin/adapter.py`, `tool_registry/bootstrap.py`, `mcp_core/annotations.py`, `digital_twin/catalog/taxonomy.py`, `dashboard/src/pages/BomPage.tsx`*

### 2.24 COTS parts as geometry: "replace placeholder with part" (gap G-C2)

`twin.record_hierarchy_node` has only ever set a HierarchyNode's `REALIZED_BY` (a `cad_model` work product) and `INSTANCE_OF` (a `BOMItem`) edges once, at node-creation time -- there was no way to attach or swap a node's real geometry afterward. This gap turned out to be the *only* genuinely missing piece: the STEP-ingestion pipeline (`POST /v1/twin/import` -- real bounding box/part-count extraction via the OCCT converter, already dashboard-usable via the existing `ImportZone` component), the 3D preview pipeline (`GET /v1/twin/nodes/{id}/model`, already GLB-converts any `cad_model` work product), and the hierarchical-BOM derivation that reads these exact edges (`twin_core.consistency.hierarchical_bom`) all already existed and needed zero changes.

`api_gateway/twin/hierarchy_recorder.py`'s new `make_hierarchy_geometry_linker(twin)` returns `realize(*, hierarchy_node_id, work_product_id=None, bom_item_id=None)` -- at least one of `work_product_id`/`bom_item_id` is required, both may be given. For whichever is supplied, it REMOVES any existing outgoing edge of that type from the node first (`_replace_outgoing_edge`), then adds the new one -- a node always has at most one `REALIZED_BY` and at most one `INSTANCE_OF` target, never an accumulating history. Both the target work product/BOMItem and the hierarchy node itself are confirmed to exist before linking (`ValueError` otherwise). Because this reuses the exact edges `compute_hierarchy_rollup` and `compute_hierarchical_bom` already read, a node realized here shows up in both with zero further changes.

New MCP tool `twin.realize_hierarchy_node`, classified **DESTRUCTIVE** (not ADDITIVE like `twin.record_hierarchy_node`) since it genuinely removes a prior edge, not just appends -- "replace placeholder with part" is exactly the kind of prior-state-losing decision that classification exists for. `POST /v1/twin/hierarchy/{node_id}/realize` exposes the same action to the dashboard, reusing the SAME bound callable. `GET /v1/twin/hierarchy`'s `HierarchyNodeResponse` gained `realizedByWorkProductId`/`instanceOfBomItemId` (both `null` means "placeholder") so the dashboard knows which nodes need this action.

Dashboard: `StructureView.tsx`'s tree rows gained a "Replace placeholder"/"Replace part" action opening an inline panel with two paths -- upload a real STEP file (reuses `ImportZone` unchanged; on success, a real GLB preview renders via `GET /v1/twin/nodes/{id}/model` in a small self-contained `<Canvas>` scoped to just this panel, not the full twin-viewer's own `R3FViewer`/store, mirroring the same "own small Canvas" pattern `UrdfPreviewPanel.tsx` already uses) or pick an already-recorded `BOMItem` from the project's flat BOM (no 3D preview for this path -- a `BOMItem.cad_model_url` is a caller-asserted external link this pipeline has no fetch-and-convert path for; its own real mpn/manufacturer fields are the honest "preview" here).

**Honesty note**: no real vendor STEP files exist anywhere in this repo to seed a demo with (`tool_registry/tools/occt-converter/`'s own sample assets are synthetic generator shapes, not real vendor parts) -- a live demo needs a real, genuinely-downloadable STEP file supplied at call time, same "caller-asserted real value" discipline sections 2.22 and 2.23 already established for criteria scores and datasheet specs.

**Deliberately out of scope**: any vendor STEP-file library, scraper, or "search vendor sites for a STEP model" feature; fetching/converting an arbitrary external `cad_model_url` on demand (a `BOMItem`'s own `cad_model_url` stays a provenance link, never something this pipeline fetches); a swiftshader/headless-WebGL Playwright rendering harness (no precedent exists anywhere in this codebase to build on; the E2E test covers the pick-existing-BOMItem path, which needs no 3D rendering, and the upload+GLB-preview path is exercised by live validation instead).

*Source: `api_gateway/twin/hierarchy_recorder.py`, `api_gateway/twin/hierarchy_routes.py`, `tool_registry/tools/twin/adapter.py`, `tool_registry/bootstrap.py`, `mcp_core/annotations.py`, `dashboard/src/components/viewer/StructureView.tsx`*

---

### 2.25 Robot pose presets: drag-to-pose + saved poses (gap G-J4)

A `robot_description` work product's `metadata` gained one new optional field:

```
metadata.poses: {
  [poseName: string]: { [jointName: string]: number }  // radians (revolute/continuous) or mm (prismatic)
}
```

No new node type, no new MCP tool, and no new gateway route -- "Save current pose" reuses the existing generic `POST /v1/twin/nodes/{id}/iterate` revision endpoint exactly as-is: `metadata_updates={"poses": {...existing, <name>: <values>}}` merges one named pose into whatever's already saved (never dropping the others) and, because `/iterate` always appends a `WorkProductRevision`, the save shows up in the node's version history for free -- the same "persist + version" behavior `api_gateway/twin/routes.py`'s `update_assembly_joints` route already established for a different `robot_description` metadata field (`metadata.assembly`). `TwinNodeResponse.poses` (populated in `_wp_to_response` from `wp.metadata.get("poses")`) surfaces it back to the dashboard on every node fetch, `None` for every node type or a `robot_description` with no saved poses yet.

The three other pieces are entirely client-side, reading/writing the existing `robotJointValues` slice on `viewer-store.ts` (unchanged) rather than any new server state:

- **Drag-to-pose**: `dashboard/src/lib/robot-drag-controls.ts`'s `RobotPoseDragControls` subclasses `urdf-loader`'s own `PointerURDFDragControls` (raycast + joint-axis projection + URDF-limit clamping, all already implemented there) to report the resulting joint value back into the store and highlight the dragged link's meshes. Mounted by `RobotSceneContents.tsx` only while kinematic control is active (`!robotPhysicsEnabled` -- physics mode drives joints itself); an imperative `viewer-store` bridge (`setOrbitControlsEnabled`, same registration pattern as `_cameraResetFn`) suspends `<OrbitControls>` for the drag's duration so the camera doesn't orbit mid-manipulation.
- **Built-in presets**: Zero, Home (the midpoint of each joint's exported range -- no separate canonical "home" pose exists anywhere in this codebase to read instead), Min, Max -- pure functions of the already-known joint list (`dashboard/src/lib/robot-poses.ts`), nothing to persist.
- **Animated transitions**: a ~600ms ease-in-out interpolation (`runPoseAnimation`/`interpolatePose`/`easeInOutCubic`) driving the same `setRobotJointValue` calls a manual slider drag would, so a preset-selected pose animates in exactly like the existing kinematic-posing effect already expects.

**Deliberately out of scope**: inverse kinematics (dragging the end-effector, solving every upstream joint to reach it) -- no IK solver exists anywhere in this codebase, only the forward "analytic live-solve kinematics" in `api_gateway/constraint/kinematics.py`; posing from a natural-language chat command; trajectory playback (animating through a *sequence* of poses over time) -- depends on not-yet-built multibody-dynamics/trajectory work, itself flagged in Jira as "not in the Phase 3 stack" per MetaForge-Planner.

*Source: `dashboard/src/lib/robot-poses.ts`, `dashboard/src/lib/robot-drag-controls.ts`, `dashboard/src/components/viewer/RobotSceneContents.tsx`, `dashboard/src/components/viewer/RobotControlsOverlay.tsx`, `dashboard/src/components/viewer/R3FViewer.tsx`, `dashboard/src/store/viewer-store.ts`, `dashboard/src/hooks/use-twin.ts`, `dashboard/src/api/endpoints/twin.ts`, `api_gateway/twin/schemas.py`, `api_gateway/twin/routes.py`*

### 2.26 Release packages: versioned snapshot + G8 gating (gap G-I3)

`release_package` is a new `EngineeringEntityType` -- a lightweight, versioned snapshot of a project's current hierarchy, BOM, evidence, and decisions, recorded through the same `twin.record_engineering_entity`-backed `engineering_entity_recorder` closure every other entity type already goes through. Its `metadata` carries only node ids (`hierarchy_node_ids`, `bom_item_ids`, `evidence_ids`, `decision_ids`, `drawing_ids`), a `diff_from_previous` count-delta object, and `gate_status` -- never a deep copy of the referenced items' content:

```
metadata: {
  hierarchy_node_ids: string[]
  bom_item_ids: string[]
  evidence_ids: string[]
  decision_ids: string[]
  drawing_ids: string[]  // always [] -- 2D drawings haven't shipped
  diff_from_previous: {
    compared_to: string | null  // prior release_package's node id, or null for the first
    hierarchy_delta: number
    bom_delta: number
    evidence_delta: number
    decision_delta: number
  }
  gate_status: "passed"  // creation is refused unless this is true
}
```

**The real gate this reuses, not duplicates.** `release_approval` and G8's `evaluate_g8_release` (`twin_core.consistency.gates`) already existed -- real, comprehensive (baseline fixed, no stale evidence, verification coverage complete, waivers approved, a `release_approval` entity approved) -- but purely informational: nothing before this ticket ever called it as anything but an advisory display value. `twin.create_release_package` (`api_gateway/twin/release_package.py`) is G8's first real consumer with teeth: it calls `evaluate_g8_release` itself (with a real `TraceabilityAgent`-backed `traceability_coverage` accessor, so the "verification complete" check can actually evaluate) and refuses to create a package -- raising with every failing check named -- unless the result is `GateStatus.PASSED`. This is additive: `api_gateway/runs/gate_eval.py`'s `TwinConsistencyGateChecker` stays purely advisory everywhere else, unchanged.

`GET /v1/releases?project_id=` lists a project's packages (oldest first), `POST /v1/releases` creates one (409 if G8 isn't passed, naming the failing checks). The dashboard's Requirements page ("Release packages" section) lists them with per-metric count deltas.

**Deliberately out of scope**: drawings (empty list until 2D drawings ship); a full structural diff between releases (a simple count delta ships instead -- no diff/comparison utility exists anywhere in `twin_core` to build a real structural diff on top of); wiring G8 into `twin.attempt_promotion`'s actual blocking gate logic (that stays the separate, already-real mechanism).

*Source: `api_gateway/twin/release_package.py`, `api_gateway/releases/routes.py`, `twin_core/models/engineering_entity.py`, `twin_core/consistency/gates.py`, `dashboard/src/pages/RequirementsPage.tsx`, `dashboard/src/api/endpoints/releases.ts`, `dashboard/src/hooks/use-releases.ts`*

**Follow-up: `twin.create_baseline` (the G8 "configuration baseline fixed" check this all gates on).** `Baseline` (`twin_core/models/baseline.py`, a `BASELINE`-typed node, not an `EngineeringEntity`) was already real and built via `twin_core.transactions.baseline.create_baseline` -- but before this follow-up, nothing in `tool_registry`/`api_gateway` ever called it, so `evaluate_g8_release`'s baseline check could never actually pass on a real project. `twin.create_baseline` (`api_gateway/twin/baseline.py`) auto-discovers the project's current `Constraint`s and `EngineeringEntity`s (the only two `ControlledEntityKind`s a Baseline can pin) via the same project-scoped list calls `release_package.py` uses for its own snapshot, then atomically pins each member's current revision and advances its `authority` to `AuthorityState.BASELINED` (reusing the real optimistic-concurrency `TransactionEngine.commit` path -- a member that changed underneath the call aborts the whole baseline with no partial write). Required inputs: `project_id`, `approved_by` (non-empty list of names), `reason`; optional `name` (defaults to `"Baseline <timestamp>"`). A project with zero constraints/entities is rejected rather than producing a vacuous baseline. Multiple baselines per project are valid (each is a new pin of the then-current revisions).

*Source: `api_gateway/twin/baseline.py`, `twin_core/transactions/baseline.py`, `twin_core/models/baseline.py`*

### 2.27 Test plans: mechanical derivation from test-method requirements (gap G-I2)

`verification_case` (an `EngineeringEntityType` that was already real) had no defined `metadata` convention before this ticket, unlike `release_package`/`concept_option`/`waiver`. This is its new shape, established by `twin.generate_test_plan` (`api_gateway/twin/test_plan.py`):

```
metadata: {
  step: string              // "Measure {metric} on {target_node_type}; acceptance: {metric} {operator} {limit}{unit}"
  acceptance_value: string  // "{limit}{unit}"
  requirement_id: string    // the source Constraint's id
}
```

For every `Constraint` on a project with `verification_method == "test"` (an already-real field), `twin.generate_test_plan` mechanically derives one new `verification_case` entity from the requirement's own structured `metric`/`operator`/`limit`/`unit`/`target_node_type` fields -- pure string formatting over existing data, no new authoring/synthesis logic. This is deliberately process-generic: it works for whatever real requirements a project actually has declared `verification_method="test"`, not hardcoded to any fixed category list (the ticket's own "payload, reach, repeatability" examples are illustrative, not a closed set). Calling it twice creates a second `verification_case` per matching requirement (mirrors `release_package`'s own create-a-new-snapshot-each-call semantics).

`GET /v1/testplans?project_id=` lists a project's generated entries (oldest first), `POST /v1/testplans` generates them. The dashboard's Requirements page ("Test plan" section) renders the step + acceptance value as a table.

**Deliberately out of scope**: FMEA, HALT/HASS, reliability modeling (MetaForge-Planner's `FRAMEWORK_MAPPING.md` discipline #9 "Testing & Reliability" lists these alongside test plans, but marks only test plans as the Phase-1-relevant slice); bench-equipment/procedure authoring beyond the mechanical metric->step derivation; wiring generated `verification_case` entities into `evaluate_g8_release`'s verification-complete check (a real, separable follow-up -- generating a test plan doesn't by itself attach the Evidence that check actually looks for).

*Source: `api_gateway/twin/test_plan.py`, `api_gateway/testplans/routes.py`, `twin_core/models/engineering_entity.py`, `twin_core/models/constraint.py`, `dashboard/src/pages/RequirementsPage.tsx`, `dashboard/src/api/endpoints/testplans.ts`, `dashboard/src/hooks/use-test-plan.ts`*

### 2.28 Bring-up checklists: mechanical derivation from assembly joints (gap G-H3)

`bringup_checklist` is a new `EngineeringEntityType` -- a step-by-step assembly sequence derived from a work product's real `metadata.assembly.joints` (the `AssemblyJoint` list: `name`, `type`, `base`, `follower`, `axis`, `anchor`, `limits`). That list is flat and unordered -- there is no sequence field anywhere in this codebase -- so `twin.create_bringup_checklist` (`api_gateway/twin/bringup_checklist.py`) topologically sorts the `base`->`follower` dependency graph (a part that never appears as a `follower` is already placed; a joint becomes buildable once its `base` part is placed) into a real build order, then formats one instruction per joint. One entity holds the whole ordered checklist (not one entity per step, unlike `verification_case`'s per-requirement convention -- a checklist's steps are read together, not tracked/verified independently):

```
metadata: {
  work_product_id: string  // the source assembly's node id
  steps: [
    {
      step_number: integer
      joint_name: string
      joint_type: string
      base: string
      follower: string
      instruction: string   // "Step {n}: attach {follower} to {base} via {joint_name} ({joint_type} joint)"
    },
    ...
  ]
}
```

A cyclic joint graph (some joint's `base` never becomes reachable by placing joints starting from the real root part(s)) raises a clear error rather than guessing at a partial/wrong order. Multiple independent root parts (e.g. two parallel subassemblies) are valid and supported -- that is a disconnected-but-acyclic graph, not an error.

`GET /v1/bringup?work_product_id=` lists a work product's generated checklists (oldest first, filtered client-side by `metadata.work_product_id` since checklists aren't project-scoped the way test plans are -- a work product may exist before/without a project association), `POST /v1/bringup` generates one. The dashboard's Structure tab ("Bring-up checklist" panel, mirroring the DFM-check button placement) renders the ordered instructions as a table.

**Deliberately out of scope**: any 3D visualization (exploded-per-step or even a simple current-step cross-highlight) -- neither exists as reusable infrastructure in this codebase (`StructureView.tsx`'s own header comment states the Structure tab was "deliberately trimmed... no 3D cross-highlight"); EVT/DVT/PVT staging distinctions (one flat checklist per call, not staged variants); writing to the literal `tests/bringup.md` file in a user's `forge setup`-created project repo (a different mechanism than this Twin-graph-backed capability -- the gateway has no filesystem access pattern to that path, and this is an intentional mismatch, not a gap to bridge).

*Source: `api_gateway/twin/bringup_checklist.py`, `api_gateway/bringup/routes.py`, `twin_core/models/engineering_entity.py`, `api_gateway/twin/schemas.py` (`AssemblyJoint`)*

### 2.29 Firmware scaffolds: CAN node table + header skeleton from assembly joints (gap G-E3)

`twin.create_firmware_scaffold` (`api_gateway/twin/firmware_scaffold.py`) derives a per-joint CAN node table from the same work product `metadata.assembly.joints` section 2.28 reads, reusing its topological build order (joints are assigned a deterministic, 1-indexed CAN node ID equal to their position in that order -- a placeholder scheme, not a real hardware-specific allocation, since no target MCU or bus topology exists anywhere in this codebase to derive one from). Unlike `bringup_checklist`/`release_package`/`test_plan` (one `EngineeringEntity` holding structured metadata), this records a pair of ordinary loadable `WorkProduct`s via the generic `document_recorder` (`api_gateway/twin/document_recorder.py` -- the same facade `api_gateway/runs/fw_handlers.py`'s design-flow phase already uses for its own pinmap/firmware_source pair):

- a `pinmap` work product: a CSV table (`joint_name,joint_type,can_id,lower_limit,upper_limit`), with the same rows mirrored into the work product's own `metadata.joints` (the "content for loadability, metadata for direct reads" convention `twin.record_document`'s own docstring establishes for `simulation_result`) so the dashboard can render a table without parsing the blob;
- a `firmware_source` work product: a minimal C header (`#ifndef`-guarded, one `joint_config_t` struct literal per joint carrying `can_id`/`lower_limit`/`upper_limit`) -- structural scaffolding, not a control loop or CAN protocol implementation.

Both are linked back to the source assembly via a `PARENT_OF` edge (`source_part_node_ids`). A cyclic joint graph raises the same `AssemblyGraphError` as section 2.28 rather than guessing at an order.

`POST /v1/firmware/scaffold` (dashboard's "Firmware" panel, mirroring the bring-up checklist panel placement) creates one pair and returns the per-joint table for immediate display.

**Deliberately out of scope**: real CAN bus protocol/message-format implementation (e.g. CANopen frame definitions); actual control-loop/PID logic; real GPIO/MCU-specific pin assignment beyond the CAN-ID placeholder (needs a real target-MCU selection, which doesn't exist as an input); RTOS configuration (`domain_agents/firmware`'s existing generic `configure_rtos` skill already covers this, unrelated to this joint-specific ticket); hardware-in-the-loop testing.

*Source: `api_gateway/twin/firmware_scaffold.py`, `api_gateway/firmware/routes.py`, `api_gateway/twin/document_recorder.py`, `api_gateway/twin/schemas.py` (`AssemblyJoint`)*

### 2.30 Items and revisions: definitions vs records

The twin used to version single nodes but had no notion of "the same thing". Every run that re-authored a part created a new `cad_model` node, and the only link between generations was a `SUPERSEDES` edge added when the new node had exactly the same name as the old one. A renamed part, or any type other than geometry, got nothing: one live project held 16 `cad_model` nodes for 4 real parts.

#### Definitions vs records

Every twin type is one of three kinds. The code-level registry is `twin_core/items/registry.py` (`TWIN_TYPES`); the write paths consult it, and `tests/unit/test_item_registry.py` fails if this table and the registry disagree.

- A **definition** is revised. It has an **item** with a stable key and exactly one current head; re-recording it creates the next revision of the same item.
- A **record** happened. It is append-only, never revised and never given an item. A second decision is a second decision, not "the decision, revision 2".
- A **derived** type is a generated view of other nodes. It is not a source of truth.

| Type | Kind | Key prefix | Stored as |
|------|------|------------|-----------|
| `cad_model` | definition | `CAD` | `WorkProduct` `cad_model` (a part: `twin.commit_geometry` without `parts`) |
| `assembly` | definition | `ASM` | `WorkProduct` `cad_model` (`twin.commit_geometry` with `parts`) |
| `constraint_set` | definition | `CS` | `WorkProduct` `constraint_set` (`twin.record_constraint_set`) |
| `intent` | definition | `INT` | `EngineeringEntity` `intent` |
| `stakeholder_need` | definition | `NEED` | `EngineeringEntity` `stakeholder_need` |
| `objective` | definition | `OBJ` | `EngineeringEntity` `objective` |
| `design_value` | definition | `VAL` | `EngineeringEntity` `design_value` |
| `bom` | definition | `BOM` | `WorkProduct` `bom` (electronics BOM recorder) |
| `component_selection` | definition | `CMP` | `BOMItem` (`twin.record_component_selection`), keyed by `role`, else manufacturer + MPN |
| `schematic` | definition | `SCH` | `WorkProduct` `schematic`: a KiCad project's sheets (`twin.record_design_files`) |
| `pcb_layout` | definition | `PCB` | `WorkProduct` `pcb_layout`: a KiCad board (`twin.record_design_files`) |
| `gerber` | definition | `GBR` | `WorkProduct` `gerber`: a board's gerber and drill set (`twin.record_design_files`) |
| `pick_and_place` | definition | `PNP` | `WorkProduct` `pick_and_place`: a board's placement file (`twin.record_design_files`) |
| `pinmap` | definition | `PIN` | `WorkProduct` `pinmap` (`twin.record_design_files`, or `twin.create_firmware_scaffold`) |
| `firmware_source` | definition | `FW` | `WorkProduct` `firmware_source`: a firmware project's sources and build output (`twin.record_design_files`, or `twin.create_firmware_scaffold`) |
| `purchased_model` | definition | `PMD` | `WorkProduct` `cad_model`: a bought part's OpenUSD package with its GLB preview, licence, attribution and mass (`twin.record_design_files` after `simready.import`) |
| `gcode` | definition | `GC` | `WorkProduct` `gcode`: G-code for one printer with the `config.ini` it was sliced with (`twin.record_design_files` after `slicer.slice`), which `library.dispatch_job` sends |
| `sketch_strokes` | definition | `SKT` | `WorkProduct` `sketch_strokes`: a person's freehand strokes from the Create stage, as JSON in a viewport-independent sketch space plus a generated SVG. Each save is the next revision. A human sketch, not CAD: scale unknown, plane `UNASSIGNED` until one is confirmed |
| `sketch_plane` | definition | `PLN` | `WorkProduct` `sketch_plane`: the plane one sketch is placed on (frame, unit, lifecycle state, who proposed it). One item per sketch; every confirm, reassign, undo or archive is the next revision, with a `DEPENDS_ON` edge to the sketch revision it is for |
| `sketch_interpretation` | definition | `SKI` | `WorkProduct` `sketch_interpretation`: the sketch compiler's proposal for a sketch, fitted primitives and proposed, accepted and rejected constraints, all estimated. `DEPENDS_ON` the strokes revision it was made from. A proposal, not CAD |
| `parametric_sketch` | definition | `PSK` | `WorkProduct` `parametric_sketch`: a sketch a person promoted, with only the accepted constraints (re-checked by the gateway) and a feature plan whose depth, angle and material are required input. `DEPENDS_ON` the interpretation and the strokes. Held for approval |
| `generated_sketch` | definition | `GSK` | `WorkProduct` `generated_sketch`: the accepted AI-generated concept sketch for a sketch or a text concept; each accepted proposal is the next revision. Illustrative only: never an editable sketch, CAD or evidence |
| `manufacturing_context` | definition | `MFC` | `WorkProduct` `manufacturing_context`: how the product (`MFC-PROJECT`) or one component (`MFC-<COMPONENT>`) might be made, each field with its authority and provenance. Every change is the next revision; every generated concept records the revision it used. See [the manufacturing context](#manufacturing-context) |
| `appearance` | definition | `APR` | `WorkProduct` `appearance`: how the product and each component look (`APR-PROJECT`): colour, finish, render material and texture refs, keyed by component name. Never CAD, never the manufacturing context. See [the appearance record](#appearance-record) |
| `design_decision` | record | | `WorkProduct` `design_decision` |
| `simulation_result` | record | | `WorkProduct` `simulation_result` |
| `evidence` | record | | `EngineeringEntity` `evidence`, pinned to the revisions it was produced against |
| `interface_edge` | record | | `WorkProduct` `interface_edge`: a typed coupling between two items in different domains, pinned to their revisions. Its status (`proposed`, `confirmed`, `removed`) changes in place; it is never deleted. See [interface edges](#interface-edges-and-cross-domain-impact) |
| `change_impact` | record | | `WorkProduct` `change_impact`: what one change did across domains, the stale results it left and the reruns it proposes, each rerun's approval decision recorded in place |
| `dfm_finding_set` | record | | `WorkProduct` `dfm_finding_set`: one component's heuristic DFM findings under one process rule profile, pinned to the geometry and manufacturing context revisions it read. Guidance only, never an analysis pass. See [DFM findings](#dfm-findings) |
| `approval` | record | | approvals service |
| `session` | record | | `agent_sessions` (Postgres) |
| `run` | record | | runs / design-flow workflow |
| `prd` | definition | `PRD` | `WorkProduct` `prd`: the prd's prose only (background, scope, non-requirements), `twin.record_document` with `document_type='prd'` |
| `prd_view` | derived | | rendered on read by `GET /v1/twin/projects/{id}/prd`: the prd prose plus intent, needs, objectives and the current constraint set (below) |

A type not in the table keeps its earlier behaviour (one node per write, no item) until it is classified. `cad_model` and `assembly` share a family, so a part that gains parts continues its own history as an assembly.

#### Items and revisions

An `Item` node (`NodeType.ITEM`, `twin_core/models/item.py`) carries `key`, `item_type`, `project_id`, `name` (the head's display name, which may drift) and `head_revision` / `head_node_id`. Each revision is the node the recorder already creates (a `WorkProduct`, `EngineeringEntity` or `BOMItem`), and it is never edited after creation:

| Edge | Direction | Metadata |
|------|-----------|----------|
| `REVISION_OF` | revision node -> Item | `revision`, `change_reason`, `run_id`, `author`, `created_at`; `adopted: true` for a pre-existing node folded in; also `status`, and for a run's drafts `change_set`, `phase`, `base_revision`, `prior_node_id` (below) |
| `HEAD` | Item -> current revision node | none; at most one per item, moved on every revision outside a run and on a gate approval inside one |
| `SUPERSEDES` | revision n+1 -> revision n | unchanged meaning; staleness propagation still follows it |

Edges and properties, deliberately both. The edges keep history traversable in Cypher and let a node written before items existed join an item without touching its properties. `head_revision` / `head_node_id` on the Item mirror `HEAD`, so a head read is one node fetch (the same denormalized-mirror precedent as `Baseline.includes`). History is always read from the `REVISION_OF` edges. A revision created through the new path is also stamped at creation with `metadata.item_key`, `item_revision`, `item_type` and, when present, `change_reason`, `run_id`, `revision_author`, so a node read on its own says which revision it is.

A reference is `KEY@n` for one revision, or the bare `KEY` for the current head.

#### Which item a write lands on

No caller has to do anything new. The recorder resolves the item before any write (so a bad reference creates nothing), in this order:

0. Inside a design-flow phase that declares deliverable slots of the write's type, and with neither `item_key` nor `supersedes` given: the matching slot's key, used as if it were `item_key`. The slots ride on the MCP call context like `run_id`; see [deliverable slots](architecture/design-flow-harness.md#deliverable-slots-carry-item-keys) for how a write is matched to a slot. A write that matches none is recorded as its own item and stamped `metadata.undeclared_item: true`, `undeclared_phase` and `declared_item_keys`; a write that matched carries `metadata.flow_slot_key`.

1. `item_key`, if given. A bare key revises that item's head (or names a new item if no item has that key yet). `KEY@n` additionally asserts the head is still `n`; otherwise the call fails with "`KEY` is at @m, not @n" and nothing is written. An unknown `KEY@n` is an error.
2. `supersedes`, a node id: the item that node belongs to.
3. Otherwise, in the same project: an item of the same type family whose head has the same name, or whose key the name derives to (so `Shelf Bracket` and `shelf-bracket` are one item).
4. Otherwise, for geometry only, today's same-name rule: the current same-named `cad_model` with no item is adopted, together with its whole `SUPERSEDES` chain, as revisions `@1..@k`, and the new write becomes `@k+1`.

Nothing found means a new item at revision 1. An unscoped write (no `project_id`) with neither `item_key` nor `supersedes` gets no item, the same rule the geometry `SUPERSEDES` chain always followed: with no project there is no identity to match on. Author and run come from the MCP call context (`actor_id`, and `run_id` when a design-flow run makes the call; `run_id` is null outside a run). Every definition write tool returns `item_key`, `revision` and `item_ref` (`KEY@n`); a write inside a run also returns `revision_status: "draft"`. Revision numbers are `max(last_revision, head_revision) + 1`, so a number is never handed out twice, drafts included.

Reads: `GET /v1/twin/items?project_id=` lists items at their heads; `GET /v1/twin/items/{key}/revisions` returns one item's history, oldest first; the MCP tool `twin.item_history` returns the same by key or by any revision's node id. The response also has a `lessons` list: every `rejected` revision as "already tried `KEY@n`, failed because ..." (its `status_reason`), so a later run does not repeat it. `GET /v1/twin/nodes` lists every node except unapproved drafts (below), and `GET /v1/twin/relationships` omits `REVISION_OF` / `HEAD` because their Item endpoints are not in the node list.

Concurrent writes: two writes to one item can both plan revision n+1. Before linking, each write re-reads the item's head. The one that finds the head already moved is renumbered to the next free number and supersedes the actual head, and its node's `item_revision` stamp is corrected (`item_revision_race` logs the planned and assigned numbers). A write pinned with `KEY@n` is refused instead, with the same stale-revision error as at planning time; its node stays saved but is not a revision of the item. The re-read and the link are not one transaction, so this narrows the window rather than closing it; inside a run the base check at approval (below) catches what it misses.

#### Drafts, approval and closed drafts

A definition written inside a design-flow run is a **draft** in that run's change set. The node is created as usual (stamped `metadata.change_set = <run id>` at creation) and linked by `REVISION_OF`, but `HEAD` does not move and no `SUPERSEDES` is added, so for everyone outside the run the head is still current. The run's gate decides what it becomes. Each `REVISION_OF` edge carries a `status`:

| Status | Meaning | Head |
|--------|---------|------|
| `committed` | written outside any run (the behaviour before drafts existed, unchanged) | moved immediately |
| `draft` | written inside run `change_set`, in phase `phase`, on base revision `base_revision` | not moved |
| `approved` | committed by the run's gate: `gate`, `decided_by`, `approved_at`; `change_reason` is the gate's reason (the agent's own is kept as `draft_change_reason`) | moved to the run's latest draft; `SUPERSEDES` added to what it replaced |
| `rejected` | closed by a gate rejection (`status_reason`, `closed_at`) | never moved |
| `abandoned` | closed by a retry, a rework, a failed or canceled run, or a conflict at run completion | never moved |

The `Item` gains `last_revision` (the highest number handed out) and `drafts`, a map from run id to that run's open draft: `revision`, `node_id`, `base_revision`, `base_node_id`, `phase`, `name`. An item first written inside a run has no head (`head_node_id` null, `head_revision` 0) until its gate approves.

Who sees what:

- **The run itself** sees its drafts over the head: `resolve_item_ref` and `twin.item_history` / `twin.get_node` with `item_key` resolve a bare `KEY` to the run's latest draft; a second write to the same item in the same run revises that draft.
- **Everyone else** (the dashboard, other runs, `GET /v1/twin/items`, `GET /v1/twin/items/{key}/revisions` by default) sees approved heads only. Another run's open drafts are not in a history; `GET /v1/twin/nodes` leaves out open, rejected and abandoned drafts unless `include_drafts=true` (and history records such as `change_impact` unless `include_history=true`); an item with no head yet is not listed. `GET /v1/twin/items/{key}/revisions?run_id=` reads as that run, drafts included.
- **Closed drafts** (rejected, abandoned) stay in every item's history with their status and reason, and never become current.

Approval is atomic and optimistic (spec sections 40 and 41): each item's head must still be the base the run drafted on, or the whole commit is refused with `PATCH_CONFLICT` and nothing moves. See [the run's change set](architecture/design-flow-harness.md#a-runs-change-set-drafts-until-the-gate) for the gate side.

Limits of this first slice: the old revisions of a constraint set keep their `Constraint` nodes, so the constraint engine still evaluates them; the project-wide migration of today's unlinked duplicates is a separate migration ([below](#migrating-legacy-nodes)).

Observability: `item_revision_created` / `item_revision_failed` log events, the `metaforge_twin_item_revision_total{item_type, outcome, resolved_by}` counter, and the `TwinItemRevisionLinkFailures` alert. A failed link never fails the write (the node already exists, and failing would invite a retry that duplicates it); the result carries `item_warning` instead.

#### One home for requirements

Requirements used to be stored three times: as a table in a free-standing `prd` document, as the `constraint_set`, and restated in the requirements phase's "verifiable requirements" decision. The three drifted apart. Now the **constraint set item is the only place requirement values live**, and everything else points at it by `KEY@n`:

| What | Holds | Requirement values |
|------|-------|--------------------|
| `constraint_set` item (`CS-...`) | the requirements: metric, operator, limit, unit, verification method, acceptance criteria | the only copy |
| `prd` item (`PRD-...`) | prose: background, scope, what is out of scope | none of its own; values in the text are checked against the constraint set |
| the prd view | the prd prose, current intent / needs / objectives, and a requirement table read from the current constraint set revision | rendered on read, never stored |
| the requirements decision | the choice, with `depends_on=[CS-...@n]` | none; it links the revision it rests on |

**The prd view.** `GET /v1/twin/projects/{project_id}/prd` renders the project's prd as markdown; `GET /v1/twin/items/{key}/prd` renders one prd prose revision (`PRD-...@n`) the same way. Both return `markdown`, `prose_ref`, `requirement_refs` (the constraint set revisions the table came from), `requirement_count`, `refs` (every `KEY@n` used) and `sources`. The requirement table has one row per current requirement, with columns Ref (`CS-...@n`), Requirement, Metric, Operator, Limit, Unit, Verification method and Acceptance criteria (the constraint's `message` when no acceptance criteria were given). Intent, needs and objectives are listed with their `KEY@n`. When the constraint set gets `@2`, the next read shows `@2`'s values and only those: the constraints of an older revision, or of a draft another run has not had approved, are left out. Constraints recorded outside any item (no project at write time, the dashboard's constraint editor) are listed with an empty Ref. `?run_id=` reads as that run does, its own drafts included.

**A prd write.** `twin.record_document` with `document_type='prd'` (and the requirements phase handler, which uses the same recorder) stores the text as given as the next revision of the project's prd item. A project has one prd: once it has exactly one prd item, a prd written under a new title is that item's next revision. The text is also kept in `metadata.prose` so the view renders without a blob fetch. Before the write, numbers written with a unit (`15 g`, `0.5 W`, `85 °C`, `$20`) are compared with the current constraint set: a value no current requirement states (same number and unit, or the same number in a generated expression) is a **stray value**. The write still succeeds and nothing is dropped; the result lists them in `stray_requirement_values` and explains in `requirement_warning`, and the node keeps them in `metadata.stray_requirement_values`. Every prd write result also carries `requirements_home` (where requirement values live) and `requirement_refs`.

**The decision.** `twin.record_decision` takes an optional `depends_on`: item refs (`KEY@n`, or a bare `KEY`, pinned to its current revision at record time). Each becomes a `DEPENDS_ON` edge from the decision to that exact revision node (edge metadata `kind: decision_basis`, `item_ref`), `metadata.depends_on` on the decision, and a "Depends on" line in its markdown. The same edge is the decision's revision pin (section 2.31): a newer revision of the constraint set marks the decision stale. An unknown ref fails the call before anything is written. The requirements phase records, in order, the constraint set, the prd prose (goal, functional scope, interfaces, environment) and a decision whose rationale counts the requirements and names `CS-...@n` without restating a value.

**The Requirements page** reads the same current requirements: `GET /v1/requirements/matrix` lists only the current constraint set revisions' constraints, each row carries `revisionRef` (`CS-...@n`), and the response carries `revisionRefs`, which the page shows next to the "Evidence matrix" heading.

Observability: `prd_rendered` and `prd_prose_recorded` / `prd_stray_requirement_values` log events, the `twin.prd.render`, `twin.record_prd` and `twin.requirements.current` spans, the `metaforge_twin_prd_requirement_value_total{outcome="matched"|"stray"}` counter and the `TwinPrdStrayRequirementValues` alert.

*Source: `twin_core/items/registry.py`, `twin_core/items/service.py`, `twin_core/items/change_sets.py`, `twin_core/models/item.py`, `api_gateway/twin/item_revisions.py`, `api_gateway/twin/item_routes.py`, `api_gateway/runs/change_sets.py`, `orchestrator/design_flow/slots.py`, `api_gateway/twin/requirements_home.py`, `api_gateway/twin/prd_routes.py`*

### 2.31 Baselines over items, the current view and revision compare

Items (section 2.30) say what each definition is now and what it was. This section is how a reader sees that without wading through every node a run ever wrote: by default the project shows its **current items only**; history, compare and baselines are opt-in views.

#### Baselines pin item revisions

The existing `Baseline` node (`twin.create_baseline` is described in section 2.26) gains item pins rather than a parallel concept:

| Field | Meaning |
|-------|---------|
| `items` | one immutable `{key, item_type, revision, node_id, name}` pin per current item of the project (`KEY@n`) |
| `gate_id` | the gate whose approval recorded it, or null |
| `run_id` | the design-flow run that gate belongs to, or null |
| `source` | `gate` (a gate approval) or `manual` (`twin.create_baseline`) |
| `approved_by`, `created_at`, `reason` | unchanged |

A baseline is never edited after creation: there is no update path, and each pin is a frozen model. Each pinned revision also gets an `INCLUDED_IN_BASELINE` edge to the baseline (edge metadata `item_key`, `revision`). The current revision of an item is its head; an open draft (status `draft`) or a closed one (`rejected`, `abandoned`) never reaches a baseline, and an item whose every revision is still a draft is left out.

Two paths create a baseline, and neither needs a new step from a user or an agent:

- **A gate approval.** After `decide_run_gate` commits the run's change set (section 2.30), it calls `api_gateway.twin.baseline.create_item_baseline(project_id, gate, approver, run_id)`, which pins every current item of the project with the gate, run and approver. It is idempotent per (project, gate, run), so a retried approval does not record two baselines, and best-effort: a project with no current item records nothing, and a failure is logged (`item_baseline_failed`) without failing the approval. A rejection, retry or rework records no baseline.
- **`twin.create_baseline`.** The existing tool now also pins the project's items, so a project whose only definitions are parts can be baselined; its result adds `item_count` and `items` (`KEY@n` list).

#### Read API

| Route | Returns |
|-------|---------|
| `GET /v1/twin/baselines?project_id=` | the project's baselines, newest first |
| `GET /v1/twin/baselines/{id}` | one baseline with its item pins and constraint/entity members |
| `GET /v1/twin/baselines/diff?a=&b=` | per item: `unchanged`, `changed` (`@x -> @y`), `added` (only in `b`) or `removed`; `b=current` compares `a` with the project's current items |
| `GET /v1/twin/items/{key}/revisions` | as section 2.30, plus `baselines` (ids pinning each revision) and `gate` filled from the first such baseline when the edge names none |
| `GET /v1/twin/items/{key}/diff?a=&b=` | compare `KEY@a` with `KEY@b` (defaults: `b` is the current revision, `a` the committed or approved one before it) |
| `GET /v1/twin/current-view?project_id=` | the project page's default view (below) |
| `GET /v1/twin/runs/{run_id}/changes?project_id=` | revisions the run produced (with status and whether each is current) and the baselines its gates recorded |
| `GET /v1/twin/revision-index?project_id=` | node id to `KEY@n` for every committed or approved revision of the project, and for each `Constraint` of a constraint-set revision (`via: constraint_set`), so the BOM and Requirements pages can show a revision per row |

The item diff returns:

- `geometry`: bounding box, volume and mass for `KEY@a` and `KEY@b` and their deltas. When the gateway's geometry differ is configured (a real `freecad.describe_step_file` on both STEP files) it is called with the two revision nodes (it now takes an explicit `previous_work_product_id`); otherwise, or when that call fails, the measurements each revision recorded at commit (`metadata.geometry_features.properties`) are compared. `source` says which (`describe_step_file` or `recorded`). Mass is the recorded mass, else volume times the density of the recorded material when that material is known (`mass_source` says so); it is never guessed.
- `parameters`: generation parameters that changed, were added or were removed.
- `requirements`: for a constraint set, each constraint matched by name, with its value (`operator limit unit`, else its expression) and severity before and after.
- `fields`: other top-level scalar fields that differ (statement, title, part number, quantity), so intent, needs and BOM rows compare too.
- `dependents`: records and baselines still pinned to `KEY@a` (an edge into that revision node, or a record whose `analysed_geometry` pin or `source_cad_model_id` names it). These are what go out of date once `b` is current.

#### The current view

`GET /v1/twin/current-view` (`twin_core/items/current.py`) returns:

- `items`: one row per item at its current revision: `key`, `ref` (`KEY@n`), `revision_status`, `validation_status` (from the project's linked work product for that revision: `valid`, `warning`, `error`, `unknown`), `run_id` and `gate_id` that produced it, `revision_count`, `evidence_state` and `drafts` (the open drafts of any run, for the dashboard's Working toggle). An item with drafts only has a row with no revision; it is not counted. An item that never reached a head and has no open draft (every revision rejected or abandoned) has no row; it stays in its history.
- `groups`: current items grouped by type.
- `records`: decisions, simulation results and evidence, listed as they are (records are never revised). A simulation result or evidence record whose analysed revision (its `depends_on_items` pins and `DEPENDS_ON` revision-pin edges; a simulation result's `analysed_geometry` pin; or any other edge to a revision node) is no longer current, and which names no current revision of that item, is `out_of_date`. The record's own `staleness` wins when set: `revalidated` keeps it current, `stale`, `invalid` and `superseded` mark it out of date; the row carries `staleness` too. `evidence_state` on an item row is `current`, `out_of_date` or `none` accordingly.
- `other`: linked work products that are neither a revision of an item nor a record (unclassified types such as a pinmap, derived views such as the PRD, and unsuperseded definitions written before items existed).
- `counts` and `readiness`: computed over current items plus `other`, never over every node. Readiness is the share whose validation status is `valid`. `superseded_revisions` counts what the default view hides.

Observability: `item_baseline_created`, `item_baseline_exists`, `item_baseline_skipped`, `item_baseline_failed`, `current_view_built` and `item_diff_computed` log events; the `metaforge_twin_baseline_total{source, outcome}` counter (`created`, `existing`, `empty`, `failed`); the `TwinGateBaselineMissing` alert when a gate approval records no baseline; spans `twin.baseline.create_item_baseline`, `twin.items.current_view`, `twin.item_diff`.

*Source: `twin_core/models/baseline.py`, `twin_core/transactions/baseline.py`, `twin_core/items/current.py`, `api_gateway/twin/baseline.py`, `api_gateway/twin/baseline_routes.py`, `api_gateway/twin/item_diff.py`, `api_gateway/twin/item_routes.py`, `api_gateway/runs/routes.py`*

---

### 2.31 Records pinned to revisions and stale evidence {#records-pinned-to-revisions}

A record (section 2.30) is only valid against the definition revisions it was produced from (spec sections 20, 21 and 54). Each record now says which, and says when one of them has moved on.

#### What a record is pinned to

When `twin.record_document` writes a `simulation_result`, `twin.record_decision` writes a `design_decision`, or `twin.record_evidence` writes an `evidence` entity, the recorder resolves its pins before anything is written. Nothing new is asked of the caller; the pins come from what the call already names:

| Record | Pinned to |
|--------|-----------|
| `simulation_result` | first the `analysed_geometry_node_id` (the node the analysis meshed and solved; its `analysed_geometry` block also gains `item_ref`, `item_key` and `item_revision`), then each `source_part_node_ids` node (and `metadata.source_cad_model_id`). Constraint sets only when `depends_on` names them: a result's numbers do not depend on requirement values, so a requirement edit does not stale it unless it verifies those requirements |
| `evidence` | each `valid_against` entry of kind `work_product`, plus every project `constraint_set` head |
| `design_decision` | each `parent_refs` node that is an item revision |

A node is pinned at the revision it is (`item_for_node`), so a simulation of `CAD-BRACKET@1` stays pinned to `@1` whatever happens later. A node that is not an item revision is not pinned. Inside a run, "current" for a constraint set is the run's own draft. All three tools also take an optional `depends_on`: item references (`KEY@n`, a bare `KEY` for the current revision, a revision's node id, or a requirement (Constraint) id, which pins the constraint-set revision that requirement belongs to, `metadata.constraint_set_wp`). A bad explicit reference fails the call before anything is written. One pin per item; an explicit reference wins.

Stored twice, for the same reason as items:

- `metadata.depends_on_items` on the record, `[{item_key, revision, item_ref, item_type, node_id}]`, and `metadata.staleness`, so the record read on its own answers "what was this for, and is it still current".
- one `DEPENDS_ON` edge per pin, record -> the pinned revision node, with the `item_ref` and `metadata.kind = "revision_pin"`, or `"decision_basis"` for a decision (the same edge under another name; the staleness walk follows both). This is what makes invalidation dependency-directed and lets Cypher traverse it.

A decision has one `depends_on` argument and one set of edges: its explicit `depends_on` refs (also listed in the markdown's "Depends on" line) and any `parent_refs` that are item revisions are resolved by the same resolver, each becomes one `decision_basis` edge, and `metadata.depends_on` lists them all as `KEY@n`.

The record tools return `depends_on` (the `KEY@n` refs), `staleness` and `superseded_records`.

#### Status

The vocabulary is `StalenessStatus` (`twin_core/consistency/staleness.py`):

| Status | Set when |
|--------|----------|
| `current` | at record time |
| `stale` | an item the record is pinned to got a newer head; `staleness_reason` says which (`it was for CAD-BRACKET@1, and CAD-BRACKET is now @2`) and `stale_for` holds `{item_key: {pinned, current}}` |
| `superseded` | a re-run of the same check on a newer revision of a pinned item was recorded; `superseded_by` names it and a `SUPERSEDES` edge runs new -> old |
| `invalid` | the record was pinned to a run's draft that its gate rejected or the run abandoned, so it was never about the design |
| `revalidated` | set by a caller that re-checked the record and found it still holds (`set_record_status`) |

A record is never deleted or hidden; only the status changes, and the status write keeps the record's `updated_at`, so a stale record never looks like the newest result of a phase window. "The same check" is the same `analysis_type` (else `load_case`) for a simulation, the same `evidence_type` and producing tool for evidence, and the same title for a decision.

#### When the status changes

- **A head write outside a run** (`commit_revision` moving `HEAD`): every record pinned to an older revision of that item becomes `stale`.
- **A gate approving a run's change set** (`commit_change_set`): the same, per item whose head moved. A record the run made on its draft is now pinned to the head, so it stays `current`, and it supersedes its older run.
- **A draft** moves no head, so it stales nothing. A re-run recorded on an open draft supersedes nothing until the gate approves it.
- **A rejected or abandoned draft** (`close_change_set`): records pinned to it become `invalid`; records on the unchanged head are untouched.

Only records with a pin edge to one of the changed item's revisions are visited (spec section 21): a new bracket revision never touches the leg's analysis. `StalenessEngine.propagate` still runs alongside for constraints and engineering entities pinned through `metadata.depends_on`.

Gates read the same flag; see [stale evidence at the gate](architecture/design-flow-harness.md#stale-evidence-at-the-gate). `GET /v1/twin/nodes/{id}` returns `dependsOn` (`[{itemKey, revision, itemRef, itemType, nodeId}]`) and `staleness` (`{status, reason, staleFor, supersededBy}`) for a record; both are null for a definition.

Observability: `record_pinned`, `record_superseded`, `records_marked_stale`, `records_marked_invalid`, `record_pin_edge_failed` and `record_staleness_failed` log events; the `metaforge_twin_record_staleness_total{record_type, status}` counter (`stale`, `superseded`, `invalid`, `revalidated`, plus `pin_failed` and `propagation_failed`); the `TwinRecordStalenessFailures` alert. A failure after the record or the head write is saved is logged and counted, never raised.

Limits: records written before pinning existed carry no pins and read as `current`; a record about a record (a decision citing evidence) is not pinned through it.

*Source: `twin_core/consistency/record_pins.py`, `api_gateway/twin/record_pins.py`, `api_gateway/runs/analysis_constraints.py`, `api_gateway/runs/gate_eval.py`*

---

### 2.32 Sketch planes {#sketch-planes}

A sketch is drawn with no plane. When it needs a physical meaning (it is used
in 3D, or later made editable), a person places it on a plane. The plane is a
`sketch_plane` item, `PLN-<name>` for the sketch `SKT-<name>`, and the model
is `twin_core/models/sketch_plane.py`.

| Field | Meaning |
|---|---|
| `plane_id` | Stable id, the plane item's key |
| `owner_component` | The item key of the component the sketch belongs to, if any |
| `kind` | `unassigned`, `principal` or `face`. `offset` and `custom` are reserved names and refused today |
| `principal` | `XY`, `XZ` or `YZ` for a principal plane |
| `origin`, `u_axis`, `v_axis`, `normal` | The frame. U and V are unit length and orthogonal, and `normal` must equal `u_axis` x `v_axis` (right-handed) |
| `transform` | The 4x4 local-to-world matrix, row-major, whose columns are U, V, normal and origin. Derived when left out; refused when it does not match the frame |
| `unit` | `mm`, `in` or `unknown`. An unknown unit is kept as it is and never converted |
| `scale` | Model units per sketch unit; `null` is an unknown scale, which every freehand sketch has. A known scale needs a known unit |
| `source_revision` | The sketch revision (`SKT-...@n`) the plane was set against |
| `attachment` | For a face plane: `item_key` and `revision` of the CAD revision, `face_ref` (the face id on that revision), the face's geometry `signature`, `remapped_from` (`KEY@n#face`, when a remap found it again) and, while `REATTACHMENT_REQUIRED`, `pending` (what the remap saw). See [Face attachment](#face-attachment) |
| `state` | `UNASSIGNED`, `PROPOSED`, `CONFIRMED`, `ATTACHED`, `INVALID`, `REATTACHMENT_REQUIRED` or `ARCHIVED` |
| `proposed_by`, `confidence` | Where the plane came from (`user`, `face`, `existing_sketch`, `view`, `component_frame`, `model`) and how sure that source was |

**Frame conventions.** The principal planes use the design IR's names
(`SketchEntity.plane`) with CadQuery's named-plane axes:

| Principal | View | U | V | Normal |
|---|---|---|---|---|
| `XY` | Top | +X | +Y | +Z |
| `XZ` | Front | +X | +Z | -Y |
| `YZ` | Side | +Y | +Z | +X |

A stroke's points are in sketch space (x right, y down, as drawn on the
screen). On the plane, `u = x` and `v = -y`, so up on the screen is +V, and
world is `origin + u * U + v * V`. A confirmed principal plane maps onto a
design IR sketch as `{"plane": principal, "offset": origin . normal}`.

**Lifecycle.** A sketch with no plane item reads as `UNASSIGNED`. A proposal
is returned as `PROPOSED` and never stored. Confirming stores `CONFIRMED`
(a face plane is `ATTACHED`). Archiving stores `ARCHIVED`. `INVALID`
and `REATTACHMENT_REQUIRED` are for face attachments that no longer hold.
Every change is the plane item's next revision; undo is a new revision with
the content from before the last change (`restored_from`), so history is
never rewritten. Each revision's metadata carries the whole plane, the
action (`confirmed`, `reassigned`, `undone`, `archived`), `undo_to`, the
client's `op_id` (a retried op returns the revision it already made), and,
when a reassign moved the strokes, `strokes_rewritten: {from, to}`.

**Graph links.** Each plane revision has a `DEPENDS_ON` edge
(`metadata.kind: sketch_plane`) to the sketch revision it was set against,
and the same pin in `metadata.depends_on_items`. The edge kind is not a
record pin, so saving new strokes does not mark the plane stale. A later CAD
feature built from the sketch depends on both.

**Proposal order.** A proposal reads, in order: the explicit selection, the
plane of another sketch of the same component (0.8), the client's
orthographic view hint (0.7), the component frame's XY plane (0.5), then a
heuristic over the sketch's name and shape (at most 0.45). It is
`ambiguous` when the best guess is below 0.3, or the top two name different
planes within 0.15 of each other. A plane already confirmed for the sketch
is returned as the candidate whatever the hints say: explicit always wins.

**Reassign modes.** `preserve_local` keeps the stroke coordinates (the
sketch moves with its plane). `preserve_world` keeps every point where it is
and is refused when the sketch does not lie in the new plane.
`project_to_target` projects the points straight onto the new plane and
reports how far they moved. A preview writes nothing; a commit that moves
the strokes also saves them as the sketch's next revision.

Routes, under `/v1/twin/projects/{project_id}/sketches/{sketch_key}/plane`:
`GET` (the current plane), `POST propose`, `POST confirm`,
`POST reassign/preview`, `POST reassign`, `POST undo` and `POST archive`.
Each write is reported as a `sketch` operation `job:sketch-plane-<op_id>`.
Nothing takes the camera: the plane changes only through these explicit
calls. Actions are counted in `metaforge_sketch_plane_total` by `action` and
`proposed_by`, and `SketchPlaneChangesFailing` fires when more than a fifth
of the writes in half an hour fail.

*Source: `twin_core/models/sketch_plane.py`, `api_gateway/twin/sketch_planes.py`, `api_gateway/twin/sketch_plane_routes.py`*

#### Face attachment {#face-attachment}

A sketch can sit on a planar face of a CAD revision whose geometry is
`validated` exact geometry. The faces come from `cadquery.describe_faces`
over the revision's own STEP and are kept on the revision as `face_index`,
keyed by its geometry hash, so a second read does no adapter work.

**Face ids are per revision.** A face id (`s<solid>f<face>`, the OCCT
explorer order) means something on its own revision only. A face is never
found again on another revision by its id.

**The frame from a face.** The origin is the face centroid projected onto
its plane, U lies along the longest straight edge of the face's outer loop
(signed so the same edge always gives the same U), the normal is the face's
outward normal, and `V = N x U`, so the frame is right-handed. The unit is
millimetres. On a design IR sketch the plane maps to an explicit `frame`
(`origin`, `x_dir` = U, `normal`) in place of `plane` and `offset`; the
CadQuery Lowering Pass builds it with `cq.Plane`, a pad grows along the
normal, and the FreeCAD Lowering Pass refuses it.

**The signature** (`twin_core/geometry/face_signature.py`) is what was true
of the face when the plane was put on it:

| Field | Meaning |
|---|---|
| `semantic_tag` | Which feature made the face, when the revision's recorded build source says: the extrude of a parametric sketch tags its end faces `<PSK>:<feature>:top` and `:bottom`, and each straight side `<PSK>:<feature>:side:<primitive id>`. `null` otherwise |
| `normal`, `offset` | The unit outward normal and `normal . p` for any point on the plane |
| `area`, `centroid` | In millimetres |
| `loop_edges` | Edges in the outer loop |
| `adjacent_normals`, `adjacent_curved` | The normals of the neighbouring planar faces, and how many neighbours are curved |
| `part_size` | The part's bounding-box diagonal, which scales the position tolerances |

**Remapping.** When a CAD item's head moves, every plane attached to it is
looked for on the new head by signature. A candidate's score is in `[0, 1]`:
a matching tag scores at least 0.7 (the tag dominates; geometry decides
between faces with the same tag), a different tag at most 0.3 of its
geometry score, and with no tag the geometry score alone. Geometry is the
product of a normal term and an area term with a weighted sum of centroid,
offset, loop edge and neighbour terms; position terms halve at 0.3 of the
part size, a normal that turned by more than 20 degrees scores 0.

Each candidate's centroid displacement is also split in two: the part along
the signature's normal, and the part within the face's plane. Moving along
its own normal is the common parametric edit (a box gets taller and its top
rises) and is allowed. A candidate that moved within its plane by more than
one characteristic face length, `sqrt(area)` (the larger of the old and new
areas), is *displaced*: it may be congruent, but it is somewhere else, such
as the twin of a feature that was removed. A displaced candidate is never
picked unless its semantic tag matches the signature's.

A face is picked only when the best score is at least 0.6, beats the
runner-up by at least 0.1, and is not displaced without a matching tag
(`unique`). Otherwise the outcome is `ambiguous` (two faces too close to
call) or `missing` (nothing good enough, the best look-alike is displaced
with no matching tag, the new head is not validated, or its faces could not
be read). Every outcome carries a plain `reason` the dashboard shows, for
example *s0f10 is gone: the closest look-alike, s0f15, sits 20 mm away
along its surface, more than the face's own size (6 mm), and no build tag
says it is the same feature*. Each candidate lists whether it is
`displaced`.

A semantic tag that the build source gives to more than one face of a
revision (the tops of two bosses padded from one sketch, or a side split in
two) names no face in particular, so it is dropped from those faces rather
than trusted to tell them apart.

| Outcome | What is written |
|---|---|
| `unique`, face moved | The plane's next revision, `ATTACHED`, with the new frame and attachment and `remapped_from`. The local sketch coordinates are unchanged, so the sketch moves with its face |
| `unique`, face did not move | No new revision: the attachment's revision and face id are carried forward in place (`plane_remap`), so nothing built on the plane goes stale |
| `ambiguous` or `missing` | The plane's next revision, `REATTACHMENT_REQUIRED`, keeping the last valid frame and attachment, with `attachment.pending`: the outcome, the revision looked at, the candidate faces and the reason. A person is asked |

A plane held as `REATTACHMENT_REQUIRED` is looked at again on the item's
next head, so a revision where the face is back resolves it with no person
needed. Each remap is a `sketch` operation `job:sketch-plane-remap-...`:
`completed`, or `review_required` (waiting for the person) with an `open`
action to the sketch in the Engineering viewer and the plain words *The
surface this sketch was on changed; choose where it goes*.

**Blocking and staleness.** While a plane is `REATTACHMENT_REQUIRED` or
`INVALID`, the authoritative commits that depend on it are refused with
`409` and the reason: the exact build from a parametric sketch on it (or a
rebuild of the CAD item made from one) and the sketch's promotion to a
parametric sketch. Reads and previews (the plane, a proposal, the faces,
an interpretation) are not refused. An exact revision built from a sketch on
a plane carries a `revision_pin` to the plane revision it used, so the
plane's next revision marks it `stale` through the ordinary record
staleness; a stale exact revision reads geometry state `stale`, never
validated, and its STEP export reads `stale` with the reason. A rebuild
uses where the plane is now (a face plane follows its item's head), and a
same-geometry rebuild marks the revision `revalidated`.

**Resolving.** The person picks a face on the attached item's current
revision (`pick_face`), moves the sketch onto a principal plane keeping its
local coordinates (`detach_principal`), or keeps it as a concept with no
plane (`detach_unassigned`). Each is the plane's next revision.

Routes, under `/v1/twin/projects/{project_id}`:

| Route | Does |
|---|---|
| `GET attachable-geometry` | The CAD items whose head is validated exact geometry, with `reason` when there are none |
| `GET items/{item_key}/faces?revision=n` | The planar faces of a validated revision (the head by default), each with its signature summary and semantic tag, and `mesh_map`: face id to `{part_index, face_index, triangle_start, triangle_count}` in the revision's converted GLB |
| `POST sketches/{sketch_key}/plane/attach-face` | Attach to `face_id` on `item_key` (and `revision`), idempotent per `op_id` |
| `POST sketches/{sketch_key}/plane/resolve-reattachment` | `pick_face`, `detach_principal` or `detach_unassigned`, idempotent per `op_id`; `409` when the plane does not need it |

Counted in `metaforge_face_attachment_total` (by `action`),
`metaforge_face_remap_total` (by `outcome`: `unique`, `ambiguous`,
`missing`, `unavailable`, `error`) and `metaforge_plane_blocked_commit_total`
(by `commit`: `exact_build`, `promotion`). `FaceRemapAmbiguitySpike` fires
when more than half of an hour's remaps (at least four) could not pick one
face.

*Source: `twin_core/geometry/face_signature.py`, `api_gateway/twin/face_attachment.py`, `api_gateway/twin/face_attachment_routes.py`, `tool_registry/tools/cadquery/faces.py`*

---

### 2.33 The sketch compiler {#sketch-compiler}

The sketch compiler turns a person's saved strokes into an editable,
constrained sketch (the Create stage's **Make editable**, see
[the Create view](create-view.md#making-a-sketch-editable)). It keeps four
representations apart, each with its own authority and revisions:

| Representation | Where | Authority |
|---|---|---|
| A generated image of a sketch | `image`, `render` or `generated_sketch` (`GSK-*`, [below](#generated-sketch)) | `illustrative` |
| The person's strokes | `sketch_strokes` (`SKT-*`) | `human_sketch`: not CAD, scale unknown |
| The interpretation | `sketch_interpretation` (`SKI-*`) | `proposal`: every value `estimated` |
| The parametric sketch | `parametric_sketch` (`PSK-*`) | `human_accepted`: accepted rules only, held for approval |

Everything is in the sketch's own 2D space, the strokes' sketch units with y
down the screen. The compiler never reads a plane. When the sketch is
placed, the interpretation and the parametric sketch carry the plane's ref
(`plane_id`, the `PLN-*@n` revision) and its unit, read from the plane item
on the gateway, never from the payload. Otherwise the unit is `unknown`.

**Design IR extensions.** `twin_core/design_ir/models.py` adds the
following, all optional, so existing IR reads and lowers unchanged:

- on every sketch element: `id`, `source_stroke_ids`, `source_ranges`
  (stroke id and inclusive point indices), `estimated`, `confidence`,
  `residual` and `construction` (helper geometry such as a symmetry axis,
  skipped by both Lowering Passes);
- `SketchEllipse` (`center`, `major_radius`, `minor_radius`, `rotation` in
  degrees) and `SketchSpline` (through-`points`, `closed`). The CadQuery
  Lowering Pass draws an arc as a `threePointArc` through its start, middle
  and end (in increasing angle) and a spline as an interpolating `spline`
  through its points (periodic when closed), so a profile of lines, arcs and
  splines extrudes to a solid. Ellipses are refused there with a clear error;
  the FreeCAD Lowering Pass refuses arcs, ellipses and splines;
- `SketchConstraint`: `id`, `type` (`coincident`, `horizontal`, `vertical`,
  `parallel`, `perpendicular`, `tangent`, `equal`, `concentric`,
  `symmetric`, `radius`, `length`), `refs` (an element id, or
  `<id>.start`, `<id>.end` or `<id>.center`), `value`, `unit` (`unknown`,
  `mm`, `in`, `deg`), `status` (`proposed`, `accepted`, `rejected`), `source`
  (`estimated` for a size read off the drawing, `inferred` for a guessed
  rule, `human_confirmed` for one a person stands behind), `confidence` and,
  for tangent circles, `variant` (`external`, `internal`). Only a dimension
  can be `estimated`;
- on `SketchEntity`: `constraints` and `plane_id`.

`twin_core/design_ir/sketch_compiler.py` holds `SketchInterpretation` (the
proposal, with its `frame`, fit and solve reports), `ParametricSketch` and
`feature_plan`. A parametric sketch in millimetres becomes a design IR
`sketch` entity through `to_sketch_entity`, which mirrors y into the plane's
v (up), as the plane manager does. One in an unknown unit is refused: a
sketch of unknown size is not CAD.

**Fitting** (`dashboard/src/lib/sketch/fit/`). Each stroke is resampled
evenly along its length. A stroke whose ends meet within the tolerance is
closed: it is tried as a circle (algebraic Kasa fit, then Gauss-Newton on
the geometric distance) and an ellipse (moments, then refined on the radial
distance). Otherwise it is cut at corners (local maxima of the turning angle
over a short window, after light smoothing, kept apart by a minimum gap).
Each run is fitted greedily over its Douglas-Peucker breakpoints with the
line (total least squares) or arc that reaches furthest inside the
tolerance. Neighbouring lines meet at their intersection, and collinear
lines and co-circular arcs merge. A run that only breaks into slivers
becomes a smoothing spline, always shown as uncertain. Closed profiles are
single closed shapes, or open shapes whose ends meet in pairs all round. The
tolerance is 1% of the sketch's extent, never finer than 3 screen pixels at
the scale it was drawn. Ids are the stroke id plus a segment number, so the
same strokes give the same ids (stroke to shape identity).

**Constraint inference** (`lib/sketch/constraints/`) proposes each of
these with a confidence:

- coincident ends;
- horizontal and vertical lines;
- parallel and perpendicular lines, each direction class linked once, so
  accepting them all adds no redundancy;
- tangency: a line and an arc or two arcs meeting smoothly, and a line
  touching a circle;
- equal lengths and radii;
- concentric circles;
- symmetry about the sketch's vertical or horizontal centre line, when two
  or more point pairs make it obvious (a construction axis is added);
- a length for every line and a radius for every circle and arc.

Ids derive from the rule and its refs.

**Solver** (`lib/sketch/solver/`). Levenberg-Marquardt over the packed
primitive parameters (a line is four numbers, a circle three, an arc and an
ellipse five, a spline two per point). It solves only accepted constraints,
from the current geometry, so a typed size re-solves without fitting again.
Angle residuals are scaled by the sketch extent so one tolerance applies.

- *Degrees of freedom* are the parameters less the rank of the constraint
  Jacobian (central differences, Gram-Schmidt), less the construction
  geometry's own freedom. The rigid motions still free (move, turn) are
  taken off to give `shape_dof`.
- *Status* is `fully` (shape and size fixed), `under` or `over`.
- *Conflicts.* When accepted constraints cannot all hold, the dependent
  rows of the Jacobian and the unsatisfied constraints are candidates. Each
  is tried removed, and the ones whose removal lets the rest hold are the
  clash. The report names them in words (sizes first) and offers to remove
  one or, for a size, to change it to the value the others imply.
  Conflicting geometry is not moved.
- *Redundancy.* Constraints already implied by others are reported as
  redundant.
- *Scale.* The first size a person confirms scales the whole sketch so it
  has that value. `frame` (`scale`, `origin`) maps the result back over the
  strokes.

Measured in the test suite on a bracket of 18 fitted primitives and 2
construction axes (an L-shaped outline with a rounded corner, four holes, a
slot and a gusset), as the median of five runs: fitting takes about 18 ms,
solving with every confident rule accepted about 10 ms, and a re-solve
after a typed size about 1 ms, against the 250 ms targets.

**Gateway.** Storing an interpretation:

- validates its shape: ids unique and resolving, point names valid for the
  element, fitted elements marked estimated, finite coordinates;
- refuses degenerate geometry (`422`) and a proposal made from an older
  strokes revision (`409`);
- stores it with a generated SVG, a `DEPENDS_ON` edge
  (`metadata.kind: sketch_compiler`) and a pin to that strokes revision;
- lists accepted constraints that do not hold as `unsatisfied` without
  refusing, since a proposal may be stored mid edit.

Promoting re-checks every accepted constraint with
`twin_core/design_ir/sketch_check.py`, a deterministic mirror of the
browser's residual equations, at 1e-5 of the sketch extent. It refuses with
`409` (naming each failing constraint) when one does not hold, when nothing
is accepted, or when the sketch was redrawn since. The parametric sketch
depends on both the interpretation and the strokes revision, starts
`approved: false` and is approved through the same approver as a design
sketch, listed as `parametric_sketch:<node id>` in
[approvals](approvals.md), a human-authority kind. Storing is idempotent per
`interpretation_id`, and promoting per `promote_id`.

Each interpretation is one `sketch` operation, `job:sketch-compile-<id>`:

1. `preview_ready` at stage `interpretation_proposed` when it is stored;
2. `review_required` with an approval action when it is promoted;
3. `completed` when the parametric sketch is approved.

Writes are counted in `metaforge_sketch_compile_total` by `stage`
(`interpretation`, `promotion`, `approval`) and `outcome` (`proposed`,
`promoted`, `approved`, `duplicate`, `refused`, `failed`).
`SketchCompilerFailing` fires when more than a fifth of them fail on the
gateway in half an hour.

**Decision record: where the solver runs.** The fitter and solver run in
the browser, in TypeScript written for this, with no new dependency and no
WebAssembly.

- *Context.* The targets are 250 ms for fitting a simple sketch and for a
  simple solve, and a typed size must re-solve without recognition running
  again. Sketches here are bounded (tens of primitives).
- *Chosen.* A small Levenberg-Marquardt solver with Jacobian-rank degrees of
  freedom in the browser, and a deterministic residual check in Python on
  the gateway before promotion. It measures well inside the targets, needs
  no round trip per edit and raises no licence question, and the gateway
  still decides what is promoted.
- *Considered: planegcs (FreeCAD's solver compiled to WebAssembly).* More
  complete (more constraint types, better on large sketches), but a large
  binary, LGPL terms to manage, and a second numeric engine to keep in step
  with the gateway check. Worth revisiting if sketches grow past what dense
  linear algebra handles, or when angle and distance dimensions,
  point-on-curve and drag solving are needed.
- *Considered: FreeCAD Sketcher on the gateway.* The same solver CAD export
  uses, but a container round trip for every accept or typed size would miss
  the 250 ms target, and editing would depend on the tool adapter being up.
- *Consequences.* The browser equations (`lib/sketch/solver/equations.ts`)
  and the gateway mirror (`twin_core/design_ir/sketch_check.py`) must change
  together. Only the listed constraint types exist.

*Source: `twin_core/design_ir/models.py`, `twin_core/design_ir/sketch_compiler.py`, `twin_core/design_ir/sketch_check.py`, `api_gateway/twin/sketch_compiler_recorder.py`, `api_gateway/twin/sketch_compiler_routes.py`, `dashboard/src/lib/sketch/`*

---

### 2.34 Interface edges and cross-domain impact {#interface-edges-and-cross-domain-impact}

A change to one item can matter to items in other domains: moving an enclosure's standoffs moves where the board's mounting holes must be; changing a pin map changes what the firmware drivers address. Interface edges record those couplings explicitly, and the impact walker follows them when a head moves. This is product dependency between items. It is not the workflow-step DAG in `orchestrator/dependency_engine.py`, which orders agent steps and is unchanged (see [cross-domain impact](architecture.md#cross-domain-impact)).

#### Interface edges

An `InterfaceEdge` (`twin_core/models/interface.py`) is typed and revision-aware:

| Field | Meaning |
|-------|---------|
| `type` | `mounts_to` (a part carries a board's mounting holes), `clearance_to` (an enclosure must clear a board outline or component envelope), `connects_via` (a connector meets a cable or cut-out), `pin_maps_to` (an MCU pin or net is used by a firmware module, driver or config), `simulation_input_of` (geometry, material or load feeds a simulation), `feature_of` (a sketch or sketch plane feeds what is built from it), `manufactured_by` (a manufacturing context shaped what was generated under it), `dfm_constrains` (a DFM finding set constrains a component's geometry under a manufacturing context, see [DFM findings](#dfm-findings)) |
| `source`, `target` | each an item key pinned to the revision the coupling was stated against (`ref`, `revision`, `domain`, `item_type`, `node_id`), or a result record as `record:<uuid>` |
| `domain_pair` | the two ends' domains, from the item type (`cad_model` is mechanical, `pcb_layout` and `pinmap` electronics, `firmware_source` firmware) |
| `declared_by` | `human`, `tool` (recorded as a fact by the tool that ran) or `inferred` (MetaForge read it from data) |
| `confidence` | 1.0 for a person's edge; an inference says how sure it is |
| `status` | `proposed`, `confirmed` or `removed`. An inferred edge is `proposed` until a person confirms it; a person's edge is confirmed at once. A removed edge is kept, never deleted, and never proposed again |
| `coupled_parameters` | what crosses the interface, when known (`mounting_holes`, pin `PA5`, peripheral `SPI1`) |
| `evidence` | what an inferred or tool edge was read from (the names it matched, the files, the counts it compared) |

`mounts_to`, `clearance_to` and `connects_via` are symmetric: a change on either end reaches the other. A stored edge is an `interface_edge` work product. Three types are derived on read rather than stored twice: `simulation_input_of` from a `simulation_result`'s own revision pins, `feature_of` from the sketch layer's `DEPENDS_ON` edges (kinds `sketch_plane` and `sketch_compiler`), and `manufactured_by` from the `manufacturing_context_ref` a generated item's head records (see [a process change](#manufacturing-context)). All three are `declared_by: tool`, `confirmed` and `derived: true`. Confirming an edge re-pins both ends to their current heads; the list route reports an edge whose pinned revision is behind the head (`source_behind`, `target_behind`).

Proposals come only from data the tools already recorded (`api_gateway/twin/interface_proposals.py`). With no data behind a pair there is no edge.

| Edge | Proposed from |
|------|---------------|
| `mounts_to` | the board revision's `mounting_holes` and the enclosure revision's `mounting_standoffs` (or `mounting_holes`), the fields the cross-domain mounting check reads. Confidence grows with the holes that sit on a standoff |
| `clearance_to` | the board's `dimensions` or `board_outline` and the enclosure's `dimensions` |
| `connects_via` | the board's `connectors` and the enclosure's `cutouts` |
| `pin_maps_to` | the pin map's recorded file (JSON or CSV) against the firmware item's source files: a firmware that names a pin (`PA5`), a peripheral (`SPI1`, or its `hspi1` handle) or a net from the pin map. The matched names become `coupled_parameters`; the files are in `evidence.modules` |

An enclosure is a `cad_model` or `assembly` item whose head carries `subtype: enclosure` or one of the enclosure fields. Proposals run on `POST .../interfaces/propose` and after each head move of a board, enclosure, pin map or firmware item; `simulation_input_of` and `feature_of` need none.

#### The impact walker

`twin_core/impact/cross_domain.py` takes a change (`item_key`, old and new revision, and the changed parameters when known) and walks the edges breadth first, bounded by `max_depth` (default 4, at most 8), recording any cycle it meets. For each affected item or result it reports the domain, whether it is direct (one edge away) or indirect, every edge (coupling) that reaches it with a plain rationale ("Enclosure: mounting hole positions changed; Main board mounting depends on them"), and a proposed action. An edge whose `coupled_parameters` the change does not touch is not followed, and the item is listed in `not_affected` with the reason; with `include_unaffected`, every other item is listed too (XDM-006). Changed parameters are unknown (never narrowing) unless they can be read: a pin map's pins are diffed from its files, and otherwise metadata is compared only when the content itself did not change.

Only a confirmed edge marks results stale or proposes reruns. A proposed edge is reported as a possible impact with the action "Confirm or remove the suggested link", so an unconfirmed guess never invalidates anything.

The walk reuses what exists: [record pins](#records-pinned-to-revisions) for what a revision's results are, and `StalenessEngine.preview_impact` for constraints and engineering entities pinned through `metadata.depends_on` (reported as review actions).

#### Revision-scoped invalidation

When a head moves (a write outside a run, or a gate approving a change set), `record_pins.on_head_moved` marks records pinned to older revisions of that item `stale` as before, then runs the walk. A directly affected item's results whose check read the changed item across the interface are marked `stale` too: a firmware build or static check across `pin_maps_to`, a DRC or clearance check across `mounts_to`, `clearance_to` or `connects_via`, an FEA across `simulation_input_of`. Each keeps its own pins, and `stale_for` names the input it was valid against (`{"PIN-MAIN": {"pinned": 1, "current": 2}}`). Nothing is deleted or relabelled as a pass: an old result stays browsable as history with the revision it was for. Indirect impacts are reported, not invalidated, since the item between has not changed yet.

The outcome is recorded as a `change_impact` work product: the impact graph, the affected items by domain, the stale results (with what each said before, `previous_status`), the proposed reruns and review actions, the plain summary and next action, the approval policy it was judged under, and its operation id. It is the change transaction view of CHG-001: operation, source and target revision, targets, proposed effects, impact graph and approval policy (`transaction` in the API).

An impact is recorded only when the walk reached something: an affected item, a result made stale, a proposed rerun or a review action. A head move that reaches nothing (a sketch with no interface edges and no results pinned to it, for example) records no `change_impact` and opens no operation; it is logged as `cross_domain_impact_none` and counted as `skipped`.

A `change_impact` is history about a change, not a design work product. `GET /v1/twin/nodes` leaves it out of `nodes` and `total`, and `GET /v1/twin/relationships` does not walk it, so the twin graph and its counts show design work only; pass `include_history=true` to either to list them too. Recorded impacts are always read through `GET /v1/twin/projects/{id}/impact` and `/impact/{change_or_revision}` below, which is what the Engineering impact view uses.

#### Validation statuses {#validation-statuses}

Every check result maps onto one vocabulary (`twin_core/consistency/validation_status.py`): `PASS`, `WARN`, `FAIL`, `INCOMPLETE`, `NOT_EVALUATED` and `STALE`. The existing status fields are mapped, not replaced:

| Existing field | Mapping |
|----------------|---------|
| `GateCheckStatus` | `pass`, `fail`, `not_evaluated`: same names |
| cross-domain `CheckStatus` | `no_data` is `NOT_EVALUATED` |
| requirement matrix status | `uncertain` is `WARN`, `no_data` is `NOT_EVALUATED`, `unverified` is `INCOMPLETE`, `stale` is `STALE` |
| `ConstraintStatus` | `unevaluated` is `NOT_EVALUATED` |
| a record's `staleness` | anything but `current` or `revalidated` is `STALE`, whatever the result said |

`NOT_EVALUATED` means no result exists; `INCOMPLETE` means a result exists but carries no verdict (a `passed` flag, a `status` word, or violation and error counts). A `CheckResult` binds the check to its input revisions (`KEY@n`) and the record holding the tool's actual output; a `PASS` or `WARN` without both is downgraded to `NOT_EVALUATED`. Lack of evidence is never a pass.

#### API

| Route | Purpose |
|-------|---------|
| `GET /v1/twin/projects/{id}/interfaces` | every edge (stored and derived) and the items they join; `include_removed`, `include_derived` |
| `POST /v1/twin/projects/{id}/interfaces` | a person declares an edge (`type`, `source`, `target`, `coupled_parameters`, `rationale`); declaring a proposed link confirms it |
| `POST /v1/twin/projects/{id}/interfaces/propose` | propose edges from recorded data, optionally for one `item_key` |
| `POST /v1/twin/projects/{id}/interfaces/{edge_id}/confirm` | confirm, re-pinned to the current heads |
| `DELETE /v1/twin/projects/{id}/interfaces/{edge_id}` | remove (kept as `removed`) |
| `POST /v1/twin/projects/{id}/impact/preview` | the impact of a proposed change; writes nothing, results it would stale are `marked: false` |
| `GET /v1/twin/projects/{id}/impact` | recorded impacts, newest first |
| `GET /v1/twin/projects/{id}/impact/{change_or_revision}` | one recorded impact by id, by `KEY@n`, or the latest for a bare `KEY` |

The proposed reruns, their dispositions and approvals are described in [approvals](approvals.md#re-checks-after-a-cross-domain-change); the operation event in [operations](operations.md#cross-domain-impact).

Observability: `metaforge_cross_domain_impact_total{event, kind}` (`previewed`, `recorded`, `skipped` when a head move reached nothing, `failed`, `result_invalidated` by check kind, `rerun_proposed` by disposition, `rerun_decided`) and `metaforge_interface_edge_total{type, provenance, action}`; the `CrossDomainImpactFailing` alert; log events `cross_domain_impact_recorded`, `cross_domain_impact_none`, `cross_domain_impact_failed`, `cross_domain_impact_previewed`, `record_marked_stale_for_input`, `interface_edge_declared`, `interface_edge_proposed`, `interface_edge_confirmed`, `interface_edge_removed`, `interface_edges_proposed`, `impact_rerun_decided`; spans `twin.impact.analyse`, `twin.interfaces.*`, `twin.impact.publish` and `twin.impact_routes.*`. A failed walk after a head move is logged and counted, never raised: the head move stands.

Limits: rerun approval records the decision; the tool is then run by the agent or person, and its new result supersedes the stale one through its pins. Board and enclosure proposals read the recorded metadata fields, not the KiCad or STEP files themselves.

*Source: `twin_core/models/interface.py`, `twin_core/impact/`, `twin_core/consistency/validation_status.py`, `api_gateway/twin/interface_proposals.py`, `api_gateway/twin/impact_service.py`, `api_gateway/twin/impact_routes.py`*

---

### 2.35 Generated concept sketches {#generated-sketch}

`GENERATED_SKETCH = "generated_sketch"` is a concept sketch an image model
drew from a description, the project's references or a saved sketch (the
Create stage's **Generate**, or `twin.generate_sketch`). It is a fifth
representation beside the four in [the sketch compiler](#sketch-compiler),
with its own authority: always `illustrative`. A generated raster is never an
editable sketch, never CAD and never evidence (`editable: false`,
`representation: "generated_raster"`).

Each image is first a **proposal**: a `generated_sketch` node with no item,
`metadata.generation_status` `proposed`, then `accepted`, `rejected` or
`stale`. Accepting one makes it the next revision of the source's
`generated_sketch` item (`GSK-*`), so the item's head is the accepted concept
and earlier accepted concepts stay as earlier revisions. Accepting never
copies pixels: the revision points at the same blob. A branch is a new item
seeded with one concept; the current line is untouched. Proposals are linked
`GENERATED_FROM` the sketch revision, the accepted concept and the reference
images they were made from.

Every output records, in `metadata.provenance`:

| Field | Holds |
|---|---|
| `prompt`, `negative_prompt` | Exactly what was sent, and what it was told to avoid |
| `intent`, `constraints`, `negative`, `protected_features` | What the person asked for |
| `style`, `mode` | `industrial_sketch`, `marker_render`, `line_art` or `concept_drawing`; `text`, `reference` or `sketch` |
| `references` | Each reference's id, role, source hash, whether its image or its stated facts were sent, and why not when it was left out |
| `mask`, `locked_regions` | The region polygon in sketch units, how it was applied (`provider_mask` or `composited`), and the locked regions |
| `source_sketch`, `baseline_input` | The `SKT-*@n` revision and the accepted concept it was built from |
| `provider`, `model`, `cost_usd` | Who drew it, and the cost when the provider reported one (`cost_reported`) |
| `job_id`, `operation_id`, `target_key`, `base_revision`, `target_revision` | The job and operation that made it, the target it ran on, the accepted concept it started from, and the revision it became when accepted |
| `frame`, `size` | Image centre at the sketch origin, shorter side one sketch unit; the pixel size |

`metadata.preservation` holds the region check: the share of pixels kept
outside the mask (from the provider and after compositing), each locked
region's score, which protected features were checked by pixels, the
threshold and whether it passed. `review_required` is set when it did not.
`latency_s` is the measured time from the request to the stored proposal. A
proposal that arrived after another concept was accepted is stored with
status `stale` and `stale_reason`, as history only.

Accepting is refused (`409`) for a stale, rejected or accepted proposal, and
when the accepted concept moved since the person looked, unless the head is a
sibling variant of the same request (a switch, not a race).

**References.** The Create brief keeps a record per reference
(`brief.reference_details`, beside the ids in `brief.references`): its role
(`inspiration`, `geometry`, `functional_demo`, `specification`,
`manufacturing_rule`, `verification_evidence`, `existing_design`), its
associations (project, component, parameter, plane, interface or
requirement), whether it is `active` for the next instruction, the features
to keep unchanged, the file's `source_hash`, a version per change, the
candidate facts and the providers it was sent to. A fact carries a
`citation` (reference, region or line, source hash, excerpt), a unit, an
`uncertainty` and a `basis`: `estimated` for anything read off an image or a
non-authoritative text, `stated` only for an explicit value with a unit in a
`specification` or `manufacturing_rule`. Nothing is `measured`. Two
authoritative references stating different values for one parameter (in one
base unit per quantity) are raised as a `review_required` operation event
with both citations.

Metrics: `metaforge_sketch_generation_total{mode, outcome}` (generated,
preservation_flagged, stale, superseded, cancelled, unavailable, timeout,
failed, accepted, rejected), the histogram
`metaforge_sketch_generation_first_result_seconds{mode, provider}` and
`metaforge_reference_conflict_total`. `SketchGenerationFailing` fires when
more than three in ten generations in half an hour fail, and
`SketchGenerationPreservationFlaggedOften` (info) when most region edits in
an hour are flagged.

*Source: `api_gateway/twin/sketch_generation.py`, `api_gateway/twin/sketch_generation_routes.py`, `api_gateway/create/references.py`*

---

### 2.36 The manufacturing context {#manufacturing-context}

`MANUFACTURING_CONTEXT = "manufacturing_context"` is the one record of how a
product, or one of its components, might be made. Every concept, sketch,
image and illustrative 3D generation is made against it, even when it is
incomplete, and never against nothing in silence (DFM-001). It is a definition
item: `MFC-PROJECT` for the product, `MFC-<COMPONENT>` for a component (a
component with no record of its own reads the product's, and its first write
starts from a copy of it). Every change is the next revision, and each
revision's metadata says what changed (`changed_fields`), what the conflict
rule refused (`refused_fields`), which assumptions went stale
(`stale_fields`) and, for a process change, `process_change` (`from`, `to`).

#### Fields, authority and provenance

`twin_core/manufacturing/context.py` holds the record. Each field is a value
with an `authority` and a `provenance` (`source`, `at`, `actor`, `ref`):

| Authority | Meaning |
|-----------|---------|
| `user_stated` | a person said so: the Create brief or route, `PUT .../manufacturing-context`, or a design flow proposal's stated context |
| `decision` | a `manufacturing_route` (and its `process_ranking`) design decision recorded outside a design-flow run, or one a run's gate approved |
| `assumption` | proposed by MetaForge, or by an agent; shown as "may change", never as a constraint |
| `unknown` | not known; listed as a gap |

| Field | Holds |
|-------|-------|
| `intended_use`, `production_goal` | what it is for; `concept`, `appearance_prototype`, `functional_prototype`, `pilot` or `production` |
| `route` | how it will be made: `home`, `workshop` or `external` (Create), `in_house` or `vendor` (flows) |
| `process_candidates`, `selected_process` | candidate processes and the chosen one |
| `material_candidates`, `selected_material` | candidate materials and the chosen one |
| `quantity`, `target_cost`, `lead_time` | commercial targets; a cost is `{amount, currency, text}` and an amount needs its currency, a lead time is `{days, text}` |
| `machine_or_supplier_profile` | `{machine, machine_id, supplier, description}` as stated |
| `dimensional_envelope`, `tolerances`, `finish`, `assembly`, `fastening` | dimensional, appearance and joining requirements |
| `applicable_rules`, `evidence` | rule profile and validation provenance |
| `unknowns`, `assumed`, `confidence` | derived on every write: the gaps, the assumed fields, and `low`, `medium` or `high` |
| `dfm_status` | see below |
| `conflicts` | the last refused writes, each with what was kept and why |

Unknown process, material, machine, supplier, tolerance, quantity, target cost
and lead time are listed in `unknowns` (DFM-010). The route decides two of
them: made at home or in a workshop, no supplier is needed; made by a vendor,
no machine. `target_cost`, `lead_time` and `machine_or_supplier_profile` are
never assumed (DFM-014): the record refuses an assumed one, an assumed Create
budget is not recorded, and an agent's `PUT` of one is refused. Nothing
estimates a price, a lead time or a machine capability.

#### The conflict rule

The most recent explicit human statement or approved decision wins. An
assumption never overrides a stated value or a decision; a newer assumption
replaces an older assumption or a gap. An unknown never erases a value unless
a person clears it (a `null` in `PUT`, or a Create value that a person
removed, which clears only a field that came from Create). A refused write
is kept in `conflicts`. A write that changes nothing makes no revision.

#### One source

| Source | What it writes |
|--------|----------------|
| Create brief save | `intended_use`, `dimensional_envelope` (size and fit), `finish` (appearance), `material_candidates` (materials), `target_cost` (manufacturing budget, stated only), `quantity`, and the making context into the machine or supplier profile. A confirmed item is `user_stated`; an assumed one, or one written by an agent surface, is an assumption |
| Create route save | `route`, `selected_process`, `selected_material`, and the machine and supplier |
| Design flow proposal | a stated `manufacturingContext` is recorded as `user_stated`. A proposal without one reads the record's confirmed values (never its assumptions) as its context, so a route already chosen is not asked for again |
| `twin.record_decision` with `manufacturing_route` | outside a run: `decision`. Inside a design-flow run: an assumption with `pending_gate` set to the run, which cannot override a stated value, and becomes a `decision` when a person approves that run's gate (once per decision, so a later gate never overrides a newer statement) |

Every write is a head write, never a design-flow draft, and is reported as a
short operation in the `manufacturing` domain.

#### Proposals when nothing is stated

`POST .../manufacturing-context/propose` proposes candidate processes and
materials from the intended use, the envelope, the quantity and the product
text, each with its reason. It always proposes at least two, so no method is
locked in silently, checks them against the machine library and the
`rank_manufacturing_processes` skill, and writes nothing. To accept one, state
it.

#### Injection into generation

`manufacturing_context_block(project_id, component_key=None)` in
`api_gateway/twin/manufacturing_context.py` is the one helper every generator
calls. It returns a compact, prompt-safe block (single lines, no control
characters or backticks) with confirmed constraints, assumptions and gaps on
separate lines, the ref it used, the unresolved set and the note. Where no
process is known, proposed candidates are added to the block as assumptions,
never stored.

| Generator | How the context is used |
|-----------|-------------------------|
| `twin.generate_sketch` and the Create **Generate** | in the prompt, beside the stated values of `manufacturing_rule` references |
| `twin.render_concept_image` | in the prompt |
| `twin.generate_concept_mesh` | in a text-to-model prompt in a project, after the person's words and within the provider's limit (`prompt_sent`); metadata only for image input or a call with no project |
| `twin.render_concept_sketch` | metadata only (the renderer is deterministic) |
| the chat project brief | as a block in the brief |
| design-flow phase brains | read for each phase, beside the frozen flow context |

Every output records `manufacturing_context_ref` (`MFC-PROJECT@3`, or
`unresolved` when nothing was recorded), `manufacturing_context_unresolved`,
`manufacturing_context_applied`, `speculative`, `manufacturing_note`,
`dfm_status` and `dfm_status_label`. Each generator takes `speculative`
(DFM-004): the context is recorded but not applied, the prompt says so, and
the output is labelled "Speculative concept". Each also takes `component` to
read that component's context.

#### DFM status and the note {#dfm-status}

`dfm_status` has the seven states of DFM status semantics: `not_assessed`,
`preliminary_guidance`, `analysis_pending`, `issues_found`,
`analysis_passed` (the specified checks completed; not a guarantee of build
success), `verification_required` (physical or supplier confirmation
required) and `released`. A generated concept is `not_assessed` unless a real
heuristic check ran (then `preliminary_guidance`); it can never carry a later
state. The heuristic checks that set a component's status are in
[DFM findings](#dfm-findings). `manufacturing_note` is the short, truthful line a concept card shows:
"Manufacturing not assessed" when nothing informed it, "Informed by: FDM
(assumed), PLA (assumed) · Manufacturing not assessed" when a context did. No
illustrative label or note ever says verified or production-ready; a unit
test scans every one.

#### A process change

When `selected_process` changes, every assumption marked `process_specific`
(material candidates proposed for the old process, for example) is marked
`stale` with the reason, and kept. Stated values and decisions are never
touched. The impact walker then follows `manufactured_by`: a derived
interface edge from the context item to each generated item whose head
recorded a revision of it (an accepted `GSK-*` concept), coupled on
`selected_process` and `selected_material`. The context's changed parameters
are read field by field, so a process or material change records a
`change_impact` with the review action "Review it against the new process;
keep what the person approved", and a change to, say, the intended use
records none. A speculative concept is not walked.

#### API

| Route | Purpose |
|-------|---------|
| `GET /v1/twin/projects/{id}/manufacturing-context` | the current context (`?component=` for one component): ref, record, gaps, note and the block a generator would get; `recorded: false` and ref `unresolved` when none |
| `PUT /v1/twin/projects/{id}/manufacturing-context` | state `fields` (`null` clears one), with `expected_revision` (409 when the head moved). An agent caller (`X-MetaForge-Surface: agent`) only proposes assumptions |
| `GET /v1/twin/projects/{id}/manufacturing-context/history` | every revision with what it changed |
| `POST /v1/twin/projects/{id}/manufacturing-context/propose` | candidate processes and materials; writes nothing |

Observability: `metaforge_manufacturing_context_write_total{authority,
outcome}` (`written`, `unchanged`, `refused`, `failed`) and
`metaforge_manufacturing_context_injection_total{generator, resolution}`
(`record`, `unresolved`, `speculative`); the
`ManufacturingContextSyncFailing` alert; log events
`manufacturing_context_written`, `manufacturing_context_writes_refused`,
`manufacturing_context_injected`, `manufacturing_context_proposed`,
`manufacturing_context_sync_failed`; spans
`twin.manufacturing_context.write`, `.block`, `.propose` and
`twin.manufacturing_context_routes.*`. A sync from Create, a decision or a
flow that fails is logged and counted, never raised: the save it follows
stands.

*Source: `twin_core/manufacturing/`, `api_gateway/twin/manufacturing_context.py`, `api_gateway/twin/manufacturing_context_routes.py`*

---

### 2.37 The appearance record {#appearance-record}

How the product and each component look, and nothing else. One `appearance` item per project (`APR-PROJECT`) holds the product's look and each component's: colour (sRGB hex), finish, render material and texture refs.

- A request the gateway classifies as **appearance** ("make it red") writes only this record. It never rebuilds CAD, never writes the manufacturing context, never moves a head revision and marks nothing stale. While a turn is classified as appearance, exact CAD builds and manufacturing context writes in that turn are held with a plain reason.
- A component's look is keyed by the component's name (and remembers the node it was set on), so it outlives any one mesh: when a new L0, L1 or L3 mesh shows the same component, the viewer applies the stored look to it (SYNC-007).
- Words that could mean either the look or the material ("metallic", "chrome", "wood look") are classified as ambiguous, and the person chooses: the look updates this record, the material proposes a change to the [manufacturing context](#manufacturing-context).

Routes: `GET /v1/twin/projects/{project_id}/appearance` and `PUT` the same path; `POST /v1/twin/projects/{project_id}/classify-change` classifies a request without writing anything.

*Source: `api_gateway/twin/appearance.py`, `api_gateway/twin/change_classification.py`, `api_gateway/twin/change_classification_routes.py`, `api_gateway/chat/change_turn.py`*

---

### 2.38 DFM findings {#dfm-findings}

Cheap design for manufacture checks run during interactive refinement, long
before an exact check can. They are guidance: a finding set's status is never
an analysis pass.

#### Rule profiles

One versioned profile per process, in `twin_core/dfm/profiles/`: `fdm`,
`resin`, `cnc`, `injection_moulding` and `sheet_metal` (each `1.0.0`). A
profile is picked from the manufacturing context's process words ("3D
printing" is `fdm`, "SLA resin" is `resin`). Every rule has an id
(`fdm.min_wall`), a check, the quantity it reads, a limit (absolute, or a
factor of another quantity such as the sheet thickness), and its **kind and
source**:

| Kind | Meaning |
|------|---------|
| `machine_limit` | A capability of the selected machine, read from the machine library at evaluation time with the library's own source. A profile only names the capability (`build_volume_mm`); its number is never in the profile |
| `supplier_limit` | Only from a verified integration. No profile seeds one: the model refuses it |
| `heuristic` | A MetaForge rule of thumb (supports inside an enclosed cavity cannot be removed) |
| `general_guidance` | Widely published process design guidance, marked as such in its source |

A guidance rule may defer to the machine (`machine_override`): when the
selected machine records that capability (a minimum wall, a tool diameter),
the machine's value and source replace the guidance. The FDM profile has
minimum wall, minimum feature, overhang angle, unsupported bridge, build
volume (from the selected machine), minimum hole and support access. The
starter profiles have resin wall, detail, hole, drainage and build volume;
CNC internal corner radius, corner radius for the pocket depth, pocket depth
to width, walls by material family, hole and travel; moulding minimum and
maximum wall, wall uniformity, draft and rib thickness; sheet metal bend
radius, flange length, hole to bend distance and hole size, in multiples of
the sheet thickness.

#### Geometry the checks read

For each component (a `CMP-*` id, or `item:<KEY>` for CAD with no component)
the best geometry it has is read, and quantities the best source lacks are
filled from lower ones, each measure keeping its own confidence:

| Source | Measures | Confidence |
|--------|----------|------------|
| Exact, validated CAD | bounding box as the envelope; generation parameters (`wall_thickness`, `hole_diameter`, `draft_angle` ...) | `exact` (box), `design_parameter` |
| CAD not yet validated | the same | `design_parameter` |
| Parametric sketch | an extrude depth a person gave, as a wall | `human_confirmed` |
| Concept layout (L1) | each block's size as the envelope and its smallest side as the thinnest section | `estimated` (`human_confirmed` for a `verified` part) |
| Face tables | a face table recorded on the CAD (`dfm_faces`), else the faces of a current overhang check pinned to it | `estimated` |

Faces run the FDM overhang check (the same one `twin.evaluate_overhang_metric`
uses): undirected for a mesh face table, so flat tops are flagged too, and
directed only when the table says its normals point outward.

#### Findings and gaps

A finding has an id stable across recomputes, the rule id, the profile and
version, a severity, an **anchor** (the component, plus the feature or face
signature when known, and every face it covers), the quantity measured
against the limit with units, comparison and the measure's confidence, the
limit's source, the evidence refs it was read from, a plain message, a
suggested fix and what the fix would change. A finding is `blocking` only
when the limit is a machine (or supplier) limit and the measure is exact,
confirmed by a person or a design parameter; anything on guidance or on
estimated geometry is `advisory`.

A rule whose input is unknown is listed in `not_assessed` with the reason,
never passed: "Not assessed for minimum wall thickness because the wall
thickness is unknown", "because the machine is unknown", "because the
material is unknown" (a rule for one material family), or, with no process,
one entry "because the process is unknown".

#### Status semantics

| Status | When |
|--------|------|
| `issues_found` | any finding, blocking or advisory |
| `analysis_pending` | exact checks are scheduled and nothing was found |
| `preliminary_guidance` | only heuristics ran and nothing was found |
| `not_assessed` | no rule ran |

A finding set is never `analysis_passed`, `verification_required` or
`released`: the model refuses those states. The product's status is its
worst component's. The note is grounded in what ran: *Likely printable with
supports (preliminary)* only for FDM and only when the overhang rule ran and
found only overhangs, *No issues in 3 preliminary CNC machining checks*, *2
manufacturing suggestions for FDM (preliminary)*, *1 blocking manufacturing
issue for FDM (preliminary)*, or *Manufacturing not assessed (the process is
unknown)*.

#### The record

One `dfm_finding_set` record (a `WorkProduct`, a history type left out of
the node lists) per component and evaluation, with the set in
`metadata.dfm_finding_set`, pinned to the geometry item revisions and the
manufacturing context revision it read (`depends_on_items`, revision pin
edges), and `result.verdict: incomplete` so the shared validation status
never reads it as a pass. The same inputs reuse the current set. A recompute
under the same profile supersedes the old set; under another profile (the
process changed) the old set is marked `stale` with the reason and kept. The
CAD revision (or the L1 mesh, per component in `dfm_components`) it was
computed on gets `dfm_status`, `dfm_status_label`, `manufacturing_note` and
`dfm_finding_set_id` as status tags, only when a rule ran.

#### Incremental recompute

When a component's geometry head moves (`cad_model`, `assembly`,
`parametric_sketch`), the record pins mark its finding set stale and, after
the other head-move listeners, only the components whose sets were pinned to
that item are recomputed. A manufacturing context write that changes a field
the rules read (process, process candidates, material, material candidates,
quantity, machine or supplier, tolerances) recomputes the sets made under
that context; any other change (a finish, the intended use) revalidates the
sets its move marked stale. A process change therefore gives findings keyed
to the new profile and leaves the old process's findings stale, while every
stated or decided field and every geometry revision stays as it was.

#### In the impact graph

Each current or stale set is derived as `dfm_constrains` interface edges
(symmetric, `declared_by: tool`) from `record:<set>` to each item it is
pinned to. The edge to the manufacturing context is coupled on the fields
above, so a finish edit does not reach the findings. A geometry change lists
the set as a stale result of kind `dfm`, with the review action "Review the
recomputed manufacturing guidance"; no tool rerun is proposed, since the
recompute happens on its own.

#### Decisions

`keep_design` records the person's choice on the finding (carried to later
recomputes of the same finding; it no longer counts as a suggestion).
`try_fix` returns the fix as a proposal and changes nothing; when the fix
would change a field a person stated in the manufacturing context, or a
reference's protected feature, it files a design-change approval
(`change:<id>`) instead. `explore_process` previews the component under the
other profiles and stores nothing.

#### Generation guidance

A concept generation with `manufacturability_variant: true` makes its last
variant a proposal shaped by the selected process's profile, as guidance
text (only when the process is specified; otherwise the reason is returned).
That variant records `variant_kind: manufacturability`, `dfm_profile` and
`dfm_rules_applied` (each rule id, version, kind and source); its DFM status
stays `not_assessed`, since nothing checked the picture.

#### API

| Route | Purpose |
|-------|---------|
| `GET /v1/twin/projects/{id}/dfm` | current findings by component, product status, open suggestion count and profile versions; `?history=true` adds stale and superseded sets |
| `POST /v1/twin/projects/{id}/dfm/evaluate` | run the checks now (`component_ids` to narrow) |
| `POST /v1/twin/projects/{id}/dfm/findings/{finding_id}/decision` | `keep_design`, `try_fix` or `explore_process` |

MCP: `twin.evaluate_dfm` (additive) and `twin.get_dfm_findings` (read only).
Observability: `metaforge_dfm_evaluation_total{trigger, outcome}`,
`metaforge_dfm_finding_total{severity, process}`,
`metaforge_dfm_stale_recompute_total{reason}` and
`metaforge_dfm_decision_total{decision, outcome}`; the `DfmEvaluationErrors`
alert; spans `twin.dfm.evaluate`, `twin.dfm.evaluate_project`,
`twin.dfm.view`, `twin.dfm.decide` and `twin.dfm_routes.*`; operation events
in the `manufacturing` domain titled "Manufacturing guidance".

*Source: `twin_core/dfm/`, `api_gateway/twin/dfm_findings.py`, `api_gateway/twin/dfm_routes.py`*

---

### 2.39 Migrating legacy nodes into items {#migrating-legacy-nodes}

A project written before items existed holds unlinked copies: the live shelf project had 16 `cad_model` nodes for 3 parts and 1 assembly, 8 constraint sets and 14 phase-summary decisions. The migration folds them into items and revisions. It is a dry run first, it applies only a plan someone reviewed, and it never deletes anything.

#### Dry run, then apply

`ItemMigration(twin).plan(project_id)` (`twin_core/items/migration.py`) reads the project and returns a `MigrationPlan` without writing anything:

| Field | Holds |
|-------|-------|
| `items` | each item to create (or existing item to extend): `key`, `item_type`, `name`, `rule` (the strongest rule that formed it) and `rules`, `confidence` (`high` / `low`), `review_reasons`, `head_node_id` / `head_revision`, and `revisions` |
| `items[].revisions` | each node: `node_id`, `name`, `created_at`, `revision`, `status` (`head`, `approved` or `rejected`), `status_reason`, `run_id`, and the `rule` and `evidence` that put it in this item |
| `records` | `mark_run_summary` for a phase-summary decision; `pin` for a simulation result, with `item_ref` (`KEY@n`) and `staleness` (`current` / `stale`) |
| `low_confidence` | the groupings flagged for review |
| `skipped` | nodes left alone, with why (a run that is still open) |
| `counts_before` / `counts_after` | per type: nodes, items, unlinked nodes; records: decisions listed, run summaries, simulation results pinned and stale |
| `twin_fingerprint`, `plan_hash` | what the plan was made against, and a hash of what it would do |

`render_report(plan)` prints the same plan as a table. `ItemMigration.apply(plan)` takes the plan back: it refuses one whose content no longer matches its `plan_hash` (`PlanTamperedError`), then plans again and refuses unless the fresh plan has the same hash (`StalePlanError`), so a plan reviewed against an earlier twin is never applied to a changed one. A failure on one item is logged and reported in `failures`; the rest still apply, and a new dry run shows what is left.

#### How nodes are grouped

Per definition type (`intent`, `stakeholder_need`, `objective`, `prd`, `constraint_set`, `bom`, `assembly`, `cad_model`), strongest evidence first. A `cad_model` with `metadata.parts` is an `assembly`, and so is one with no parts whose name is assembly-like (`assembly`, `assy`, `asm`): an assembly is never a revision of a single part. A `SUPERSEDES` chain that crosses `cad_model` and `assembly` is still one item, typed by its existing item when it has one, else by its newest node.

| Rule | Groups |
|------|--------|
| `existing_item` | a node already a revision stays in its item; existing items anchor the groups below |
| `supersedes` | nodes joined by a `SUPERSEDES` edge |
| `flow_slot` | a node carrying its run and phase (`metadata.run_id` or `change_set`, and `phase`) lands on the slot key that run's flow declared for that phase ((architecture/design-flow-harness.md#deliverable-slots-carry-item-keys)) |
| `same_name` | the same normalised name: case, punctuation, dimensions (`220 x 120 x 12 mm`) and version words ignored, or a name that derives an existing item's key |
| `name_similarity` | one name's meaningful words contained in the other's (`Left PETG Gusset Bracket` and `Left Vertical Triangular PETG Gusset Bracket - 220 x 120 x 12 mm`), at least 75%, **and** a jaccard overlap of at least 0.5, so a short name merely contained in a long unrelated one (`wall_shelf_board` in `Wall Shelf Assembly - plywood board with two PETG brackets`, jaccard 0.38) is not grouped. Never across opposite side words (`left` / `right`, `top` / `bottom`, ...). See [geometry evidence](#migration-geometry-evidence) below |
| `one_per_project` | intent, constraint set, prd and bom: a project has one, so their remaining groups fold into one item (unless the project already has two or more items of that type) |
| `new_item` | anything left is its own item; its key is the slot key when it has one, else derived from the head's name |

#### Geometry evidence {#migration-geometry-evidence}

A name match between cad nodes is checked against their geometry before it groups them. The migration reads the bounding box from `metadata.bbox_mm` (or `bounding_box`, or `geometry_features.properties`) in any shape the recorders store: `[x, y, z]`, `[xmin, ymin, zmin, xmax, ymax, zmax]`, `{min_x, ..., max_x, ...}`, `{xmin, xmax, ...}`, `{x, y, z}` or `{min: [...], max: [...]}`, compared as sorted edge lengths; and the volume from `metadata.volume_mm3`.

| Evidence | Effect on a `name_similarity` match |
|----------|--------------------------------------|
| every edge within 1.35x, or volumes within 10% | `similar`: grouped |
| any edge more than 2x off (`800 x 250 x 18` vs `800 x 250 x 138`), or volumes more than 10% apart | `dissimilar`: **not grouped**, both sides flagged for review |
| edges between 1.35x and 2x | grouped and flagged for review |
| no bbox or volume on one side | names decide |

Similarity does not chain through a weak link: a member that joined its group by name similarity can bring another node in only on a full containment, so a node is compared with the group's anchor members, not carried in by a chain of partial matches. A partial match that is grouped is flagged for review; a near miss that is not grouped (geometry differs, overlap below 0.5, or reachable only through a weak link) flags both sides with the reason and the evidence.

`component_selection` (`BOMItem`) is not migrated: its nodes have no `created_at` to order revisions by, and its write path already gives new selections items.

#### Revisions, heads and rejected runs

Revisions are ordered by `created_at`. The newest node whose run was approved, or simply the newest when the run is unknown, becomes HEAD; older ones are approved history. A node from a rejected, failed, canceled or timed-out run becomes a `rejected` revision with the run's reason (`approval_reason`, else `error`) as `status_reason`, so it shows up as a lesson. A node from a run still in progress is skipped for that run's gate to settle. Run outcomes and slots come from the gateway's run store (`completed` is approved); without it every run is unknown.

Applying writes edges only on definition nodes, which are never edited: `REVISION_OF` (`revision`, `adopted: true`, `status` `approved` / `committed` / `rejected`, `run_id`, the node's `created_at`, `change_reason` = "migrated: rule (evidence)", and `migrated`, `migration` (the plan hash prefix), `migrated_at`, `migrated_by`), `HEAD`, and `SUPERSEDES` between consecutive accepted revisions where missing. An existing item that gains revisions numbers them after its `last_revision` (numbers are never reused), and its head moves only to a newer accepted node.

#### Records

* A `design_decision` titled `"<Phase> - phase summary"` (hyphen, en dash or em dash) gets `metadata.run_summary = true` and `record_kind = "run_summary"`. It stays in the twin; decision lists (the project brief, the `decisions` brief section, `GET /v1/decisions`) leave it out.
* A `simulation_result` whose analysed geometry resolves (its `analysed_geometry` pin, else a `DERIVES_FROM` / `PARENT_OF` / `VALIDATES` edge to a geometry revision) is pinned the [same](#records-pinned-to-revisions) way: `metadata.depends_on_items`, a `DEPENDS_ON` edge with `kind: revision_pin`, and `staleness` `stale` (with `staleness_reason`, `stale_for`) when that revision is not its item's head, else `current`. From then on revision pinning keeps the status up to date. A result already pinned is left alone, unless this plan moves its item's head past the pinned revision.

Idempotent: after an apply every definition node is a revision and every record has its flag or pin, so the next dry run is empty.

#### Surfaces

* `POST /v1/twin/projects/{project_id}/item-migration/plan`: the dry run, `{plan, report, empty}`. Writes nothing.
* `POST /v1/twin/projects/{project_id}/item-migration/apply` with `{plan, approve: true, reason}`: applies the plan. Without `approve: true` it is 403; a plan for another project or an altered plan is 400; a twin that changed since the plan is 409. The approver is the request's caller (`approver_from_request`, the same identity every approval records), returned as `approved_by` / `approver_verified` and logged with the reason.
* `forge twin migrate <project> [--apply]` ([CLI reference](cli-reference.md#twin-migrate)).

Observability: `item_migration_planned`, `item_migration_applied`, `item_migration_item_applied`, `item_migration_approved`, `item_migration_plan_stale` and `item_migration_item_failed` log events; the `twin.items.migration.plan` / `.apply` spans; the `metaforge_twin_item_migration_total{item_type, kind="item"|"revision", outcome}` counter (items and revisions created per type) and the `TwinItemMigrationFailures` alert.

*Source: `twin_core/items/migration.py`, `api_gateway/twin/item_migration_routes.py`, `cli/forge_cli/main.py`*

---

## 3. Edge Types

Edges are directed relationships between nodes. Each edge type has defined source and target node types.

| Edge Type | Source -> Target | Description |
|-----------|-----------------|-------------|
| `DEPENDS_ON` | WorkProduct -> WorkProduct, or record -> revision node | WorkProduct A requires WorkProduct B (e.g., PCB depends on schematic). With `metadata.kind = "revision_pin"`, a record (`simulation_result`, `design_decision`, `evidence`) is pinned to the item revision `metadata.item_ref` (section 2.31) |
| `IMPLEMENTS` | WorkProduct -> WorkProduct | WorkProduct A implements the spec defined in WorkProduct B |
| `VALIDATES` | WorkProduct -> WorkProduct | WorkProduct A (test result) validates WorkProduct B (design) |
| `CONTAINS` | WorkProduct -> WorkProduct, or HierarchyNode -> HierarchyNode | Hierarchical composition. `metadata` carries `{quantity, placement}` when nesting one product-hierarchy position inside another |
| `VERSIONED_BY` | WorkProduct -> Version | Links an work_product to the version that last modified it |
| `CONSTRAINED_BY` | WorkProduct -> Constraint | Constraint applies to this work_product |
| `PRODUCED_BY` | WorkProduct -> Agent | WorkProduct was produced or modified by this agent |
| `USES_COMPONENT` | WorkProduct -> Component | WorkProduct references this component (e.g., BOM uses resistor) |
| `PARENT_OF` | Version -> Version (also used for WorkProduct -> WorkProduct provenance, e.g. a robot_description's source cad_model parts) | Version lineage / source-artifact provenance. An assembly `cad_model` is `parent_of` each part `cad_model` it contains (`twin.commit_geometry` with `parts`); the assembly's `metadata.parts` is `[{node_id, name, material, position_bbox_mm}]` |
| `CONFLICTS_WITH` | Constraint -> Constraint | Two constraints that cannot both be satisfied |
| `REALIZED_BY` | HierarchyNode -> WorkProduct | A hierarchy position's real cad_model/robot_description geometry |
| `INSTANCE_OF` | HierarchyNode -> BOMItem, or DeviceInstance -> WorkProduct | A COTS leaf position is an instance of one canonical component record. A manufactured unit is an instance of the design revision it was built from |
| `MEASURED_BY` | DeviceInstance -> WorkProduct | A real-world measurement from this unit was recorded against an interface quantity embedded in this system_architecture WorkProduct |
| `REVISION_OF` | revision node -> Item | This node is revision `metadata.revision` of the item (section 2.30); `metadata.status` is `committed`, `draft`, `approved`, `rejected` or `abandoned` |
| `HEAD` | Item -> revision node | The item's current revision; exactly one per item |

### Typed Edge Models

Edges with domain-specific properties are modeled as typed subclasses of `EdgeBase`:

```python
class DependsOnEdge(EdgeBase):
    """WorkProduct A requires WorkProduct B."""

    edge_type: EdgeType = EdgeType.DEPENDS_ON
    dependency_type: str = "hard"  # "hard" or "soft"
    description: str = ""


class UsesComponentEdge(EdgeBase):
    """WorkProduct references a physical component."""

    edge_type: EdgeType = EdgeType.USES_COMPONENT
    reference_designator: str = ""  # e.g. "R1", "U3"
    quantity: int = 1


class ConstrainedByEdge(EdgeBase):
    """Constraint applies to an work_product."""

    edge_type: EdgeType = EdgeType.CONSTRAINED_BY
    scope: str = "local"  # "local" or "global"
    priority: int = 0
```

*Source: `twin_core/models/relationship.py`*

### SubGraph Response

```python
class SubGraph(BaseModel):
    """A traversal result containing a subset of the graph."""

    nodes: list[NodeBase] = Field(default_factory=list)
    edges: list[EdgeBase] = Field(default_factory=list)
    root_id: UUID
    depth: int
```

*Source: `twin_core/models/relationship.py`*

---

### 3.1 Graph Engine Interface

The `GraphEngine` ABC defines the core contract for all graph storage backends. It provides node CRUD, edge management, and traversal queries.

> **v0.1**: `InMemoryGraphEngine` (dict-based, for development and testing).
> **Planned**: `Neo4jGraphEngine` for production persistence.

```python
from abc import ABC, abstractmethod
from uuid import UUID
from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.relationship import SubGraph


class GraphEngine(ABC):
    """Abstract interface for Digital Twin graph storage and retrieval.

    All backends (in-memory, Neo4j) implement this contract.
    """

    # --- Node operations ---

    @abstractmethod
    async def add_node(self, node: NodeBase) -> NodeBase:
        """Add a node to the graph. Raises ValueError if ID already exists."""
        ...

    @abstractmethod
    async def get_node(self, node_id: UUID) -> NodeBase | None:
        """Retrieve a node by ID, or None if not found."""
        ...

    @abstractmethod
    async def update_node(self, node_id: UUID, updates: dict) -> NodeBase:
        """Update a node's fields. Raises KeyError if node not found."""
        ...

    @abstractmethod
    async def delete_node(self, node_id: UUID) -> bool:
        """Delete a node and all its connected edges. Returns False if not found."""
        ...

    @abstractmethod
    async def list_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict | None = None,
    ) -> list[NodeBase]:
        """List nodes, optionally filtered by type and field values."""
        ...

    # --- Edge operations ---

    @abstractmethod
    async def add_edge(self, edge: EdgeBase) -> EdgeBase:
        """Add an edge. Raises ValueError if source or target node doesn't exist."""
        ...

    @abstractmethod
    async def get_edges(
        self,
        node_id: UUID,
        direction: str = "outgoing",
        edge_type: EdgeType | None = None,
    ) -> list[EdgeBase]:
        """Get edges connected to a node. Direction: 'outgoing', 'incoming', or 'both'."""
        ...

    @abstractmethod
    async def remove_edge(
        self, source_id: UUID, target_id: UUID, edge_type: EdgeType
    ) -> bool:
        """Remove a specific edge. Returns False if not found."""
        ...

    # --- Traversal queries ---

    @abstractmethod
    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: EdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[NodeBase]:
        """Get nodes directly connected to the given node."""
        ...

    @abstractmethod
    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> SubGraph:
        """BFS traversal from root, returning all nodes/edges within depth hops."""
        ...

    @abstractmethod
    async def traverse(
        self,
        root_id: UUID,
        edge_types: list[EdgeType],
        max_depth: int = 5,
    ) -> list[list[UUID]]:
        """Find all paths from root following the given edge types, up to max_depth."""
        ...
```

*Source: `twin_core/graph_engine.py`*

---

## 4. Versioning Model

The Twin uses a Git-like branching model for the work product graph. Every mutation goes through a version, and changes can be isolated in branches before merging.

### Branch Types

| Branch | Purpose | Lifecycle |
|--------|---------|-----------|
| `main` | Canonical design state — approved work_products only | Persistent |
| `agent/<domain>/<task>` | Agent working branch for a specific task | Temporary — merged or discarded |
| `review/<id>` | Human review branch for approval workflow | Temporary — merged or discarded |

### Version Operations

```python
from abc import ABC, abstractmethod

class VersionEngine(ABC):
    """Abstract interface for Git-like versioning of the work_product graph."""

    @abstractmethod
    async def create_branch(self, name: str, from_version: UUID | None = None) -> str:
        """Create a new branch, optionally forking from a specific version.

        If from_version is None, forks from the HEAD of "main".

        Raises:
            ValueError: If branch name already exists.
            KeyError: If from_version doesn't exist.
        """
        ...

    @abstractmethod
    async def commit(
        self,
        branch: str,
        message: str,
        work_product_ids: list[UUID],
        author: str,
    ) -> Version:
        """Create a new version on the given branch.

        Captures a snapshot of all tracked work_products, overlaying changes
        from the provided work_product_ids.

        Raises:
            KeyError: If branch doesn't exist or an work_product_id is not in the graph.
        """
        ...

    @abstractmethod
    async def merge(
        self,
        source_branch: str,
        target_branch: str,
        message: str,
        author: str,
    ) -> Version:
        """Merge source_branch into target_branch.

        Uses three-way merge with common ancestor detection.

        Raises:
            KeyError: If either branch doesn't exist.
            MergeConflict: If conflicting changes are detected.
        """
        ...

    @abstractmethod
    async def diff(self, version_a: UUID, version_b: UUID) -> VersionDiff:
        """Compute the diff between two versions.

        Raises:
            KeyError: If either version doesn't exist.
        """
        ...

    @abstractmethod
    async def log(self, branch: str, limit: int = 50) -> list[Version]:
        """Return commit history for a branch, newest first.

        Raises:
            KeyError: If branch doesn't exist.
        """
        ...

    @abstractmethod
    async def get_head(self, branch: str) -> Version:
        """Get the HEAD version of a branch.

        Raises:
            KeyError: If branch doesn't exist or has no commits.
        """
        ...
```

*Source: `twin_core/versioning/branch.py`*

### Version Diff

```python
class WorkProductChange(BaseModel):
    """A single work_product change between two versions."""

    work_product_id: UUID
    change_type: str  # "added", "modified", "deleted"
    old_content_hash: str | None = None
    new_content_hash: str | None = None

class VersionDiff(BaseModel):
    """The diff between two versions."""

    version_a: UUID
    version_b: UUID
    changes: list[WorkProductChange]
    constraints_added: list[UUID] = Field(default_factory=list)
    constraints_removed: list[UUID] = Field(default_factory=list)
```

*Source: `twin_core/models/version.py`*

### Three-Way Merge Algorithm

The merge implementation follows Git's three-way merge strategy:

1. **Common ancestor detection**: `_find_common_ancestor()` uses interleaved BFS from both branch HEADs, walking `parent_id` and `merge_parent_id` links, to find the nearest shared commit.

2. **Conflict detection**: `detect_conflicts()` compares source and target snapshots against the ancestor:
   - **Content conflict**: Both branches modified the same work product with different content hashes.
   - **Structural conflict**: One branch deleted a work product that the other branch modified (or added differently).
   - **No conflict**: If only one side changed, or both sides made identical changes.

3. **Merge execution**: `perform_merge()` starts from the target snapshot and applies non-conflicting source changes. If any conflicts exist, it raises `MergeConflict`.

```python
class ConflictDetail(BaseModel):
    """Description of a single merge conflict."""

    work_product_id: UUID
    conflict_type: str  # "content" or "structural"
    source_hash: str | None = None
    target_hash: str | None = None


class MergeConflict(Exception):
    """Raised when a three-way merge encounters unresolvable conflicts."""

    def __init__(self, conflicts: list[ConflictDetail]) -> None:
        self.conflicts = conflicts
        ids = ", ".join(str(c.work_product_id)[:8] for c in conflicts)
        super().__init__(f"Merge conflicts on work_products: {ids}")
```

*Source: `twin_core/versioning/merge.py`*

Conflicts must be resolved manually (by a human or an agent with explicit instructions). Auto-merge is only performed for non-conflicting changes.

### Two VersionEngine backends: graph for semantics, git for working history

`InMemoryVersionEngine` above reimplements a commit DAG, branch pointers, and three-way merge inside the graph — and since it only ever tracks opaque `content_hash` strings, it can never say more than "this work product's content changed." For CAD work products in particular, that means it can never show what changed.

`GitVersionEngine` (`twin_core/versioning/git_backend.py`) implements the *same* `VersionEngine` protocol but delegates `create_branch`/`commit`/`merge`/`diff`/`log` to a real git repository (via `subprocess` — no GitPython, no new dependency). The split in responsibility is deliberate:

- **The graph stores semantics.** Structured, queryable properties — extracted geometric parameters (e.g. `pad_length_mm`, `hole_diameter_mm`) and derived properties (volume, bounding box, mass properties) — live directly on a `WorkProduct` node's `metadata["geometry_features"]`, populated at commit time (see `api_gateway/twin/geometry_recorder.py`). This is what `twin_query_cypher` and the constraint engine reason over.
- **Git owns the working, diffable history.** When a CAD work product was authored from a real CadQuery/FreeCAD generation script, that script is committed as its own `CAD_SOURCE_SCRIPT` work product, git-versioned as the actual source of truth, and linked to the resulting `CAD_MODEL` node via a `PARENT_OF` provenance edge — the same edge type `boolean_ops.py` already uses for derivation lineage. `git diff`/`git merge` on that script produce genuine text diffs and real three-way merges (non-overlapping edits auto-resolve) instead of hash comparisons. Imported/vendor geometry with no generation script has no script to version — it stays content-hash-only, same as before.

Selection is per-project: `GitRepoRegistry` (`api_gateway/twin/git_repo_registry.py`) lazily constructs one `GitVersionEngine` per `project_id`, rooted under `METAFORGE_VERSION_GIT_ROOT/projects/<project_id>/`, so independent projects never share git history. `InMemoryVersionEngine` remains the default for graph-only/ephemeral use (dev, tests); a project only gets a real git repo when `METAFORGE_VERSION_GIT_ROOT` is configured.

> **Known gap**: MetaForge projects don't yet have a dedicated filesystem workspace (a project is still Postgres metadata + a `project_id` graph attribute) — `GitRepoRegistry` partitions by `project_id` under one configured root as an interim measure, not a true per-project workspace directory.

A commit's `paths` argument gives a work product a *stable* location (e.g. `mechanical/cad_src/bracket.py`, derived from the part's name) rather than the default `work_products/<id>`. This matters because `geometry_recorder.py` gives every regeneration a fresh graph node id — without a stable path, each regeneration would write to a brand-new git path instead of evolving one file, and `git log`/`git diff` on "this part's history" would only ever show a single commit. `geometry_recorder.py` always passes a name-derived path for this reason.

### Regenerating geometry: parameter view/edit → propose → apply

Closing the loop from "a parameter is stored in the graph" to "a human can change it and see it take effect":

- `GET /v1/twin/nodes/{id}` returns `geometryParameters` (the node's `geometry_features`, unflattened — kept separate from the generic scalar-only `properties` map) and `hasScript` (whether a git-versioned generation script backs this node).
- `GET /v1/twin/nodes/{id}/script` returns the node's current script text, read live from its project's git repo — used to seed an edit form with what's there today rather than asking a human to retype it.
- `POST /v1/assistant/proposals` lets a human create a `DesignChangeProposal` directly (previously only an agent's `twin.propose_change` MCP call could) — e.g. `{"diff": {"action": "regenerate_geometry", "script_source": "<edited script>", "parameters": {...}, "cad_tool": "cadquery"}}`.
- On approval, the apply executor's `regenerate_geometry` action (`api_gateway/twin/regenerate_geometry.py`) is no longer a no-op, and supports **both** CAD scripting tools this codebase generates from — they work fundamentally differently, so `diff.cad_tool` states which one `script_source` is written in (never auto-detected):
  - `"cadquery"` (default): one self-contained call — `cadquery.execute_script` writes a STEP file to the shared `ADAPTER_WORKSPACE_DIR` volume the gateway and adapter containers share (mirrors `boolean_ops.py`).
  - `"freecad"`: a stateful session lifecycle — `open_session` → `execute_code` (runs against that session's live document, returns an `obj_id`, not a file) → `export_model` (session_id + obj_id → `step_base64` returned directly) → `close_session`. FreeCAD is in fact the more commonly used tool in this codebase (`geometry_recorder.py`'s own default is `source_tool="freecad.export_model"`), so this path matters more than the CadQuery one it shipped alongside first.

  Either way, the result commits through the same `geometry_recorder` path — so the new node gets git-versioned script history, `geometry_features` metadata, *and* a `SUPERSEDES` link to the part it replaces (below).

### Regeneration history in the graph: SUPERSEDES chains

Every `commit_geometry` call — not just ones going through the apply-on-approve path — links successive generations of the *same named part* (matched by `project_id` + `name`) via a `SUPERSEDES` edge (new → old), mirroring `TwinAPI.ingest_datasheet`'s existing pattern for datasheet revisions. This applies to both the `CAD_MODEL` node and its `CAD_SOURCE_SCRIPT` node independently. Without `project_id`, there's no reliable identity to match on, so unscoped commits are never linked.

### Editing a parametric feature's own parameters (gap G-D2)

The `regenerate_geometry`/`script_source` flow above is one real editing path -- edit a *script*, propose, apply. The feature library (§2.19-adjacent, `generate_parametric_feature`) generates from *typed macro parameters* instead of a script, so "edit" means something more specific there: call `generate_parametric_feature` again with the SAME `name` and a CHANGED parameter value. `generate_cad_ir` (the skill every feature ultimately lowers through) gained a `parameters: dict[str, Any] | None` field, threaded straight into `twin.commit_geometry`'s own `parameters` argument (real, already part of `geometry_recorder.py`'s signature unused by any caller before this ticket) -- landing in the new node's `metadata.geometry_features.parameters`, alongside a `feature_type` key so a later caller knows which macro produced it. The existing content-hash dedup + same-name `SUPERSEDES` matching documented just above does the rest: no new versioning mechanism, no ECT.

`GET /v1/features/{work_product_id}/diff` (`api_gateway/features/routes.py`) walks that one `SUPERSEDES` edge back and diffs the two nodes' `geometry_features.parameters`, reusing `api_gateway.twin.version_schemas.FieldDelta` for the diff shape (the same one `VersionService.diff` already uses for per-node `_revisions` diffs -- a different, per-node mechanism, not conflated with this cross-node one) rather than inventing a second. A node with no prior version -- the common case, since most work products are generated once -- answers `200` with `hasPreviousVersion: false` and an empty diff rather than a `404`, matching `/v1/twin/nodes/{id}/versions`, which reports an empty history the same way; the dashboard asks for this on every node selection, so the ordinary answer must not read as an error. A `404` now means only that the node itself is unknown. The dashboard's Twin Viewer "History" tab renders it (`FeatureVersionSection`, `dashboard/src/pages/TwinViewerPage.tsx`) right alongside the existing per-node revision history, when one exists.

**Deliberately out of scope**: widening `ControlledEntityKind` (`twin_core/transactions/patch.py`) to cover `work_product` so an edit could go through a real ECT proposal/approval cycle instead of an immediate re-commit -- the same nontrivial, separate transaction-engine widening already deferred in section 2.17; a "propose, then apply" gate for macro-parameter edits (unlike script edits) would need it. Reviving `twin_core.versioning`'s branch/git `VersionEngine` for this -- confirmed zero consumers outside its own tests, a much larger lift than this ticket's real, minimal gap needed.

### Geometry diff across a SUPERSEDES chain (gap G-J2)

`GET /v1/twin/nodes/{id}/diff` (`VersionService.diff`, above) diffs a work product's own `_revisions` -- but `/nodes/{id}/iterate` only ever merges `metadata_updates` into existing metadata, it never re-uploads a blob or changes `metadata["minio_object_key"]`. So no two *revisions* of the same node ever have different geometry; that mechanism is metadata-only by construction.

`GET /v1/twin/nodes/{id}/geometry-diff` (`api_gateway/twin/geometry_diff.py`) is the real "geometry changed" case: it walks the same `SUPERSEDES` edge the feature-parameter diff above already walks, resolves BOTH nodes' actual committed STEP blobs (`digital_twin.storage.work_product_blobs.resolve_work_product_blob` -- safe here since each `SUPERSEDES`-linked node has its own independent object key, unlike a same-node revision), and calls the real `freecad.describe_step_file` on each. When a STEP export includes both named leaf parts and a top-level assembly compound (their volumes overlapping), the component with the largest volume is taken as "the whole part" -- the same ambiguity `describe_step_file`'s own docstring already documents. Returns a `volume_mm3`/`area_mm2`/`bounding_box` delta; with no prior version it returns `200` with `has_previous_version: false` and the measured fields unset (same reasoning as the parameter diff above), 404s only if the node is unknown or a `SUPERSEDES` edge names a predecessor that no longer exists, and 400s if either side isn't a STEP file. No MCP tool registration (REST-only, like `manufacture_release`), so it carries none of `bootstrap_tool_registry`'s signature-drift risk.

**Deliberately out of scope**: a true volumetric/mesh boolean diff visualization (a 3D "added/removed volume" highlight) -- no boolean-diff utility exists anywhere in this codebase (confirmed independently during scoping), the same class of gap as the other deferred 3D-result visualizations; a numeric delta is shipped instead.

---

### Engineering values and their sources

A `design_value` `EngineeringEntity` is one engineering value (a table
height, a tube section, a top thickness) together with where it came from.
It is a definition (item key prefix `VAL`), so a changed value or a better
source is a new revision of the same item.

| `metadata` key | Meaning |
|---|---|
| `name` | The value's name, e.g. `table_height` (required) |
| `value` | The number or text (required) |
| `unit` | Optional; must be a unit `twin_core.models.quantity` recognises |
| `source` | `{kind, ...}`, checked by `twin_core.provenance` (required) |
| `source_summary` | One line written by the recorder, e.g. `standard: EN 12521:2015, clause 5.3` |
| `needs_review` | `true` when the source kind is `assumption` |

Source kinds, strongest first, and the fields each needs:

| `kind` | Needs |
|---|---|
| `user` | nothing more (the record says who) |
| `project_document` | `document` (plus `locator`) |
| `standard` | `standard` (plus `clause`, `url`) |
| `past_work` | `node_id`, or `project_id` + `ref` |
| `component` | `mpn` or `stock` |
| `analysis` | `evidence`, or `method` + `inputs` |
| `measurement` | `artefact` |
| `assumption` | `rationale` |

The recorder refuses a `design_value` without `name`, `value` and a valid
`source`, and checks the `source` of any other entity that carries one. A
requirement's limit records its source the same way: each
`twin.record_constraint_set` entry takes an optional `limit_source`, kept in
the Constraint's `metadata.limit_source` with a `limit_source_summary`.

## 5. Constraint Engine

The Constraint Engine evaluates rules against the current graph state. It runs automatically on every proposed commit.

### Constraint Engine Interface

```python
from abc import ABC, abstractmethod
from uuid import UUID
from twin_core.constraint_engine.models import ConstraintEvaluationResult
from twin_core.models.constraint import Constraint


class ConstraintEngine(ABC):
    """Abstract interface for constraint evaluation against the Digital Twin graph."""

    @abstractmethod
    async def evaluate(
        self, work_product_ids: list[UUID]
    ) -> ConstraintEvaluationResult:
        """Evaluate constraints relevant to the given work_products.

        Returns a result indicating whether all ERROR-severity constraints pass.
        """
        ...

    @abstractmethod
    async def evaluate_all(self) -> ConstraintEvaluationResult:
        """Evaluate every constraint in the graph."""
        ...

    @abstractmethod
    async def add_constraint(
        self, constraint: Constraint, work_product_ids: list[UUID]
    ) -> Constraint:
        """Register a constraint and create CONSTRAINED_BY edges to the given work_products."""
        ...

    @abstractmethod
    async def get_constraint(self, constraint_id: UUID) -> Constraint | None:
        """Retrieve a constraint by ID, or None if not found."""
        ...

    @abstractmethod
    async def remove_constraint(self, constraint_id: UUID) -> bool:
        """Delete a constraint node and all its edges. Returns False if not found."""
        ...
```

> **v0.1**: `InMemoryConstraintEngine` backed by a `GraphEngine` instance.

*Source: `twin_core/constraint_engine/validator.py`*

### Constraint Language

Constraints are expressed as Python expressions evaluated against a context object. The expression must return a boolean (`True` = pass, `False` = fail).

```python
# Example constraint expressions:

# Voltage rail must not exceed 3.3V
"ctx.work_product('power_budget').metadata.get('max_voltage', 0) <= 3.3"

# BOM cost must stay under $50
"ctx.work_product('bom').metadata.get('total_cost', 0) < 50.0"

# All components must be ACTIVE lifecycle
"all(c.lifecycle == 'active' for c in ctx.components())"

# PCB must have DRC passing
"ctx.work_product('pcb_layout').metadata.get('drc_status') == 'pass'"
```

### Safe Builtins Whitelist

Constraint expressions run in a restricted `eval()` environment. Only the following 25 builtins are available — no `__import__`, `open`, `exec`, `eval`, or `compile`:

| Category | Functions |
|----------|-----------|
| Aggregation | `all`, `any`, `len`, `min`, `max`, `sum`, `abs`, `round` |
| Iteration | `sorted`, `enumerate`, `zip`, `map`, `filter` |
| Type checking | `isinstance` |
| Type constructors | `str`, `int`, `float`, `bool`, `list`, `dict`, `set`, `tuple` |
| Constants | `True`, `False`, `None` |

*Source: `twin_core/constraint_engine/validator.py` (`_SAFE_BUILTINS` dict)*

### Constraint Evaluation Context

The `ConstraintContext` is a **plain class** (not a Pydantic `BaseModel`) that provides a synchronous, read-only view of the graph state. It is pre-loaded asynchronously by `build_context()` so that `eval()` never needs to `await`.

```python
class ConstraintContext:
    """Synchronous read-only snapshot of graph state, exposed as ``ctx`` in expressions."""

    def __init__(
        self,
        artifacts_by_name: dict[str, WorkProduct],
        artifacts_by_id: dict[UUID, WorkProduct],
        all_components: list[Component],
        dependency_map: dict[UUID, list[UUID]],
    ) -> None: ...

    def work_product(self, name: str) -> WorkProduct:
        """Lookup an work_product by name. Raises KeyError if not found."""
        ...

    def work_products(
        self,
        domain: str | None = None,
        type: str | None = None,
    ) -> list[WorkProduct]:
        """Return work_products, optionally filtered by domain and/or type."""
        ...

    def components(self) -> list[Component]:
        """Return all components in the graph."""
        ...

    def dependents(self, work_product_id: UUID) -> list[WorkProduct]:
        """Return work_products that have incoming DEPENDS_ON edges to work_product_id."""
        ...


async def build_context(graph: GraphEngine) -> ConstraintContext:
    """Async factory that pre-loads graph state into a synchronous ConstraintContext.

    Loads all work_products (indexed by name and ID), all components, and builds
    a dependency map by following incoming DEPENDS_ON edges.
    """
    ...
```

*Source: `twin_core/constraint_engine/context.py`*

### Constraint Resolver

The resolver module handles two-phase constraint discovery:

```python
async def resolve_constraints(
    graph: GraphEngine,
    work_product_ids: list[UUID],
) -> list[Constraint]:
    """Two-phase constraint resolution.

    1. Follow outgoing CONSTRAINED_BY edges from each work_product to find direct constraints.
    2. Include all cross_domain=True constraints from the graph.
    3. Deduplicate by constraint ID.
    """
    ...


async def find_constrained_work_products(
    graph: GraphEngine,
    constraint_id: UUID,
) -> list[UUID]:
    """Reverse lookup: find which work_products a constraint applies to.

    Follows incoming CONSTRAINED_BY edges to the constraint node.
    """
    ...
```

*Source: `twin_core/constraint_engine/resolver.py`*

### Evaluation Lifecycle

```
Proposed commit arrives
        |
        v
  Load all constraints linked to modified work_products
  (resolve_constraints: direct CONSTRAINED_BY edges + cross_domain constraints)
        |
        v
  Build ConstraintContext (async pre-load of graph state)
        |
        v
  Evaluate each constraint expression against ctx (restricted eval)
        |
        v
  Collect results: PASS / FAIL / WARN / SKIPPED
        |
        v
  Any ERROR-severity FAIL?
   |-- Yes -> Block commit, return violations
   +-- No  -> Allow commit (warnings logged)
```

### Constraint Evaluation Result

```python
class ConstraintViolation(BaseModel):
    constraint_id: UUID
    constraint_name: str
    severity: ConstraintSeverity
    message: str
    work_product_ids: list[UUID] = Field(default_factory=list)
    expression: str
    evaluated_at: datetime

class ConstraintEvaluationResult(BaseModel):
    passed: bool  # False if any ERROR-severity constraint fails
    violations: list[ConstraintViolation] = Field(default_factory=list)
    warnings: list[ConstraintViolation] = Field(default_factory=list)
    evaluated_count: int = 0
    skipped_count: int = 0
    duration_ms: float = 0.0
```

*Source: `twin_core/constraint_engine/models.py`*

---

### Exact CAD, validity and STEP

The fast preview is drawn on the client from the design IR and never reaches
the twin as engineering geometry. Exact geometry is built only on an explicit
commit or on demand, never per pointer event, and only a validated exact
revision becomes the item's head. Code: `twin_core/geometry_state.py` (the
state model and the gate readiness), `api_gateway/twin/exact_cad.py` (the
service), `api_gateway/twin/exact_cad_routes.py` and
`tool_registry/tools/cadquery/validity.py` (the adapter checks).

**Geometry state.** Every geometry artifact has one of eight states. A CAD
revision carries its state in `metadata.geometry_state`, and node responses
(`GET /v1/twin/nodes`, `GET /v1/twin/nodes/{id}`) return it as
`geometryState: {state, badge, authority, itemKey, sourceRevision}`.

| State | Badge | Meaning |
|-------|-------|---------|
| `concept` | Concept | Illustrative (a render, image, sketch, or anything labelled `authority: illustrative`). Never validated or released. |
| `preview` | Preview | A client procedural preview; never stored as engineering geometry. |
| `exact_ready` | Exact | A STEP exists but no validity check has passed on it. Every `cad_model` committed without a state reads so. |
| `validation_pending` | Exact | An exact build or check is running. |
| `validated` | Validated | The exact B-Rep passed the validity check. |
| `failed` | Exact | An attempt failed. Only an attempt is ever failed; the head keeps the last valid revision. |
| `stale` | Exact | Derived from a revision that is no longer the head (an older STEP export). |
| `released` | Validated | Set only by the release path, when a release package is created with G8 passed. |

`check_transition` enforces the rules: `released` only through the release
path and only from `validated`; `validated` only with a `pass` verdict;
concept and illustrative artifacts never exact, validated or released.

**Exact build** (`twin.build_exact_cad`, `POST /v1/twin/items/{key}/exact-build`
with `project_id`, optional `base_revision`, and `design_ir` or `script`):

1. The design IR is lowered by the CadQuery Lowering Pass (or the CadQuery
   script runs) in the CadQuery adapter, through MCP. The adapter writes the
   result's STEP with its solids unwrapped: every compound is opened down to
   the shapes it holds, one solid is written directly and several as a
   compound of solids, so a shelled or filleted enclosure reads back as a
   solid. A bare face or an open shell is written as what it is and still
   fails the check below.
2. `cadquery.validate_shape` checks the B-Rep: OCCT `BRepCheck_Analyzer`
   validity, at least one solid where one is expected, every solid closed,
   every solid's volume above zero, and no free edges on a solid. The
   verdict is `pass`, `warn`, `fail` or `not_evaluated`; a check that could
   not run is never a pass.
3. Only a `pass` commits the result as the item's next revision, pinned to
   `KEY@base`, born with `geometry_state: validated`, `validity` (outcome,
   findings, measures, the base revision checked) and `exact_source` (the IR
   or script it was built from). A pinned commit whose base moved is refused
   by the twin and the job ends `stale`.
4. A `fail` or `not_evaluated` appends the attempt to
   `metadata.exact_attempts` (the last five, with state, outcome, findings,
   content hash and job) on the revision it started from, and the job fails
   with a retry action. A `warn` is recorded the same way and not validated.

With neither `design_ir` nor `script`, the current head is validated in
place (its own STEP is checked and, on a pass, its state becomes
`validated`). A head already validated answers at once with no adapter work.

From a parametric sketch: `item_key` may name an approved
`parametric_sketch` (`PSK-*`). Its chosen feature (`feature_id`, default the
first) is turned into a design IR (`create_body`, the sketch on its
confirmed principal plane, then `pad` by depth or `revolve` by angle) and
built into the item `CAD-<name>`, linked `generated_from` the sketch. Every
`required_input` of that feature (depth or angle, and material) must be
`provided` on the plan or supplied by the person in `inputs`; a missing one
is refused at once (422 on the route) and nothing is assumed. The sketch
must be approved and in millimetres.

**One shared definition.** The design IR built for a parametric sketch's
feature is the definition both the exact build and the browser's L2 preview
read (`twin_core/design_ir/feature_definition.py`): `feature_entities`
makes the entities, `definition_from_entities` reads them back as the
preview definition, and `definition_hash` is the SHA-256 of the canonical
entities (key order and float noise ignored). The exact build stores the
hash in `exact_source.definition_hash`. See
[L2 preview, comparison and parameter commits](#l2-preview).

**Incremental builds.** What is incremental and what is a full rebuild:

- **Skipped:** a build of a parametric sketch whose definition hash equals
  the one stored on the CAD item's validated head runs nothing: no job is
  started (the reply is `skipped: true` with the head as its result), and a
  job that was already queued returns the head with `skipped:
  "definition_unchanged"`. Material is not part of the design IR, so a
  material-only change does not rebuild geometry.
- **Per component:** each parametric sketch builds its own CAD item
  (`PSK-X` into `CAD-X`), so a change to one component schedules one build
  of that item only; the other components' heads, STEP exports and checks are
  untouched.
- **Full rebuild of the changed component:** within one component the
  CadQuery path rebuilds the whole solid from its design IR. There is no
  per-feature B-Rep cache inside a component: CadQuery has no persistent
  feature tree to replay from, and one parametric sketch is one feature
  today.

Counted in `metaforge_exact_incremental_total{outcome}` (`skipped`,
`rebuilt`).

**STEP export** (`twin.export_cad_step`, `POST /v1/twin/nodes/{id}/exports/step`
with optional `critical`): only from a `validated` or `released` revision
(409 otherwise). `cadquery.export_step` writes the STEP with the adapter's
configured schema (`CADQUERY_STEP_SCHEMA`: `AP214` by default, or `AP242`)
and the schema recorded is the one the written file's header declares. The
file is re-imported with `cadquery.validate_shape` and compared with the
shape before export: same solid count, volume within a relative 1e-3, and
bounding box corners within 0.01 mm or 1e-4 of the diagonal. The export is
kept on its revision as `metadata.step_export`: `state` (`queued`, `ready`,
`failed`, `stale`), `item_ref`, `content_hash` of the revision's geometry,
`file_sha256`, `filename`, `object_key`, `schema`, `schema_requested`,
`round_trip` (outcome, findings, both sets of measures, the tolerances) and
`critical`. A repeat request for the same revision and geometry returns it
with no adapter work. A critical export (a gate or release) fails unless the
round trip passes. When the item's head moves, every ready export of an
older revision is marked `stale` (on the revision, so it stays downloadable
from `GET /v1/twin/nodes/{id}/exports/step/file`).

The file is stored in the blob store, and every STEP the service handles is
also kept in a local cache by content hash (`METAFORGE_CAD_CACHE_DIR`,
default `~/.metaforge/cad_cache`) for when the blob store is unavailable.

`GET /v1/twin/nodes/{id}/exports` returns one revision's view: its geometry
state and badge, whether it is the head, its validity, its latest attempt,
the last validated revision of the item, whether a STEP can be exported, and
the GLB, native CAD and STEP states (`READY`, `STALE`, `QUEUED`, `FAILED`,
or `NONE` when not made).

**Gates.** G8 (`evaluate_g8_release`) adds the check `exact_cad_validated`
when the project has CAD items (`cad_model` and `assembly` items, analysis
models left out): each head must be validated, with a ready STEP of that very
revision whose round trip passed. Otherwise the check is `incomplete`, with
the reason per item, and the gate is `not_ready`. The design-flow gate of a
G8 phase lists the same reasons as missing. A project with no CAD items gets
no such check. Creating a release package marks each validated CAD head
`released`.

**Observability.** `metaforge_exact_cad_build_total{outcome}`,
`metaforge_geometry_validity_total{outcome}`,
`metaforge_step_export_total{event}` (`cache_hit`, `cache_miss`, `ready`,
`failed`, `stale`), `metaforge_step_round_trip_total{outcome}` and
`metaforge_cad_artifact_duration_seconds{stage}` (`exact_build`, `validate`,
`exact_total`, `step_export`, `round_trip`). Alerts
`GeometryValidityFailureSpike` and `StepRoundTripFailing`. Spans
`twin.exact_cad.build`, `twin.exact_cad.export_step`,
`cadquery.validate_shape`, `cadquery.export_step` and
`twin.geometry.readiness`. Operation events: see
[Operations](operations.md#exact-cad-and-step-events).

Measured on one workstation with a warm kernel (CadQuery 2.x, OCCT): a
simple exact build through `cadquery.execute_script` about 60 ms, a
validity check 30 to 110 ms, and a STEP export about 80 ms for a small
part.

### L2 preview, comparison and parameter commits {#l2-preview}

The L2 preview is drawn in the browser (`dashboard/src/lib/preview/`) from
the shared definition above; it is never stored as engineering geometry.
Code: `api_gateway/twin/parametric_preview.py` (the definition view and the
commit), `api_gateway/twin/preview_comparison.py` (the comparison) and
`api_gateway/twin/parametric_preview_routes.py`.

**The preview definition.** `GET /v1/twin/projects/{project_id}/parametric-sketches/{sketch_key}/preview-definition`
(optional `feature_id`, and `job_id` to read a build's state) returns:

- `op` (`extrude` or `revolve`), `elements` (the profile's elements on the
  plane, `v` up), `frame` (`origin`, `x_dir`, `y_dir`, `normal` in world
  millimetres, using CadQuery's own named-plane conventions, so the preview
  sits where the B-Rep will), `depth` or `angle`, `scale_to_mm` and
  `definition_hash`;
- `parameters`, each with `value`, `status` and `assumed`. A parameter
  nobody gave yet is drawn at a labelled assumed value (a tenth of the
  sketch's extent for a depth, a whole turn for an angle); the definition is
  then not `buildable` and has no hash, and nothing assumed reaches the exact
  path. `unit` and `scale_assumed` say whether the scale is confirmed;
  `plane_assumed` and `plane_blocked` say whether the preview is on the
  sketch's real plane or on XY;
- `dimensions` (the confirmed radius dimensions of circles a handle can
  edit) and `handles`;
- `representation`: always `L2` / `preview`, with the sketch revision as
  `source_revision` (`preview_descriptor` in `twin_core/representation.py`);
- `cad`: the CAD item it builds into, its head, geometry state and
  `definition_hash`, `matches_definition`, the stored `comparison`, the
  latest attempt and the last validated revision;
- `build`: given `job_id`, that exact build job's `status` and error.

The preview supports extrude (outer loop with inner loops as through-holes,
islands as extra shapes; lines, arcs, circles, ellipses, rectangles and
splines), revolve about the plane's `v` axis (caps on a partial turn) and an
empty-selector shell (approximate: a cavity inset by the thickness). Arcs and
circles are drawn with 6 degree segments and splines as Catmull-Rom with 8
segments per span, on both sides.

**Parameter commits (Strategy A).**
`POST /v1/twin/projects/{project_id}/parametric-sketches/{sketch_key}/parameters`
with `base_revision` (the sketch revision the browser edited), optional
`feature_id`, `values` (`depth` or `angle`, and `material`), `dimensions`
(`{constraint_id: radius}` for an accepted radius dimension of a circle),
`source` (`drag` or `numeric`), and optionally `preview_ms` (the browser's
rebuild time) and `comparison_tolerance`. It is the only way a manipulation
becomes authoritative: the mesh the person dragged is never stored. The edit
is classified `geometric` by the person's own action (stage `person`),
re-checked against every accepted rule, and written as the sketch's next
revision pinned to `base_revision` (a moved head answers `409`). The new
revision keeps the sketch's approval and records `parameter_edit` (base,
values, dimensions, source) and `change_classification`. Then exactly one
exact build of the sketch's CAD item is scheduled through
`twin.build_exact_cad` with the CAD head as its base; a result that loses a
race to a newer head ends `stale` and the newer head is kept. The reply
gives the new `sketch_ref` and `build` (`job_id` and `status`; or
`needs_input`, `blocked` or `refused` with the reason; or `skipped` when the
definition is unchanged). A value that is not a positive number, an angle
over 360, a parameter the feature does not have or a dimension that is not
a circle's radius answers `422`; an unapproved sketch or a broken rule
answers `409`.

**The comparison** (spec section 7). When an exact build of a supported
design IR commits a validated revision, the L2 preview of the same
definition is rebuilt in Python with the browser's tessellation rules and
compared with the exact result. It is stored on that revision as
`metadata.preview_comparison`:

| Field | Meaning |
|---|---|
| `outcome` | `consistent` (every check ran and is within tolerance), `discrepancy` (a check exceeded it) or `incomplete` (none exceeded it, at least one could not run) |
| `checks` | `bounding_box` (largest corner difference), `dimensions` (sorted extents), `volume` (relative difference against `cadquery.validate_shape`), `surface_distance` (sampled bidirectional distance: largest and mean, preview to exact and exact to preview, over 96 points per surface), `features` (intended through-holes against the genus of the welded exact tessellation); each with `outcome` `pass`, `fail` or `not_evaluated`, and its values |
| `discrepancies` | The checks that failed |
| `tolerance` | `abs_mm`, `rel`, `volume_rel`, the `linear_mm` applied, `basis` and `source` (`default` or `component`) |
| `preview` | `op`, `definition_hash`, `bounding_box`, `volume_mm3`, `intended_through_holes`, `sketch_ref` |
| `item_ref`, `method`, `compared_at` | The exact revision, how it was measured, when |

There is no universal threshold. The default tolerance is the larger of
0.25 mm and 1% of the bounding-box diagonal (2% for volume): it covers the
preview's chord error on arcs (under 0.14% of the radius) and the difference
between Catmull-Rom and the kernel's interpolating B-splines, and it says
whether the preview showed the exact part faithfully, not whether the part
can be made. A component sets its own with `comparison_tolerance` on a
parameter commit, kept on its parametric sketch. A discrepancy is reported
with its values; it never fails the build, unvalidates the revision or
blocks a gate. The two mesh checks need the exact revision's OCCT
tessellation (the GLB the viewer shows, from the converter at
`OCCT_CONVERTER_URL`); without it, or when its box does not match the
B-Rep's, they are `not_evaluated`. The sampled distance estimates the
largest deviation from below: between samples it can be larger.

**Observability.** `metaforge_preview_rebuild_seconds{op}` (the browser's
reported rebuild time), `metaforge_preview_comparison_total{outcome}`
(`consistent`, `discrepancy`, `incomplete`, `error`),
`metaforge_preview_discrepancy_total{check}`,
`metaforge_manipulation_commit_total{source, outcome}` (`committed`,
`refused`, `stale`) and `metaforge_exact_incremental_total{outcome}`. Alert
`PreviewComparisonErrors` (comparisons that raise, not discrepancies).
Spans `twin.parametric_preview.definition`, `twin.parametric_preview.commit`,
`twin.exact_cad.preview_comparison` and `twin.preview_comparison.compare`.

Measured in the dashboard's test run (jsdom, one workstation): rebuilding the
preview of a plate with two holes, a rounded slot or a 270 degree revolve
takes a median of about 1 ms and a 95th percentile under 3 ms, against a
budget of 100 ms for simple parts.

### Representation levels, component ids and maturity {#representation-levels}

Every artifact a screen can show as the product carries a representation
descriptor. Code: `twin_core/representation.py` (the descriptor and its
guard), `twin_core/component_identity.py` (component ids),
`twin_core/maturity.py` (maturity per component),
`tool_registry/tools/twin/layout_mesh.py` and
`api_gateway/twin/structured_mesh.py` (the L1 mesh),
`api_gateway/twin/representation_routes.py` (the routes).

**The descriptor.** Node responses (`GET /v1/twin/nodes`,
`GET /v1/twin/nodes/{id}`) return `representation: {level, authority, label,
source_revision, component_ids, generated_by, derived, display_rank,
dimensions, guarded}`, or `null` for a node that is not displayable.

| Level | What it is | Most it may claim | Display rank |
|---|---|---|---|
| `L3` | Tessellation of a `cad_model` revision whose geometry state is `validated` or `released` | `validated` | 5 |
| `L2` | The browser's parametric preview from the shared definition (`preview`, computed client-side; a stored snapshot is a `render` by `dashboard.parametric_preview`), a `cad_model` revision not yet validated (`exact`), or a failed or preview one (`preview`) | `exact` | 4 |
| `L1` | Simple 3D parts drawn from the concept block layout, sizes estimated | `structured` | 3 |
| `L0` | An AI 3D concept (`twin.generate_concept_mesh`, or any mesh `render`) | `illustrative` | 2 |
| `illustration_image` | A concept image, a picture render, a design sketch, a generated sketch or sketch strokes | `illustrative` | 1 |

`authority` is one of `illustrative`, `structured`, `preview`, `exact` and
`validated`; `label` is the word Create shows (`Illustrative`, `Preview`,
`Exact` or `Validated`; `structured` reads `Preview`). `source_revision` is
the `KEY@n` the artifact derives from. `generated_by` is `{tool,
operation_id}`. A generator that knows its descriptor stores it in
`metadata.representation` (`derived: false`); every older artifact gets one
derived at read time from its type and geometry state (`derived: true`), so
nothing is backfilled.

Two rules hold on every read, whatever was stored: `L3` only for a
`cad_model` revision that is validated or released (any other `L3` claim
reads the level its type implies, `L2` for CAD), and `L0`, `L1` and pictures
never above their ceiling in the table. A corrected claim is listed in
`guarded`, logged as `representation_mislabel_guarded` and counted in
`metaforge_representation_mislabel_total{level, claimed}`
(alert `RepresentationMislabelGuardHits`). A generator cannot write such a
claim in the first place: `descriptor()` refuses it.

**Component ids.** A component gets an id, `CMP-<slug>` (with `-2`, `-3` on
a clash), the first time a concept assembly names it (`twin.record_decision`
with `assembly`), and keeps it. The decision stores it on each part
(`assembly.parts[i].component_id`) and as `component_identity: {name: id}`;
a later assembly keeps every id already issued, and a decision recorded
before ids existed is read as if they had been issued then. The id travels:

- into the L1 mesh, as each part node's name and `extras.component_id`;
- onto CAD revisions: when a `cad_model` revision is committed, its
  `component_id` (or `part_component_ids` for an assembly's members) is
  carried from the prior revision, or matched by name once with the design
  gate's rule; recording an assembly also stamps the CAD heads that existed
  before it. Later matching is by the stored id, so a renamed part keeps its
  component. Nothing is renamed;
- onto face semantic tags as a prefix (`CMP-base::<tag>`); signature
  matching compares tags without the prefix.

**The L1 mesh.** `twin.render_structured_mesh`, or
`POST /v1/twin/projects/{id}/structured-mesh`, draws the project's newest
concept block layout (the `concept_layout` of a sketch made by
`twin.render_concept_sketch`), or a named one, as a GLB: one node per part
named by its component id, with `extras` giving `component_id`, `part_name`,
`shape`, `size_mm`, `position_mm`, `provenance`, `dimensions: "estimated"` and
`level: "L1"`, under a root node `metaforge_structured_mesh` (Y up, metres).
It is stored as a `render` with `authority: structured`, `generated_from` the
sketch. It is deterministic and free (no provider), so it answers inline: the
same layout and ids give the same bytes, and an identical mesh already in the
project is returned with `reused: true` instead of a new node. Recording a
concept assembly draws it once when a layout exists. Counted in
`metaforge_structured_mesh_total{outcome}`.

**Maturity per component.** `GET /v1/twin/projects/{id}/maturity` returns,
per component, one of:

| Maturity | When |
|---|---|
| Illustrative | Named in the concept assembly, or shown in an L0 mesh or a picture |
| Structured | A part of an L1 mesh |
| Parametric | A CAD revision exists for it (feature definitions) but is not validated, or a parametric sketch carries its id |
| Exact | Its CAD head is validated |
| Verified | Exact, and at least one current check pinned to that head passed (`PASS` or `WARN` in the shared validation status), with none `FAIL` or `STALE` |
| Released | Verified, and the head was released through the release path |

Each component lists its `evidence` (`{maturity, kind, node_id, ref, detail}`
per claim) and a `next_step`. `product` gives `maturity` (that of the least
mature required component), `by_maturity`, `limited_by`, `claims: {exact,
verified, released}` (true only when every required component is at least
that) and a plain `summary`. Required components are the current assembly's
`made` parts; bought and standard parts are listed but not required. Without
an assembly, every known component is required. So a product with a verified
base and an illustrative cushion is Illustrative, never Exact.

Accepting a visual proposal is recorded as acceptance and is never read by
the maturity computation: no acceptance or approval flag raises a component
above what its geometry and checks show. Counted in
`metaforge_maturity_query_total{product}`.

## 6. Twin API

The Twin API is the public interface for all graph operations. Agents, the orchestrator, and the gateway interact with the Twin exclusively through this API.

> **Note**: The Twin API composes the lower-level `GraphEngine`, `VersionEngine`, and `ConstraintEngine` interfaces into a single facade. See Section 3.1, Section 4, and Section 5 for the underlying ABCs.

### CRUD Operations

```python
class TwinAPI(ABC):
    # --- Artifacts ---
    @abstractmethod
    async def create_work_product(self, work_product: WorkProduct, branch: str = "main") -> WorkProduct:
        ...

    @abstractmethod
    async def get_work_product(self, work_product_id: UUID, branch: str = "main") -> WorkProduct | None:
        ...

    @abstractmethod
    async def update_work_product(self, work_product_id: UUID, updates: dict, branch: str = "main") -> WorkProduct:
        ...

    @abstractmethod
    async def delete_work_product(self, work_product_id: UUID, branch: str = "main") -> bool:
        ...

    @abstractmethod
    async def list_work_products(
        self,
        branch: str = "main",
        domain: str | None = None,
        work_product_type: WorkProductType | None = None,
    ) -> list[WorkProduct]:
        ...

    # --- Constraints ---
    @abstractmethod
    async def create_constraint(self, constraint: Constraint) -> Constraint:
        ...

    @abstractmethod
    async def get_constraint(self, constraint_id: UUID) -> Constraint | None:
        ...

    @abstractmethod
    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
        ...

    # --- Components ---
    @abstractmethod
    async def add_component(self, component: Component) -> Component:
        ...

    @abstractmethod
    async def get_component(self, component_id: UUID) -> Component | None:
        ...

    @abstractmethod
    async def find_components(self, query: dict) -> list[Component]:
        ...

    # --- Relationships ---
    @abstractmethod
    async def add_edge(self, source_id: UUID, target_id: UUID, edge_type: EdgeType, metadata: dict | None = None) -> EdgeBase:
        ...

    @abstractmethod
    async def get_edges(self, node_id: UUID, direction: str = "outgoing", edge_type: EdgeType | None = None) -> list[EdgeBase]:
        ...

    @abstractmethod
    async def remove_edge(self, source_id: UUID, target_id: UUID, edge_type: EdgeType) -> bool:
        ...

    # --- Queries ---
    @abstractmethod
    async def get_subgraph(self, root_id: UUID, depth: int = 2, edge_types: list[EdgeType] | None = None) -> SubGraph:
        ...

    @abstractmethod
    async def query_cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """Execute a raw Cypher query (read-only). For advanced queries not covered by the API."""
        ...

    # --- Versioning ---
    @abstractmethod
    async def create_branch(self, name: str, from_branch: str = "main") -> str:
        ...

    @abstractmethod
    async def commit(self, branch: str, message: str, author: str) -> Version:
        ...

    @abstractmethod
    async def merge(self, source: str, target: str, message: str, author: str) -> Version:
        ...

    @abstractmethod
    async def diff(self, branch_a: str, branch_b: str) -> VersionDiff:
        ...

    @abstractmethod
    async def log(self, branch: str = "main", limit: int = 50) -> list[Version]:
        ...
```

---

## 7. Neo4j Implementation

> **Status**: Planned for v0.2+. The current implementation uses `InMemoryGraphEngine`.

### Index Strategy

```cypher
-- Primary key indexes
CREATE CONSTRAINT work_product_id IF NOT EXISTS FOR (a:WorkProduct) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT constraint_id IF NOT EXISTS FOR (c:Constraint) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT version_id IF NOT EXISTS FOR (v:Version) REQUIRE v.id IS UNIQUE;
CREATE CONSTRAINT component_id IF NOT EXISTS FOR (p:Component) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT agent_id IF NOT EXISTS FOR (ag:Agent) REQUIRE ag.id IS UNIQUE;

-- Lookup indexes
CREATE INDEX artifact_domain IF NOT EXISTS FOR (a:WorkProduct) ON (a.domain);
CREATE INDEX work_product_type IF NOT EXISTS FOR (a:WorkProduct) ON (a.type);
CREATE INDEX artifact_path IF NOT EXISTS FOR (a:WorkProduct) ON (a.file_path);
CREATE INDEX constraint_domain IF NOT EXISTS FOR (c:Constraint) ON (c.domain);
CREATE INDEX constraint_status IF NOT EXISTS FOR (c:Constraint) ON (c.status);
CREATE INDEX version_branch IF NOT EXISTS FOR (v:Version) ON (v.branch_name);
CREATE INDEX component_part IF NOT EXISTS FOR (p:Component) ON (p.part_number);
CREATE INDEX component_mfr IF NOT EXISTS FOR (p:Component) ON (p.manufacturer);
```

### Common Cypher Patterns

**Get all work products in a domain with their constraints**:

```cypher
MATCH (a:WorkProduct {domain: $domain})
OPTIONAL MATCH (a)-[:CONSTRAINED_BY]->(c:Constraint)
RETURN a, collect(c) AS constraints
```

**Get the dependency tree for a work product**:

```cypher
MATCH path = (root:WorkProduct {id: $work_product_id})-[:DEPENDS_ON*1..5]->(dep:WorkProduct)
RETURN root, nodes(path) AS chain, relationships(path) AS edges
```

**Find all work products produced by an agent session**:

```cypher
MATCH (ag:Agent {session_id: $session_id})<-[:PRODUCED_BY]-(a:WorkProduct)
RETURN a ORDER BY a.created_at
```

**Get version history for a branch**:

```cypher
MATCH (v:Version {branch_name: $branch})
OPTIONAL MATCH (v)-[:PARENT_OF]->(parent:Version)
RETURN v, parent.id AS parent_id
ORDER BY v.created_at DESC
LIMIT $limit
```

**Evaluate which constraints apply to modified work products**:

```cypher
MATCH (a:WorkProduct)
WHERE a.id IN $modified_work_product_ids
MATCH (a)-[:CONSTRAINED_BY]->(c:Constraint)
RETURN DISTINCT c
```

**Get BOM with components and reference designators**:

```cypher
MATCH (bom:WorkProduct {type: 'bom'})-[r:USES_COMPONENT]->(comp:Component)
RETURN comp.part_number, comp.manufacturer, comp.description,
       r.reference_designator, r.quantity, comp.unit_cost
ORDER BY r.reference_designator
```

---

## 8. Schema Evolution

The Twin schema will evolve across phases. Schema changes follow these rules:

1. **Additive only** within a major version: new node types, new properties (with defaults), new edge types.
2. **No breaking changes** to existing node/edge properties within a major version.
3. **Migration scripts** are provided for any structural changes across major versions.
4. **Node labels** are never renamed — deprecated labels are kept as aliases.

### Phase 1 Schema

Phase 1 implements the full schema defined in this document. The following node types and edge types are required for the mechanical vertical:

- **Nodes**: WorkProduct (CAD_MODEL, SIMULATION_RESULT), Constraint, Version, Component, Agent
- **Edges**: DEPENDS_ON, CONSTRAINED_BY, PRODUCED_BY, VERSIONED_BY, USES_COMPONENT

### Phase 2 Additions

- Additional WorkProductType values for electronics (SCHEMATIC write support)
- Extended Component specs for electronics parts (voltage rating, current rating, ESR)
- New edge type: `ROUTED_TO` (net-to-pad routing in PCB)

### Phase 2 AAS Additions

- `BOMItem` node type with `global_asset_id` and `supplier` properties
- `DesignElement` node type with AAS-aligned `parameters` keys
- `TwinModel` node type with `global_asset_id` for product-level twin identification
- `DeviceInstance` node type with `global_asset_id` for serial-number-level tracking

### Phase 3 Additions

- Supply chain tracking nodes (Supplier, Order)
- Extended DeviceInstance telemetry (FieldData)
- Additional edge types for after-sales and sustainability tracking

---

## Appendix A: AAS-Aligned Key Conventions

The following tables document recommended keys for dictionary properties on graph nodes. These keys align with the **Asset Administration Shell (AAS)** submodel standards to enable interoperability with IEC 63278 / IDTA tooling.

### A.1 `globalAssetId` URN Conventions

The `global_asset_id` property on `BOMItem`, `DeviceInstance`, and `TwinModel` follows URN format:

| Node Type | URN Pattern | Example |
|-----------|-------------|---------|
| `BOMItem` | `urn:metaforge:bom:<manufacturer>:<mpn>` | `urn:metaforge:bom:STMicroelectronics:STM32F407VG` |
| `DeviceInstance` | `urn:metaforge:device:<serialNumber>` | `urn:metaforge:device:SN-2026-00042` |
| `TwinModel` | `urn:metaforge:model:<productId>:<version>` | `urn:metaforge:model:drone-fc:1.0.0` |

### A.2 `BOMItem.specifications` Recommended Keys

| Key | Type | AAS Submodel | Example |
|-----|------|-------------|---------|
| `countryOfOrigin` | String (ISO 3166-1) | Digital Nameplate | `"US"` |
| `rohsCompliance` | Enum | Digital Nameplate | `"compliant"` / `"exempt"` / `"non-compliant"` |
| `reachCompliance` | Enum | Digital Nameplate | `"compliant"` / `"not-assessed"` |
| `customsTariffNumber` | String | Digital Nameplate | `"8542.31"` |
| `weightGrams` | Float | Technical Data | `2.5` |
| `powerDissipationW` | Float | Technical Data | `4.5` (an actuator/component's known continuous power dissipation, watts — `calculix.run_thermal` has no Twin access itself, so the caller reads this key and passes the raw wattage as `power_dissipation_w`) |
| `draw_w` / `draw_peak_w` / `draw_average_w` / `output_w` | Float | Technical Data | `331.2` (real power the hierarchy rollup — `twin_core.consistency.hierarchy_rollup.compute_hierarchy_rollup` — reads off `WorkProduct.metadata` (via REALIZED_BY) or `BOMItem.specifications` (via INSTANCE_OF) to compute peak/average draw, output work, and derived dissipation per hierarchy branch. Snake_case, not this table's usual camelCase — these are the literal keys the rollup code reads, and documentation follows the real code rather than a convention it doesn't use. `draw_w` alone counts as both peak and average if a caller hasn't distinguished them; `output_w` omitted defaults dissipation to the full draw, recorded as a tracked `PowerAssumption` rather than applied silently) |

### A.3 `DesignElement.parameters` Recommended Keys

| Key | Type | AAS Submodel | Example |
|-----|------|-------------|---------|
| `hardwareVersion` | String | Digital Nameplate | `"rev-C"` |
| `softwareVersion` | String | Digital Nameplate | `"1.2.0"` |
