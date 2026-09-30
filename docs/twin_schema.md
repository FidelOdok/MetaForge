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

*Source: `twin_core/models/quantity.py` (FORGE-311)*

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
| `acceptance_criteria` | `str` | No | What must be true for this requirement to be considered met (FORGE-312) |
| `verification_method` | `str` | No | How it's verified (e.g. `"FEA"`, `"test"`, `"inspection"`) (FORGE-312) |
| `metric` | `str` | No | The property this requirement is about, e.g. `"tip_deflection"` (FORGE-259) |
| `operator` | `str` | No | Comparison against `limit` -- `"<="`/`">="`/`"=="`/`"<"`/`">"`/`"!="`. Default `"<="` (FORGE-259) |
| `limit` | `float` | No | The numeric limit `metric` is compared to (FORGE-259) |
| `unit` | `str` | No | Must be a unit `twin_core.models.quantity` recognizes (FORGE-259) |
| `target_node_type` | `str` | No | The kind of node `metric` is measured on, e.g. `"cad_model"` (FORGE-259) |
| `expected_evidence` | `str` | No | The kind of evidence that would verify this, e.g. `"simulation"` -- must be one of `twin_core.models.enums.EVIDENCE_TYPES` (FORGE-258) |
| `last_evaluated` | `datetime` | No | When the constraint was last checked |
| `metadata` | `dict` | No | Additional context |

`acceptance_criteria`/`verification_method` are real fields (not metadata
keys) so `twin_core.consistency.gates`'s G7 (Verification Readiness) gate
can check a critical requirement for both directly. Both fall back to the
identically-named `metadata` key when empty, for requirements
`RequirementAuthorAgent` (FORGE-55) wrote before these fields existed.

FORGE-259 (gap G-A3): `metric`/`operator`/`limit`/`unit`/`target_node_type`
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
itself, or into FORGE-315/317/320's evaluator/sensitivity/optimizer tools
automatically (those already take their own metric/limit kwargs directly)
-- reading a Constraint's structured fields into those tools automatically
is real, separable follow-up work, not this ticket's own minimal scope.

The dashboard's Requirements page (section "Evidence matrix", FORGE-318)
gained a "+ New constraint" form writing exactly this binding via a new
`POST /v1/requirements/constraints` route (reusing
`api_gateway.twin.constraint_recorder` directly, the same validation every
MCP caller already goes through) -- create-only for this first cut, no
edit.

FORGE-258 (gap G-A2): `expected_evidence` closes the requirement-to-
verification-linkage gap -- `verification_method` (FORGE-312) says *how* a
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
(`api_gateway/requirement_intelligence/traceability.py`, FORGE-56) is a
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
    # FORGE-259: structured measured-key binding -- alternative to `expression`.
    metric: str = ""
    operator: str = "<="
    limit: float | None = None
    unit: str = ""
    target_node_type: str = ""
    # FORGE-258: the kind of evidence expected to verify this requirement.
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

FORGE-321: `create_device_instance`/`get_device_instance`/`list_device_instances` CRUD (`twin_core/api.py`) and `twin.register_device_instance` (MCP tool) were added -- the model existed since before this epic but was never instantiable. See section 2.18.

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

A HierarchyNode is one position in a project's product hierarchy tree (product -> system -> subsystem -> assembly), added in FORGE-260 (gap G-B1). One generic node type discriminated by `kind` -- the same "common base, not eight bespoke classes" pattern `EngineeringEntity` already uses -- rather than a class per organizational level. A fabricated part or COTS component is deliberately NOT a HierarchyNode: it's the already-real `WorkProductType.CAD_MODEL` or `BOMItem` a leaf HierarchyNode links to via `REALIZED_BY`/`INSTANCE_OF`, never duplicated.

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

### 2.11 InterfaceQuantity (FORGE-313)

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

### 2.12 Tiered Evaluator (FORGE-315)

Not a node type -- a computation, invoked via `twin.evaluate_metric` (spec §30, "prefer the minimum sufficient fidelity; escalate only when evidence quality demands it"). Only `metric="tip_deflection"` is implemented: a tier-0 closed-form cantilever-beam estimate against a CAD `WorkProduct`'s own recorded `geometry_features.properties.bounding_box` (MET-630), escalating to a real tier-2 `calculix.run_fea` call when the estimate's margin to a supplied `limit_mm` falls inside its error band.

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

The band is a fixed prior (`band_fraction` of `limit_mm`, default 0.2) -- calibrated bands from real measurement residuals are Step 11's own scope (`digital_twin/calibration/`), not this one's. Every tier's result is recorded as `twin.record_evidence`, `valid_against` the work product's current revision (FORGE-314) -- tier-0 as `evidence_type="calculation"`, tier-2 as `"simulation"`.

Tier-2 escalation requires the caller to already have a generated mesh + resolved node sets (`tier2: {mesh_file, fixed_node_set, load_node_set, material, load_force_n}`) -- `twin.evaluate_metric` does not derive FEA boundary conditions on its own. Escalating with no `tier2` args returns a clear `{"attempted": false, "reason": ...}` rather than guessing node-set names for a part it's never seen a load case for (a real, currently-unsolved gap: FORGE-278/239/277 together get a *human* to point-and-click node sets into a reusable `LOAD_CASE` work product, but there is no programmatic path from "a design changed" to "correct boundary conditions" for a genuinely novel part).

A general per-metric tier *registry* (tier 1 hand-calcs, other metrics) and an automatic "runs on every design change" hook are both deliberately out of scope for this ticket -- see `twin_core/prediction/evaluator.py`'s own module docstring for the full rationale. `InterfaceQuantity.predicted` (§2.11) already has `tier`/`evidence` fields anticipating this, but no `PREDICTED_BY` edge writer exists yet to connect a `twin.evaluate_metric` result back to an interface quantity automatically -- still a manual step.

*Source: `twin_core/prediction/evaluator.py`, `api_gateway/twin/metric_evaluator.py`*

### 2.13 Automatic Revalidation on ECT Commit (FORGE-316)

`EngineeringChangeTransaction` (FORGE-66, `twin_core/models/engineering_change_transaction.py`) gained two fields on top of its existing state machine:

```python
# Plain dicts (RevalidationStep.model_dump(mode="json")), not a typed
# field -- avoids an import cycle with twin_core.consistency.impact.
revalidation_plan: list[dict[str, Any]] = Field(default_factory=list)
revalidation_result: dict[str, Any] | None = None
```

`analyze()` stores `ImpactEngine.analyse()`'s full pre-commit PREVIEW plan here (a projection -- nothing has changed yet). A successful `commit()` OVERWRITES it with the REAL plan, built from `StalenessEngine.propagate()`'s actual writes -- this is the fix for a genuine gap: FORGE-66's own `commit()` never called `StalenessEngine` at all, so no ECT-driven change ever marked anything stale for real until this. `commit()` propagates once per REVISE/SUPERSEDE/DEPRECATE/INVALIDATE operation in the patch (best-effort -- a propagation failure never fails an already-successful commit), unions the resulting `StaleMarking`s, and reuses `ImpactEngine.build_revalidation_plan` (now public, shared between the pre-commit preview and this real post-commit build) to turn them into the same `RevalidationStep` shape.

`twin.execute_revalidation_plan(ect_id)` (requires the ECT to be COMMITTED) is the "now actually re-run them" half: for each plan step naming a stale `engineering_entity` whose `entity_type == "evidence"` and whose `metadata["replay"]` is a `{tool_id, args}` dict, it looks `tool_id` up in an injected dispatch table and calls it again with `args` (`api_gateway/twin/revalidation.py`'s `make_revalidation_executor`). A successful replay records a NEW Evidence entity with `supersedes=<old id>` (FORGE-65's own revalidation flow -- never a mutation of the old evidence). Everything else -- a non-evidence entity the impact walk reached (e.g. a Constraint), evidence with no `replay` recipe (hand-authored, or older than this field), or a `tool_id` the caller didn't wire in -- is reported in `manual_review_needed`, never guessed at.

`metadata["replay"]` is a new, optional, unvalidated passthrough on Evidence (`twin.record_evidence`'s `evidence_recorder.py`, stored opaquely -- this module never inspects its shape). Only a caller that actually knows how to re-invoke itself should set it; `api_gateway/twin/metric_evaluator.py` is the first (`{"tool_id": "twin.evaluate_metric", "args": {...the exact original top-level kwargs...}}`). It is NOT derived from `producer.tool`/`inputs` (those are often a descriptive Python dotted path, not an invokable MCP tool id -- guessing replayability from them would be wrong more often than right).

**Deliberately out of scope** (each a separable piece, no acceptance-criterion pressure to build now): property-level impact through `Constraint.dependencies` (that field doesn't exist yet -- explicitly deferred since FORGE-312/Step 2, confirmed still true during this ticket's own scoping); a dashboard UI showing the impact graph and re-run progress (zero existing ECT-related UI surface today -- new work from scratch).

*Source: `twin_core/transactions/ect.py`, `twin_core/consistency/impact.py`, `api_gateway/twin/revalidation.py`*

### 2.14 Sensitivity Analysis (FORGE-317)

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

`metric="tip_deflection"` ranks `wall_thickness_mm` and `length_mm` against a new rectangular-hollow-tube deflection formula (`twin_core/prediction/evaluator.py`'s `hollow_tube_tip_deflection_mm`, `I = (W H^3 - (W-2t)(H-2t)^3)/12`) -- FORGE-315's own solid-beam model (`cantilever_tip_deflection_mm`) has no wall-thickness concept at all, so this is a real, scoped extension, not free. `metric="mass"` ranks `wall_thickness_mm` (continuous, `hollow_tube_mass_kg`) and a caller-supplied (default: aluminum/steel/titanium/carbon_fiber) set of candidate materials (categorical -- the actual margin delta from swapping material, not a derivative, since "per unit of what" means nothing for a discrete choice). Material density/elastic-modulus lookups reuse `tool_registry.tools.cadquery.materials`' existing `resolve_density_kg_m3`/`resolve_elastic_properties` (FORGE-234's own table) -- not a second material list.

Every ranking is recorded as `twin.record_evidence` (`evidence_type="calculation"`), `valid_against` the work product it was computed from.

**Deliberately out of scope**: a general "sweep any Design IR parameter" engine -- `twin_core/design_ir/` turned out to be a CAD-*authoring* intermediate representation (a sequence of FreeCAD/CadQuery operations), not an engineering-analysis parameter model, and the real yardstick arm's own geometry was authored via a raw `cadquery.execute_script` call rather than through Design IR at all, so there's no live Design IR document to sweep; tier 1 (torque) sensitivity (tier 1 still doesn't exist, FORGE-315's own finding, and isn't needed for either named metric here); a dashboard tornado-chart UI (zero existing chart/ranking UI anywhere in `dashboard/src/` -- same scope cut FORGE-316's own dashboard bullet already established).

*Source: `twin_core/prediction/sensitivity.py`, `api_gateway/twin/sensitivity.py`*

### 2.15 Evidence-Backed Requirement Matrix (FORGE-318)

Not a node type -- a join, computed live via `GET /v1/requirements/matrix`, over requirements (Constraint), claims (`twin_core.consistency.claims`, FORGE-65), and the Evidence those claims cite.

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
    status: str            # "pass" | "uncertain" | "fail" | "no_data" | "stale"
    detail: str
    artefactIds: list[str]
    evidence: list[EvidenceSummary]
```

`twin_core.consistency.claims.list_claims_for_requirement` (new) is the one piece that was genuinely missing: `evaluate_claim` (FORGE-65) only ever evaluated a single KNOWN `(artefact, requirement)` pair; the matrix needs "what claims exist for this requirement" as its own lookup, which it gets by walking `requirement_id`'s incoming `CLAIM_EDGE_KIND` edges and re-using `evaluate_claim` per match (same live-computed status, no parallel logic).

Status is derived fresh on every call, same "never a stored, driftable boolean" discipline as `evaluate_claim` itself:

- `no_data` -- no claim recorded against the requirement at all.
- `stale` -- a claim's cited evidence includes at least one entity whose current staleness is STALE/SUPERSEDED/INVALID, flagged even when the claim itself is still SUPPORTED by other current evidence (the ticket's own "flags stale evidence after a change" wording).
- `fail` / `uncertain` / `pass` -- claim SUPPORTED, no stale evidence; derived from the most current evidence's own margin. Reads either evidence result shape this codebase produces: FORGE-315's tier-0/tier-2 (`value_mm`/`limit_mm`/`margin_mm`/`escalated`) or FORGE-317's sensitivity ranking (`baseline_value`/`limit`/`baseline_margin`, no `escalated` concept of its own). Negative margin is `fail`; a tier-0 result with `escalated=true` is `uncertain` (too close to call without a higher-fidelity check, FORGE-315's own semantics); otherwise `pass`.

FORGE-318 also closed a small gap in FORGE-315's own tier-2 evidence: the raw `calculix.run_fea` output had no `tier` key of its own (only inferable from `producer.tool` string matching) -- `api_gateway/twin/metric_evaluator.py` now persists `{"tier": 2, "metric": ..., **fea_result}` for tier-2 Evidence, so the matrix (and any future consumer) reads `tier` directly rather than guessing.

Dashboard: `RequirementsPage.tsx` gained an "Evidence matrix" section (below the existing FORGE-257 quality table, not a new page) with per-row status badges, an expandable evidence-detail row (method/tier/value/limit/margin/staleness), a client-side CSV/MD export (mirrors `BomPage.tsx`'s own `handleExportCsv` pattern -- no backend export route needed), and a Structure-tab link per row. That link is a plain `/twin` link, not a per-node deep-link: `TwinViewerPage.tsx` does support one (`?node=<hierarchy_node_id>`), but the matrix's own `artefactIds` are WorkProduct ids, not HierarchyNode ids -- no Constraint-to-HierarchyNode mapping exists yet to resolve one from the other, so this ticket does not fabricate that link; a real one is a separable follow-up.

*Source: `twin_core/consistency/claims.py`, `api_gateway/requirement_intelligence/matrix.py`, `api_gateway/requirement_intelligence/routes.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.16 MaturityGate (FORGE-319)

The first place in this codebase a gate genuinely REFUSES rather than only reports. `twin_core.consistency.gates`'s G3-G8 design-flow gates are a different, pre-existing concept -- per that module's own docstring, a FAILED status only informs a human reviewer, since `enforce_consistency_gate` doesn't exist. `MaturityGate` is its own `NodeType` (not a generic `EngineeringEntity` tag) because a promotion attempt has real, structured multi-field state worth typing, the same reasoning `EngineeringChangeTransaction` (FORGE-66) already used for itself.

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
    comment: str | None = None  # FORGE-290: a human reviewer's own rationale
```

There is deliberately no `update_maturity_gate` -- each `twin.attempt_promotion` call persists a brand-new, immutable record (whether it promoted or was refused), so "why wasn't this promoted" always has a real, queryable history instead of one mutable, driftable record.

`attempt_promotion(twin, *, project_id, level, required_claim_ids, k=1.0, decided_by=None, comment=None, reject=False)` -- note `decided_by` is supplied by the *gateway* from the approval record, never passed through from a tool argument or request body (FORGE-393) (`api_gateway/requirement_intelligence/promotion.py`) reuses rather than re-derives:

- **Pass/fail decision** -- §2.15's own `build_requirement_matrix` status per required requirement id. `status="pass"` maps to `PASS`; `"uncertain"`/`"stale"` to `UNCERTAIN`; `"fail"`/`"no_data"` to `FAIL`. No second margin/band computation is invented here.
- **Waiver coverage** -- the existing `entity_type="waiver"` / `twin.approve_engineering_entity` mechanism (FORGE-73), the same `list_engineering_entities(..., entity_type="waiver")` + `authority in APPROVED_AUTHORITY` pattern `gates.py`'s own G8 `_evaluate_waivers_check` uses (that constant is now public, promoted the same way FORGE-316 promoted `ImpactEngine._build_revalidation_plan`, since this is now a second real consumer) -- but scoped per-requirement here: only a waiver whose `parent_refs` names the SPECIFIC blocked requirement id unblocks it, since G8's own check is a blanket "any waivers outstanding" scan and a mass-specific waiver must never silently unblock an unrelated requirement.
- **Human authority** -- the person who *approved the call*, resolved by the server. This used to be a caller-supplied `decided_by` string carrying "the same agent-asserted-identity trust level every `created_by` field already carries", which was true and was the problem (FORGE-393): on this tool the string is not a label attached to a write, it **is** the write. The model filled it, so the gate recorded whatever name the model chose, and over local stdio -- where writes are exempt from approval -- that was the only authority recorded at all.

    Now: `twin.attempt_promotion` is never exempt from approval, even locally; a caller that passes `decided_by` gets an error rather than a silent drop; and the dispatcher injects the approver under a reserved key it strips from client input first, so the name cannot be forged by imitating the convention. An approval that does not identify anyone is refused rather than recorded as anonymous. `decided_by` is still recorded on EVERY attempt, promoted or not (FORGE-290).

    On a gateway running `METAFORGE_AUTH_MODE=off` there is no verified identity, so a dashboard click records `local:dashboard` with `verified=False`. That is an honest statement of what is known. It is not the same as a name nobody ever established.

FORGE-290 (gap G-G4) closes the "gates evaluate real measured data and block on 'no data'" ask two ways: the evidence-gate logic above ALREADY blocked on `no_data` since FORGE-319 shipped it (live-validated against the real arm project's `no_data` requirements as part of this ticket); what was missing was the reviewer's own **veto**. `reject=True` refuses a promotion EVEN IF every required claim is `pass`/`waived` -- the evidence gate is a necessary condition for promotion, never a sufficient one that overrides a human's own judgement. The rejecting human is the approver, taken from the approval rather than the request (FORGE-393). `comment` is a human-authored rationale, distinct from the system-derived `blocked_reason`, recorded on either outcome (a reviewer can leave context on an approval too, not only a rejection); when rejecting without an explicit comment, `blocked_reason` falls back to `f"rejected by {decided_by}"` rather than a bare `None`.

`twin.attempt_promotion` (MCP tool, `tool_registry/tools/twin/adapter.py`) is registered only when the gateway wires in a `promotion_attempter` callable (same optional-constructor-param / conditional-registration pattern every `twin.*` tool in this epic follows). `POST /v1/promotion/attempt` and `GET /v1/promotion` (`api_gateway/promotion/routes.py`, FORGE-290) expose the same action and the (previously agent-only, unsurfaced) `twin.list_maturity_gates` read to the dashboard's new "Gate review" section on the Requirements page -- pick required claims, a maturity level and an optional comment, then Approve or Reject. There is deliberately no reviewer field to type into (FORGE-393) -- the reviewer is whoever is making the request.

**Deliberately out of scope**: any change to `gates.py`'s own G3-G8 advisory posture (`enforce_consistency_gate` still does not exist -- a much larger, separate architectural change than this narrower requirement-matrix-backed gate); rollback/undo of a granted promotion (`MaturityGate` has no such semantics); retrofitting `RunDetailPage.tsx`'s unrelated design-flow `gate-review` UI (a different, pre-existing, advisory-only mechanism -- G3-G8's summary string, not this per-requirement blocking evidence gate) into this one.

*Source: `twin_core/models/maturity_gate.py`, `api_gateway/requirement_intelligence/promotion.py`, `api_gateway/promotion/routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.17 Wall-Thickness Optimiser (FORGE-320)

Not a node type -- a computation, invoked via `twin.optimize_parameter` (target lifecycle spec App. A "Optimiser", step 10: minimise an objective subject to constraints). The ticket's own Jira scope says "over Design IR parameters", but FORGE-317's own scoping already found -- confirmed still true here -- that `twin_core/design_ir/` is a CAD-*authoring* op sequence, never persisted onto a committed WorkProduct (the real arm's own "Upper Arm Link" carries `metadata["authored_by"] = "cadquery.execute_script"` and an empty `geometry_features.parameters` dict), not an engineering-analysis parameter model. This optimiser instead searches `wall_thickness_mm`, the one parameter FORGE-317's own sensitivity ranking already found dominant for both deflection and mass margin, over the same hollow-rectangular-tube hand-calcs.

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

Mass is the ticket's own stated OBJECTIVE, not a third constraint alongside SF/deflection -- the project's separate `moving_mass_budget` Constraint covers the whole assembly, not this one part in isolation, and no per-part budget allocation exists to check a share of it against (`BudgetAllocation.owner`/`.discipline` from FORGE-313 records who owns an interface quantity, not a numeric per-part mass share). Both constraints are monotonically non-decreasing in wall thickness (thicker wall -> stiffer -> lower deflection; thicker wall -> lower bending stress -> higher safety factor) for a fixed outer envelope, so the minimum-mass feasible point is exactly the smallest wall thickness where both constraints first hold -- bisection is exact here, not a heuristic, and no general nonlinear optimiser (e.g. scipy.optimize) was reached for.

The safety-factor check is a real, new tier-0 hand-calc (`cantilever_max_bending_stress_mpa`, `sigma = M c / I` at the fixed end) -- no prior ticket in this epic computed stress at all, only deflection/mass. It needed a real yield-strength table, which didn't exist: `tool_registry/tools/cadquery/materials.py` gained `MATERIAL_YIELD_MPA` + `resolve_yield_mpa` (same name set, same "unrecognized material raises" discipline as the existing `MATERIAL_ELASTIC_MPA`/`resolve_elastic_properties`). "Confirmed by FEA" (the ticket's acceptance wording) is achievable only when a real `calculix.run_fea` mesh/load case is supplied for tier-2 escalation -- this ticket does not attempt automatic FEA boundary-condition derivation for a novel part (the same real, still-unsolved gap FORGE-315 already documented, FORGE-278/239/277).

Every search is recorded as Evidence, `valid_against` the work product (FORGE-314's staleness pinning). When a feasible winner is found, it's also recorded as a Decision via the EXISTING `twin.record_decision` mechanism (MET-495, FORGE-61's own "alternatives" field) -- no new "Decision with alternatives" node type was built, since `record_decision` already is exactly that; each rejected candidate the bisection evaluated becomes one alternative, with the constraint that rejected it as its `reason_rejected`.

**Deliberately out of scope**: proposing the winning wall thickness as an actual geometry change via ECT (`ControlledEntityKind` only supports `constraint`/`engineering_entity` today, not `work_product` -- a nontrivial, separate widening of the transaction engine itself, not a quick follow-up, same honesty precedent as FORGE-316 deferring `Constraint.dependencies`); regenerating/committing the optimised geometry (no parametrized re-authoring script exists for the real arm part -- its original `cadquery.execute_script` source was never persisted with a substitutable `wall_thickness_mm` variable); any dashboard UI (no UI surface named in this ticket's own Scope, same precedent as FORGE-315/316/317/319).

*Source: `twin_core/prediction/optimizer.py`, `api_gateway/twin/optimizer.py`, `tool_registry/tools/cadquery/materials.py`, `tool_registry/tools/twin/adapter.py`*

### 2.18 Realise + Learn: Device Instances, Measurements + Calibration (FORGE-321)

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

`InterfaceQuantity` (section 2.11, FORGE-313) has no node id of its own -- embedded in `ArchInterface.quantities` inside a `SYSTEM_ARCHITECTURE` WorkProduct. `twin.record_measurement` appends a real `MeasuredValue` there (explicit `work_product_id`, or the project's one such document -- FORGE-313's own one-per-project assumption), and links the measuring `DeviceInstance` via a new `MEASURED_BY` edge to that WorkProduct (the edge names the document holding the quantity; the measurement's own `interface`/`metric` metadata disambiguates which one within it):

```python
async def record(
    *, device_instance_id: str, from_component: str, to_component: str,
    metric: str, value: float, unit: str = "mm", source: str = "", timestamp: str = "",
    work_product_id: str | None = None, project_id: str | None = None,
    predicted_value: float | None = None, predicted_tier: int = 0,
) -> dict
```

Residual computation needs a real predicted value. Rather than trusting a possibly-stale or absent `quantity.predicted` field, the caller supplies `predicted_value` explicitly -- e.g. from a `twin.evaluate_metric` call made just before this one, the natural, honest composition. When the quantity's own `predicted` is still unset, this call also backfills it (the first real prediction a quantity gets is whatever the caller most recently computed); an ALREADY-set `predicted` is never overwritten by a later measurement call. Omitting `predicted_value` records the measurement but skips calibration entirely -- never fabricates a predicted value to diff against.

**Calibration** (`digital_twin/calibration/store.py`, no twin/MCP dependency): a residual is `predicted - measured`, persisted as Evidence (`evidence_type="inspection"`, reusing FORGE-64's mechanism, not a new node type) keyed by `(metric, tier)`. `compute_calibrated_band` turns a list of residuals for one `(metric, tier)` into `band = k * stddev(|residual|)` (`k=2.0` default) once at least 3 samples exist -- below that, returns `None` so the caller's existing fixed prior stays in force, zero regression risk. This is deliberately running-stats, not full conformal prediction: the ticket's own acceptance wording ("the residual narrows the FEA band") only needs a real dispersion estimate that tightens with more measurements, not a coverage-guaranteed interval.

`twin.evaluate_metric` (FORGE-315) now takes an optional calibrated-band lookup: when a calibrated band exists for `(metric="tip_deflection", tier=0)`, it's converted to an equivalent fraction of `limit_mm` and used INSTEAD of the fixed `band_fraction=0.2` prior -- a full override once real measurement history exists, not a blend, since a real calibrated band is a strictly better estimate. The lookup scans EVERY calibration-residual Evidence entity across every project, on purpose: calibration is a prior for the NEXT project, not a per-project cache, matching the ticket's own acceptance wording ("a new project's prediction uses the calibrated band"). The response gains `band_source` (`"fixed_prior"` | `"calibrated"`) and `calibration_sample_count` so a caller can tell which was used.

**Deliberately out of scope**: job 1 of the ticket's own Jira Scope -- committing Gerbers as WorkProducts and a `kicad-cli pcb export pos` pick-and-place adapter method -- was NOT built. The real arm yardstick project has zero PCB/electronics artifacts anywhere in its 34 work products (it's a purely mechanical demo), and the ticket's own Acceptance criterion never exercises job 1 at all (only device registration, measurement recording, and calibration). Building it against synthetic Gerber bytes would be exactly the kind of fabricated demo this epic's own discipline avoids; filed as its own deferred follow-up instead. Also deliberately out of scope: any dashboard UI (no UI surface named in this ticket's own Scope, same precedent as FORGE-315/316/317/319/320).

*Source: `digital_twin/calibration/store.py`, `api_gateway/twin/calibration.py`, `api_gateway/twin/device_instance_recorder.py`, `api_gateway/twin/measurement_recorder.py`, `api_gateway/twin/metric_evaluator.py`, `tool_registry/tools/twin/adapter.py`*

### 2.19 DesignLoopIteration -- Closed Design Loop (FORGE-287)

Its own node type: one candidate value evaluated during one closed design loop run (`twin.start_design_loop`, gap G-G1, target lifecycle spec's "dual state machine: propose -> constraint engine -> commit/reject -> next iteration"). Does not reimplement the search -- composes the already-shipped `twin.optimize_parameter` (section 2.17, FORGE-320) unchanged, which already runs a real propose -> evaluate -> revise -> repeat bisection to convergence or proven infeasibility, with an iteration budget (`max_iterations`, default 60). FORGE-320's own gap was that its trace lived only inside one opaque Evidence `result` blob; this ticket persists EVERY candidate it evaluates as a real, queryable node instead.

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

Deliberately generic field names (`parameter_name`/`parameter_value`/`metric`/`objective_value`), not wall-thickness-specific -- the search itself generalized beyond `wall_thickness_mm` (FORGE-288, gap G-G2) reusing this same node type, no schema migration needed. No `"exhausted"` status: `optimize_wall_thickness` silently accepts its best candidate within `max_iterations` rather than distinguishing "ran out of budget" from "converged" -- inventing that distinction here would claim precision the underlying algorithm doesn't have.

Each candidate is linked to the prior one via `EdgeType.SUPERSEDES` (the same edge FORGE-321's revalidation flow already uses for "this is the newer replacement of that") and to the requirement(s) it was evaluated against via `EdgeType.CONSTRAINED_BY`. The winning candidate is index-identified, not value-matched: `already_feasible_at_min` -> `candidates[0]` (the lower-bound check itself was already feasible), `optimal` -> `candidates[-1]` (`winner = eval_at(hi)` is appended right before the optimizer returns) -- matching by `wall_thickness_mm` VALUE would be wrong, since the final `eval_at(hi)` can legitimately re-evaluate to the exact same float as the immediately preceding loop-appended candidate.

`twin.get_design_loop(loop_id)` returns the full iteration timeline (the dashboard's "iteration timeline" requirement). `twin.approve_design_loop(loop_id, approved_by)` records a human's approval of the converged winner -- the "with a human approving at gates" half of this ticket's own yardstick line; raises when the loop never converged (no winning candidate to approve), the same honesty precedent as every other approval gate in this codebase.

`POST /v1/design-loop/start`, `GET /v1/design-loop/{loop_id}`, `POST /v1/design-loop/{loop_id}/approve` expose the same three actions to the dashboard's Requirements page ("Design loop" section, below the evidence matrix) -- reusing the SAME bound optimizer instance `twin.optimize_parameter` uses, so Evidence/Decision recording isn't duplicated between the MCP tool and the REST route.

**Deliberately out of scope** (see FORGE-287's own PR description for the full split): duplicate-commit guards / infeasibility heuristics beyond what the optimizer itself already returns / cost-budget policy tuning (FORGE-291); wiring gate evaluators to block the loop on evidence gaps (FORGE-290); an eval harness (FORGE-292); true multi-objective/Pareto-front search (FORGE-288's own real scope is single-objective-with-constraints, matching the ticket's own literal example -- see below). The loop mechanism runs synchronously to completion in one call (bisection converges in well under a second) rather than exposing a step-by-step "run one iteration" API -- there is no real mid-search moment a human needs to inspect; the real gate is the end-of-loop approval this ticket already builds.

#### Generalizing beyond wall_thickness_mm (FORGE-288, gap G-G2)

`make_design_loop_starter`'s own `start()` used to read `c["wall_thickness_mm"]`/`c["mass_kg"]`/`c["deflection_margin_mm"]`/`c["sf_margin"]` directly off each raw candidate dict -- real, but only ever matching `make_wall_thickness_optimizer`'s own candidate shape, contrary to `DesignLoopIteration`'s own already-generic field names. `make_design_loop_starter` gained `parameter_name`/`metric`/`candidate_mapper` (a `dict -> (parameter_value, constraints_status)` function) to make that ingestion generic, defaulting to the EXACT prior wall-thickness mapping -- every existing caller (the MCP tool, the REST route) is unaffected; the full pre-existing `TestMakeDesignLoopStarter` suite passes unchanged as the regression proof.

Proven with a genuinely SECOND real optimizer, not a rename: `optimize_tube_height`/`make_tube_height_optimizer` (`twin_core/prediction/optimizer.py`, `api_gateway/twin/optimizer.py`) sweep `height_mm` with `wall_thickness_mm` held fixed, over the SAME hollow-tube hand-calcs `optimize_wall_thickness` already uses. Monotonic bisection is exactly as sound here as over wall thickness: for fixed wall thickness, increasing height strictly raises the section's moment of inertia (cubic in height) -- strictly lowering deflection and bending stress -- while ALSO strictly increasing cross-sectional area (the outer width exceeds the inner cavity's width for any positive wall thickness), so the minimum-mass feasible point is exactly the smallest height where both constraints first hold. `twin.start_tube_height_design_loop` (MCP tool) plugs it into the identical persistence/read/approve machinery via `make_design_loop_starter(twin, optimize=make_tube_height_optimizer(twin, ...), parameter_name="height_mm", candidate_mapper=tube_height_candidate_mapper)` -- `twin.get_design_loop`/`twin.approve_design_loop` need no tube-height-specific variant; they already operate on any `DesignLoopIteration` by `loop_id`.

The dashboard's "objective vs iteration chart" (its own Jira wording) is a small hand-rolled inline-SVG sparkline (`IterationSparkline`, `dashboard/src/pages/RequirementsPage.tsx`) added to the existing Design Loop section -- no chart library exists in this codebase, and a true Pareto-front multi-objective view is aspirational Jira framing for what the ticket's own literal example ("minimise mass subject to SF>=2 and deflection<=0.5mm") is actually single-objective-with-constraints. The sparkline and the iteration table's own column headers now read `parameter_name`/`metric` off the fetched iterations rather than hardcoding "wall_thickness_mm"/"mass_kg" text, so they render correctly for either loop kind without any dashboard-side branching.

*Source: `twin_core/models/design_loop_iteration.py`, `twin_core/prediction/optimizer.py`, `api_gateway/twin/design_loop.py`, `api_gateway/twin/optimizer.py`, `api_gateway/design_loop/routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.20 Decision records: evidence links + dashboard cards (FORGE-289, gap G-G3)

`twin.record_decision` (MET-495) already persisted a `WorkProductType.DESIGN_DECISION` work product with real `alternatives` (structured option/reason-rejected pairs, never prose) and real `parent_refs` edges (FORGE-61) -- the ticket's actual gap ("Current state: Weak (decisions exist, often without evidence links)") was that nothing connected a Decision to the Evidence that backed it: the rationale string could *mention* a calculation, but no graph edge let a reader or a gate evaluator walk from one to the other.

`record_decision` gained an `evidence_refs: list[str] | None` parameter, resolved through the same `_ref_resolver.py` every other recorder in this package uses and linked via `EdgeType.SUPPORTED_BY` (previously declared, never used) -- a distinct relation from `parent_refs`' own `satisfies`, since "supported by evidence" and "satisfies a requirement" are different facts. `make_wall_thickness_optimizer`/`make_tube_height_optimizer` (section 2.17) now pass `evidence_refs=[out["evidence_node_id"]]` into their existing `decision_recorder(...)` call, so every design-loop-produced Decision links to its own Evidence for free.

The design loop itself (section 2.19) gained one more edge: when `start()` produces both a winning `DesignLoopIteration` and a Decision, it links them via `EdgeType.GENERATED_FROM` (also previously declared, never used) -- `decision -[GENERATED_FROM]-> winning_iteration`. A reader can now walk a converged loop's winner straight to the Decision it produced.

`GET /v1/decisions?related_to=<node_id>` (`api_gateway/twin/decision_routes.py`) answers "what decisions touch this node" for any node with an *incoming* edge from a Decision -- a hierarchy node (via `parent_refs`), an Evidence entity (via `evidence_refs`), or a `DesignLoopIteration` (via the new `GENERATED_FROM` link). It walks `twin.get_edges(node_id, direction="incoming")` and keeps only edges whose source resolves to a `DESIGN_DECISION` work product, filtering by source type rather than by a fixed edge-type allowlist -- `parent_refs`' own `relation` is caller-configurable (default `satisfies`, not fixed), so an edge-type filter would silently miss a Decision linked with a non-default relation. No new twin_core method was needed.

Dashboard: a shared `DecisionList`/`DecisionCard` component (`dashboard/src/components/shared/DecisionList.tsx`, no new dependency) renders at both places the gap's own dashboard-interaction line named -- "Decision cards linked from hierarchy nodes and iterations": the Structure tab (`StructureView.tsx`) shows decisions related to the selected hierarchy node in a local panel (tracked independently of the tab's existing selectedNode/inspector plumbing, which assumes a WorkProduct/CAD node shape a HierarchyNode doesn't have), and the Requirements page's Design Loop section (section 2.19) shows decisions related to a converged loop's winning iteration.

**Deliberately out of scope**: auto-linking a Decision to a HierarchyNode from the design-loop flow itself -- the loop has no `hierarchy_node_id` input today, and wiring one through is a separate, larger change to its signature. The generic `twin.record_decision(parent_refs=[hierarchy_node_id])` path already supports manual/agent-driven linking, sufficient to demonstrate on the arm project per this ticket's own definition of done.

*Source: `api_gateway/twin/decision_recorder.py`, `api_gateway/twin/optimizer.py`, `api_gateway/twin/design_loop.py`, `api_gateway/twin/decision_routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/components/shared/DecisionList.tsx`, `dashboard/src/components/viewer/StructureView.tsx`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.21 Loop quality guards: duplicate detection + iteration budget (FORGE-291, gap G-G5)

The ticket bundles four things under "loop quality guards"; two were already real before this ticket. Grounding (FORGE-98, a chat-harness safeguard flagging a reply that claims a design action with zero tool calls that turn) is general and unrelated to the design loop specifically -- confirmed already shipped, untouched here. Infeasibility detection is also already real: `status="infeasible"` (section 2.19, FORGE-287) already exists and is already persisted/returned; this ticket added no new mechanism for it, only dashboard visibility (below).

The two real, previously-unshipped gaps were a duplicate-commit guard and iteration-budget visibility:

**Duplicate-commit guard.** `DesignLoopIteration` gained `loop_inputs_hash: str | None` -- a sha256 of `{work_product_id, **optimize_kwargs}` (sorted-key JSON, `record_decision` excluded since it toggles a side effect rather than changing the search), stamped on every iteration of a run. Same MET-506 "identical inputs = the same real-world thing" precedent `decision_recorder.py` already established for Decision records, applied here since a design loop has no single work-product node of its own to hash content against. Before running the bisection, `start()` calls the new `twin.find_design_loop_by_inputs_hash(loop_inputs_hash)` (a one-line `list_nodes(node_type=DESIGN_LOOP_ITERATION, filters={"loop_inputs_hash": ..., "iteration_number": 0})`, mirroring `list_design_loop_iterations`'s own shape) -- a hit means this exact loop already ran, and `start()` returns that prior loop's already-persisted result (`{status, winner, loop_id, iteration_count, iteration_ids, duplicate: true}`) instead of spending a bisection and a whole new iteration subtree on a re-submission. The original loop's `status` (`optimal` vs. `already_feasible_at_min` vs. `infeasible`) is reconstructed from the stored iterations alone -- no winner means infeasible; a winner at `iteration_number == 0` means already-feasible-at-min; otherwise optimal -- exactly mirroring the same index-based logic `start()` already uses to mark the winner in the first place (section 2.19).

**Iteration-budget visibility.** `max_iterations` (the bisection's real budget, already a parameter on `twin_core.prediction.optimizer.optimize_wall_thickness`/`optimize_tube_height`, default 60) is now threaded through `api_gateway/twin/optimizer.py`'s wrapper functions and echoed back in the response alongside the already-present `iteration_count`. "Tokens" (the ticket's own dashboard wording, "Loop health panel (tokens, iterations, failures)") is dropped rather than implemented: this loop makes zero LLM calls -- pure deterministic bisection math -- so there is no real per-loop token cost to attribute; `iteration_count`/`max_iterations` is the honest, measurable budget a dashboard can actually show.

Dashboard: `LoopHealthPanel` (`dashboard/src/pages/RequirementsPage.tsx`) renders in the Design Loop section right after a run -- a status pill, `iteration_count / max_iterations`, and a "duplicate of an earlier run" badge when the guard fired. Built from the mutation's own last result rather than the persisted iteration timeline, since `max_iterations`/`duplicate` are properties of one invocation, not persisted loop state -- there is nothing honest to show after a page reload.

**Deliberately out of scope**: any token/cost tracking on the design loop (no real LLM calls inside it to measure); rebuilding infeasibility detection (already shipped, FORGE-287); a generic cross-tool "duplicate call" framework (scoped narrowly to the design loop, matching MET-506's own narrow, per-recorder precedent rather than inventing shared middleware).

*Source: `twin_core/models/design_loop_iteration.py`, `twin_core/api.py`, `api_gateway/twin/design_loop.py`, `api_gateway/twin/optimizer.py`, `api_gateway/design_loop/routes.py`, `tool_registry/tools/twin/adapter.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.22 Concept generation and trade study (FORGE-262, gap G-B2)

`twin_core.consistency.gates`'s G5 (Concept Selection) already evaluated whatever it found: "Trade study performed" is a per-decision PASS once a recorded `twin.record_decision` has a non-empty `alternatives` list -- that module's own docstring says explicitly it never builds the "Decision Agent" (target lifecycle spec section 26.12) that would GENERATE alternatives, run the trade study, and select one. This ticket is exactly that missing half, and needed almost no new machinery.

**Concept generation** needed no new tool at all. A "candidate architecture" is a `concept_option` `EngineeringEntity` (a new `EngineeringEntityType` member alongside the existing `intent`/`objective`/... set, recorded via the already-generic `twin.record_engineering_entity`) -- its `metadata` carries `criteria_scores` (e.g. `{"mass_kg": 1.8, "cost_usd": 340, "risk": 3, "performance": 7}`) and `evidence_backed_criteria` (which of those keys came from a real measured source rather than an assertion). Only mass has a real measured source anywhere in this codebase today -- a committed CAD work product's own `metadata.geometry_features.properties.mass_kg`; cost has no real source (no price field exists in `tool_registry.tools.cadquery.materials`, and no live distributor lookup was wired in for this ticket), and risk/performance are inherently subjective in general. This module reports which criteria are asserted rather than pretending otherwise -- the dashboard visually distinguishes a grounded cell from an asserted one, same discipline FORGE-318's evidence matrix already uses for `no_data` vs `pass`.

**The trade study + selection** (`api_gateway/twin/trade_study.py`) is the one genuinely new piece. `weighted_score`/`score_concept_options` are pure functions computing `sum(weight * criteria_scores[criterion])` over the caller-supplied weights (sign encodes minimize/maximize -- a criterion to minimize needs a negative weight so a higher weighted_score always means better). `make_trade_study_selector`'s `select(...)` fetches the named `concept_option` entities, scores them, and records the selection as a real `twin.record_decision` -- reusing that mechanism completely unchanged rather than inventing a second "Decision-like" node type (same precedent FORGE-289's own docs already established). Every non-selected option becomes one real `alternatives` entry with its weighted score as `reason_rejected` -- not prose, and not empty, so G5's "Trade study performed" check now actually passes instead of reporting `NOT_EVALUATED` (confirmed live: recording two options, calling `twin.select_concept`, and re-evaluating G5 on the same project flips that exact check from `NOT_EVALUATED` to `PASS`). The Decision is also linked to the selected option via `EdgeType.GENERATED_FROM` (previously declared, reused by FORGE-289's design-loop-winner precedent for the identical "this Decision was generated from evaluating this real node" relationship) -- no new edge type either.

New MCP tool `twin.select_concept` (concept GENERATION stays `twin.record_engineering_entity` with `entity_type="concept_option"` -- there is no deterministic "architecture generator" algorithm to build, and none is specified anywhere in the Planner spec). `GET /v1/trade-study/options`, `POST /v1/trade-study/options`, `POST /v1/trade-study/select` expose the same three actions to the dashboard's "Concept trade study" section (below the feature library) -- reusing the SAME bound `engineering_entity_recorder`/`concept_selector` callables `bootstrap_tool_registry` wires up, so a concept_option or Decision created via the dashboard and via an agent's own MCP call are indistinguishable afterward.

Dashboard: a table with criteria as rows, options as columns, an editable weight per criterion row (weighted scores recompute client-side from the already-fetched `criteria_scores` as weights change -- only the FINAL committed weights get baked into the Decision's rationale at selection time), a "+ Add option" form, and a "Select concept" action requiring a rationale.

**Deliberately out of scope**: a live-editable weight grid with per-cell evidence re-fetch (weights are client-side state, recomputed from data already fetched once); an LLM-driven "generate candidate architectures" action (generation stays a chat-agent/CLI action recording `concept_option` entities one at a time); grounding risk/performance in any real measured source (none exists in general).

*Source: `twin_core/models/engineering_entity.py`, `api_gateway/twin/engineering_entity_recorder.py`, `api_gateway/twin/trade_study.py`, `api_gateway/trade_study/routes.py`, `tool_registry/tools/twin/adapter.py`, `tool_registry/bootstrap.py`, `mcp_core/annotations.py`, `dashboard/src/pages/RequirementsPage.tsx`*

### 2.23 Requirement-driven component selection (FORGE-265, gap G-C1)

`twin.record_component_selection` (`api_gateway/twin/component_recorder.py`, MET-436 follow-up) has always been presence-only: it persists whichever candidate part a caller already decided on as a `BOMItem`, with zero comparison against what the design actually needs -- no margin, no rationale trail, no Decision. This ticket is the missing "requirement-driven" half, in a new sibling module (`api_gateway/twin/component_selection.py`) rather than a mode bolted onto `trade_study.py`, because the comparison shape is different: a servo's rated torque clearing a required torque with margin is a per-spec threshold/pass-fail check with a real numeric distance, not `trade_study.py`'s weighted-sum score across criteria.

`check_spec_margin(required, actual, op)` is a pure function comparing one real candidate spec value against one required threshold (`">="` or `"<="`); `margin` is always signed so a positive value means "passes with this much headroom" regardless of direction. `check_candidate_against_requirements(specs, required_specs)` runs it for every named requirement against one candidate's specs, reporting a missing spec as a failing, marginless check (distinguishable from "spec present but fails") rather than silently skipping it.

`make_component_selector(twin, *, decision_recorder, component_recorder)`'s `select(...)` takes 2+ candidates (each an mpn + caller-asserted `specs: dict[str, float]`, e.g. `{"torque_kg_cm": 25.0}`), the `required_specs` the design needs (`{"torque_kg_cm": {"op": ">=", "value": 20.0}}`), and which `selected_mpn` was chosen. It computes every candidate's margins, records the selection as a real `twin.record_decision` -- reusing that mechanism completely unchanged (same precedent as `trade_study.py`) -- with every non-selected candidate's margin summary as its `alternatives` entry, then persists the *selected* candidate as a real `BOMItem` via the existing `component_recorder` (no new persistence path). The Decision is linked to the new BOMItem via `EdgeType.GENERATED_FROM` (same "this Decision was generated from evaluating this real node" relationship FORGE-289/FORGE-262 already established). Unlike a hard gate, a candidate that fails a requirement can still be selected -- `selected_meets_requirements` surfaces the outcome honestly rather than the tool refusing; an engineer may have a documented reason (e.g. reduced duty cycle) to accept it anyway.

New MCP tool `twin.select_component`. `POST /v1/component-selection/select` exposes the same action to the dashboard's BOM-page "Select component" action, reusing the SAME bound `component_selector` callable `bootstrap_tool_registry` wires up. Unlike `trade_study`'s `GET`/`POST /options`, there is no candidate-listing route here -- a candidate is an mpn + specs supplied inline at selection time, not a pre-recorded graph entity to browse beforehand.

**Honesty note** (same discipline `trade_study.py`'s own docs already established for concept-option criteria scores): the specs compared here are **caller-asserted real datasheet values** -- a human or agent reads a real, published number off a real datasheet and types it in. Nothing in this codebase parses a datasheet PDF or scrapes a distributor page for these numbers; that ingestion pipeline does not exist. `digital_twin/catalog/taxonomy.py`'s `CATEGORY_REGISTRY` already declares typed, range-queryable fields for categories like `servo` (`torque_kg_cm`, `voltage_range`) and `motor_driver` (`v_in_max`, `i_out_max`), but has no seeded sample data -- a live selection needs real numbers supplied at call time.

**Deliberately out of scope**: any real datasheet-parsing/ingestion pipeline (none exists anywhere in this codebase; a separate, much larger effort); routing the margin check through `twin_core.constraint_engine` (that engine evaluates work-product-scoped `Constraint.expression` strings via `eval()` against a graph-derived context -- the wrong shape and over-scoped for a lightweight per-candidate numeric comparison).

*Source: `api_gateway/twin/component_selection.py`, `api_gateway/component_selection/routes.py`, `tool_registry/tools/twin/adapter.py`, `tool_registry/bootstrap.py`, `mcp_core/annotations.py`, `digital_twin/catalog/taxonomy.py`, `dashboard/src/pages/BomPage.tsx`*

### 2.24 COTS parts as geometry: "replace placeholder with part" (FORGE-266, gap G-C2)

`twin.record_hierarchy_node` (FORGE-260) has only ever set a HierarchyNode's `REALIZED_BY` (a `cad_model` work product) and `INSTANCE_OF` (a `BOMItem`) edges once, at node-creation time -- there was no way to attach or swap a node's real geometry afterward. This gap turned out to be the *only* genuinely missing piece: the STEP-ingestion pipeline (`POST /v1/twin/import`, MET-483 -- real bounding box/part-count extraction via the OCCT converter, already dashboard-usable via the existing `ImportZone` component), the 3D preview pipeline (`GET /v1/twin/nodes/{id}/model`, already GLB-converts any `cad_model` work product), and the hierarchical-BOM derivation that reads these exact edges (`twin_core.consistency.hierarchical_bom`, FORGE-267) all already existed and needed zero changes.

`api_gateway/twin/hierarchy_recorder.py`'s new `make_hierarchy_geometry_linker(twin)` returns `realize(*, hierarchy_node_id, work_product_id=None, bom_item_id=None)` -- at least one of `work_product_id`/`bom_item_id` is required, both may be given. For whichever is supplied, it REMOVES any existing outgoing edge of that type from the node first (`_replace_outgoing_edge`), then adds the new one -- a node always has at most one `REALIZED_BY` and at most one `INSTANCE_OF` target, never an accumulating history. Both the target work product/BOMItem and the hierarchy node itself are confirmed to exist before linking (`ValueError` otherwise). Because this reuses the exact edges `compute_hierarchy_rollup` and `compute_hierarchical_bom` already read, a node realized here shows up in both with zero further changes.

New MCP tool `twin.realize_hierarchy_node`, classified **DESTRUCTIVE** (not ADDITIVE like `twin.record_hierarchy_node`) since it genuinely removes a prior edge, not just appends -- "replace placeholder with part" is exactly the kind of prior-state-losing decision that classification exists for. `POST /v1/twin/hierarchy/{node_id}/realize` exposes the same action to the dashboard, reusing the SAME bound callable. `GET /v1/twin/hierarchy`'s `HierarchyNodeResponse` gained `realizedByWorkProductId`/`instanceOfBomItemId` (both `null` means "placeholder") so the dashboard knows which nodes need this action.

Dashboard: `StructureView.tsx`'s tree rows gained a "Replace placeholder"/"Replace part" action opening an inline panel with two paths -- upload a real STEP file (reuses `ImportZone` unchanged; on success, a real GLB preview renders via `GET /v1/twin/nodes/{id}/model` in a small self-contained `<Canvas>` scoped to just this panel, not the full twin-viewer's own `R3FViewer`/store, mirroring the same "own small Canvas" pattern `UrdfPreviewPanel.tsx` already uses) or pick an already-recorded `BOMItem` from the project's flat BOM (no 3D preview for this path -- a `BOMItem.cad_model_url` is a caller-asserted external link this pipeline has no fetch-and-convert path for; its own real mpn/manufacturer fields are the honest "preview" here).

**Honesty note**: no real vendor STEP files exist anywhere in this repo to seed a demo with (`tool_registry/tools/occt-converter/`'s own sample assets are synthetic generator shapes, not real vendor parts) -- a live demo needs a real, genuinely-downloadable STEP file supplied at call time, same "caller-asserted real value" discipline FORGE-262/265 already established for criteria scores and datasheet specs.

**Deliberately out of scope**: any vendor STEP-file library, scraper, or "search vendor sites for a STEP model" feature; fetching/converting an arbitrary external `cad_model_url` on demand (a `BOMItem`'s own `cad_model_url` stays a provenance link, never something this pipeline fetches); a swiftshader/headless-WebGL Playwright rendering harness (no precedent exists anywhere in this codebase to build on; the E2E test covers the pick-existing-BOMItem path, which needs no 3D rendering, and the upload+GLB-preview path is exercised by live validation instead).

*Source: `api_gateway/twin/hierarchy_recorder.py`, `api_gateway/twin/hierarchy_routes.py`, `tool_registry/tools/twin/adapter.py`, `tool_registry/bootstrap.py`, `mcp_core/annotations.py`, `dashboard/src/components/viewer/StructureView.tsx`*

---

### 2.25 Robot pose presets: drag-to-pose + saved poses (FORGE-250, gap G-J4)

A `robot_description` work product's `metadata` gained one new optional field:

```
metadata.poses: {
  [poseName: string]: { [jointName: string]: number }  // radians (revolute/continuous) or mm (prismatic)
}
```

No new node type, no new MCP tool, and no new gateway route -- "Save current pose" reuses the existing generic `POST /v1/twin/nodes/{id}/iterate` revision endpoint (MET-251) exactly as-is: `metadata_updates={"poses": {...existing, <name>: <values>}}` merges one named pose into whatever's already saved (never dropping the others) and, because `/iterate` always appends a `WorkProductRevision`, the save shows up in the node's version history for free -- the same "persist + version" behavior `api_gateway/twin/routes.py`'s `update_assembly_joints` route already established for a different `robot_description` metadata field (`metadata.assembly`). `TwinNodeResponse.poses` (populated in `_wp_to_response` from `wp.metadata.get("poses")`) surfaces it back to the dashboard on every node fetch, `None` for every node type or a `robot_description` with no saved poses yet.

The three other pieces of FORGE-250 are entirely client-side, reading/writing the existing `robotJointValues` slice on `viewer-store.ts` (unchanged since MET-747/FORGE-283) rather than any new server state:

- **Drag-to-pose**: `dashboard/src/lib/robot-drag-controls.ts`'s `RobotPoseDragControls` subclasses `urdf-loader`'s own `PointerURDFDragControls` (raycast + joint-axis projection + URDF-limit clamping, all already implemented there) to report the resulting joint value back into the store and highlight the dragged link's meshes. Mounted by `RobotSceneContents.tsx` only while kinematic control is active (`!robotPhysicsEnabled` -- physics mode drives joints itself); an imperative `viewer-store` bridge (`setOrbitControlsEnabled`, same registration pattern as `_cameraResetFn`) suspends `<OrbitControls>` for the drag's duration so the camera doesn't orbit mid-manipulation.
- **Built-in presets**: Zero, Home (the midpoint of each joint's exported range -- no separate canonical "home" pose exists anywhere in this codebase to read instead), Min, Max -- pure functions of the already-known joint list (`dashboard/src/lib/robot-poses.ts`), nothing to persist.
- **Animated transitions**: a ~600ms ease-in-out interpolation (`runPoseAnimation`/`interpolatePose`/`easeInOutCubic`) driving the same `setRobotJointValue` calls a manual slider drag would, so a preset-selected pose animates in exactly like MET-747's existing kinematic-posing effect already expects.

**Deliberately out of scope** (see FORGE-250's own ticket "Out of scope (separate stories)" line, and FORGE-303's follow-up comment): inverse kinematics (dragging the end-effector, solving every upstream joint to reach it) -- no IK solver exists anywhere in this codebase, only the forward "analytic live-solve kinematics" in `api_gateway/constraint/kinematics.py`; posing from a natural-language chat command; trajectory playback (animating through a *sequence* of poses over time) -- depends on FORGE-284's not-yet-built multibody-dynamics/trajectory work, itself flagged in Jira as "not in the Phase 3 stack" per MetaForge-Planner.

*Source: `dashboard/src/lib/robot-poses.ts`, `dashboard/src/lib/robot-drag-controls.ts`, `dashboard/src/components/viewer/RobotSceneContents.tsx`, `dashboard/src/components/viewer/RobotControlsOverlay.tsx`, `dashboard/src/components/viewer/R3FViewer.tsx`, `dashboard/src/store/viewer-store.ts`, `dashboard/src/hooks/use-twin.ts`, `dashboard/src/api/endpoints/twin.ts`, `api_gateway/twin/schemas.py`, `api_gateway/twin/routes.py`*

---

## 3. Edge Types

Edges are directed relationships between nodes. Each edge type has defined source and target node types.

| Edge Type | Source -> Target | Description |
|-----------|-----------------|-------------|
| `DEPENDS_ON` | WorkProduct -> WorkProduct | WorkProduct A requires WorkProduct B (e.g., PCB depends on schematic) |
| `IMPLEMENTS` | WorkProduct -> WorkProduct | WorkProduct A implements the spec defined in WorkProduct B |
| `VALIDATES` | WorkProduct -> WorkProduct | WorkProduct A (test result) validates WorkProduct B (design) |
| `CONTAINS` | WorkProduct -> WorkProduct, or HierarchyNode -> HierarchyNode | Hierarchical composition. FORGE-260: `metadata` carries `{quantity, placement}` when nesting one product-hierarchy position inside another |
| `VERSIONED_BY` | WorkProduct -> Version | Links an work_product to the version that last modified it |
| `CONSTRAINED_BY` | WorkProduct -> Constraint | Constraint applies to this work_product |
| `PRODUCED_BY` | WorkProduct -> Agent | WorkProduct was produced or modified by this agent |
| `USES_COMPONENT` | WorkProduct -> Component | WorkProduct references this component (e.g., BOM uses resistor) |
| `PARENT_OF` | Version -> Version (also used for WorkProduct -> WorkProduct provenance, e.g. a robot_description's source cad_model parts) | Version lineage / source-artifact provenance |
| `CONFLICTS_WITH` | Constraint -> Constraint | Two constraints that cannot both be satisfied |
| `REALIZED_BY` | HierarchyNode -> WorkProduct | FORGE-260: a hierarchy position's real cad_model/robot_description geometry |
| `INSTANCE_OF` | HierarchyNode -> BOMItem, or DeviceInstance -> WorkProduct | FORGE-260: a COTS leaf position is an instance of one canonical component record. FORGE-321: a manufactured unit is an instance of the design revision it was built from |
| `MEASURED_BY` | DeviceInstance -> WorkProduct | FORGE-321: a real-world measurement from this unit was recorded against an interface quantity embedded in this system_architecture WorkProduct |

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

### Two VersionEngine backends: graph for semantics, git for working history (MET-630)

`InMemoryVersionEngine` above reimplements a commit DAG, branch pointers, and three-way merge inside the graph — and since it only ever tracks opaque `content_hash` strings, it can never say more than "this work product's content changed." For CAD work products in particular, that means it can never show what changed.

`GitVersionEngine` (`twin_core/versioning/git_backend.py`) implements the *same* `VersionEngine` protocol but delegates `create_branch`/`commit`/`merge`/`diff`/`log` to a real git repository (via `subprocess` — no GitPython, no new dependency). The split in responsibility is deliberate:

- **The graph stores semantics.** Structured, queryable properties — extracted geometric parameters (e.g. `pad_length_mm`, `hole_diameter_mm`) and derived properties (volume, bounding box, mass properties) — live directly on a `WorkProduct` node's `metadata["geometry_features"]`, populated at commit time (see `api_gateway/twin/geometry_recorder.py`). This is what `twin_query_cypher` and the constraint engine reason over.
- **Git owns the working, diffable history.** When a CAD work product was authored from a real CadQuery/FreeCAD generation script, that script is committed as its own `CAD_SOURCE_SCRIPT` work product, git-versioned as the actual source of truth, and linked to the resulting `CAD_MODEL` node via a `PARENT_OF` provenance edge — the same edge type `boolean_ops.py` already uses for derivation lineage. `git diff`/`git merge` on that script produce genuine text diffs and real three-way merges (non-overlapping edits auto-resolve) instead of hash comparisons. Imported/vendor geometry with no generation script has no script to version — it stays content-hash-only, same as before.

Selection is per-project: `GitRepoRegistry` (`api_gateway/twin/git_repo_registry.py`) lazily constructs one `GitVersionEngine` per `project_id`, rooted under `METAFORGE_VERSION_GIT_ROOT/projects/<project_id>/`, so independent projects never share git history. `InMemoryVersionEngine` remains the default for graph-only/ephemeral use (dev, tests); a project only gets a real git repo when `METAFORGE_VERSION_GIT_ROOT` is configured.

> **Known gap**: MetaForge projects don't yet have a dedicated filesystem workspace (a project is still Postgres metadata + a `project_id` graph attribute) — `GitRepoRegistry` partitions by `project_id` under one configured root as an interim measure, not a true per-project workspace directory (tracked as a MET-630 follow-up).

A commit's `paths` argument gives a work product a *stable* location (e.g. `mechanical/cad_src/bracket.py`, derived from the part's name) rather than the default `work_products/<id>`. This matters because `geometry_recorder.py` gives every regeneration a fresh graph node id — without a stable path, each regeneration would write to a brand-new git path instead of evolving one file, and `git log`/`git diff` on "this part's history" would only ever show a single commit. `geometry_recorder.py` always passes a name-derived path for this reason.

### Regenerating geometry: parameter view/edit → propose → apply (MET-630)

Closing the loop from "a parameter is stored in the graph" to "a human can change it and see it take effect":

- `GET /v1/twin/nodes/{id}` returns `geometryParameters` (the node's `geometry_features`, unflattened — kept separate from the generic scalar-only `properties` map) and `hasScript` (whether a git-versioned generation script backs this node).
- `GET /v1/twin/nodes/{id}/script` returns the node's current script text, read live from its project's git repo — used to seed an edit form with what's there today rather than asking a human to retype it.
- `POST /v1/assistant/proposals` lets a human create a `DesignChangeProposal` directly (previously only an agent's `twin.propose_change` MCP call could) — e.g. `{"diff": {"action": "regenerate_geometry", "script_source": "<edited script>", "parameters": {...}, "cad_tool": "cadquery"}}`.
- On approval, the apply executor's `regenerate_geometry` action (`api_gateway/twin/regenerate_geometry.py`) is no longer a no-op, and supports **both** CAD scripting tools this codebase generates from — they work fundamentally differently, so `diff.cad_tool` states which one `script_source` is written in (never auto-detected):
  - `"cadquery"` (default): one self-contained call — `cadquery.execute_script` writes a STEP file to the shared `ADAPTER_WORKSPACE_DIR` volume the gateway and adapter containers share (mirrors `boolean_ops.py`).
  - `"freecad"`: a stateful session lifecycle — `open_session` → `execute_code` (runs against that session's live document, returns an `obj_id`, not a file) → `export_model` (session_id + obj_id → `step_base64` returned directly) → `close_session`. FreeCAD is in fact the more commonly used tool in this codebase (`geometry_recorder.py`'s own default is `source_tool="freecad.export_model"`), so this path matters more than the CadQuery one it shipped alongside first.

  Either way, the result commits through the same `geometry_recorder` path — so the new node gets git-versioned script history, `geometry_features` metadata, *and* a `SUPERSEDES` link to the part it replaces (below).

### Regeneration history in the graph: SUPERSEDES chains (MET-630)

Every `commit_geometry` call — not just ones going through the apply-on-approve path — links successive generations of the *same named part* (matched by `project_id` + `name`) via a `SUPERSEDES` edge (new → old), mirroring `TwinAPI.ingest_datasheet`'s existing pattern for datasheet revisions. This applies to both the `CAD_MODEL` node and its `CAD_SOURCE_SCRIPT` node independently. Without `project_id`, there's no reliable identity to match on, so unscoped commits are never linked.

### Editing a parametric feature's own parameters (FORGE-270, gap G-D2)

The `regenerate_geometry`/`script_source` flow above (MET-630) is one real editing path -- edit a *script*, propose, apply. FORGE-269's feature library (§2.19-adjacent, `generate_parametric_feature`) generates from *typed macro parameters* instead of a script, so "edit" means something more specific there: call `generate_parametric_feature` again with the SAME `name` and a CHANGED parameter value. `generate_cad_ir` (the skill every feature ultimately lowers through) gained a `parameters: dict[str, Any] | None` field, threaded straight into `twin.commit_geometry`'s own `parameters` argument (real, already part of `geometry_recorder.py`'s signature since MET-630, unused by any caller before this ticket) -- landing in the new node's `metadata.geometry_features.parameters`, alongside a `feature_type` key so a later caller knows which macro produced it. The existing content-hash dedup + same-name `SUPERSEDES` matching documented just above does the rest: no new versioning mechanism, no ECT.

`GET /v1/features/{work_product_id}/diff` (`api_gateway/features/routes.py`) walks that one `SUPERSEDES` edge back and diffs the two nodes' `geometry_features.parameters`, reusing `api_gateway.twin.version_schemas.FieldDelta` for the diff shape (the same one `VersionService.diff` already uses for per-node `_revisions` diffs -- a different, per-node mechanism, not conflated with this cross-node one) rather than inventing a second. 404s when the node has no prior version (the common case -- most work products are generated once). The dashboard's Twin Viewer "History" tab renders it (`FeatureVersionSection`, `dashboard/src/pages/TwinViewerPage.tsx`) right alongside the existing per-node revision history, when one exists.

**Deliberately out of scope**: widening `ControlledEntityKind` (`twin_core/transactions/patch.py`) to cover `work_product` so an edit could go through a real ECT proposal/approval cycle instead of an immediate re-commit -- the same nontrivial, separate transaction-engine widening FORGE-320 already deferred; a "propose, then apply" gate for macro-parameter edits (unlike script edits) would need it. Reviving `twin_core.versioning`'s branch/git `VersionEngine` for this -- confirmed zero consumers outside its own tests, a much larger lift than this ticket's real, minimal gap needed.

---

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

Phase 1 implements the full schema defined in this document. The following node types and edge types are required for the mechanical vertical (MET-8):

- **Nodes**: WorkProduct (CAD_MODEL, SIMULATION_RESULT), Constraint, Version, Component, Agent
- **Edges**: DEPENDS_ON, CONSTRAINED_BY, PRODUCED_BY, VERSIONED_BY, USES_COMPONENT

### Phase 2 Additions

- Additional WorkProductType values for electronics (SCHEMATIC write support)
- Extended Component specs for electronics parts (voltage rating, current rating, ESR)
- New edge type: `ROUTED_TO` (net-to-pad routing in PCB)

### Phase 2 AAS Additions

- `BOMItem` node type with `global_asset_id` and `supplier` properties (MET-160, MET-161)
- `DesignElement` node type with AAS-aligned `parameters` keys (MET-162)
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

### A.3 `DesignElement.parameters` Recommended Keys

| Key | Type | AAS Submodel | Example |
|-----|------|-------------|---------|
| `hardwareVersion` | String | Digital Nameplate | `"rev-C"` |
| `softwareVersion` | String | Digital Nameplate | `"1.2.0"` |
