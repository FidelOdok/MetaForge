"""Shared enumerations for the Digital Twin graph schema."""

from enum import StrEnum


class NodeType(StrEnum):
    """Discriminator for graph node types."""

    WORK_PRODUCT = "work_product"
    CONSTRAINT = "constraint"
    VERSION = "version"
    COMPONENT = "component"
    AGENT = "agent"
    BOM_ITEM = "bom_item"
    DEVICE_INSTANCE = "device_instance"
    TWIN_MODEL = "twin_model"
    DESIGN_ELEMENT = "design_element"
    # MET-430: structured manufacturer datasheet (PDF + extracted rows)
    DATASHEET = "datasheet"


class WorkProductType(StrEnum):
    """Types of design work products tracked in the Digital Twin."""

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
    # MET-495: a recorded design decision (ADR-style), rendered to markdown
    # and persisted as a first-class work product via twin.record_decision.
    DESIGN_DECISION = "design_decision"
    # MET-630: the CadQuery/FreeCAD parametric generation script that
    # authored a CAD_MODEL — the real source of truth, git-versioned for
    # diffing. Linked to the CAD_MODEL it produced via a PARENT_OF edge.
    CAD_SOURCE_SCRIPT = "cad_source_script"
    # MET-740: a URDF/SDF/USD robot description exported from one or more
    # CAD_MODEL parts + a joint list. Linked to each source part via a
    # PARENT_OF edge (mirrors CAD_SOURCE_SCRIPT's script->geometry edge).
    ROBOT_DESCRIPTION = "robot_description"
    # A self-contained HTML reference sketch (proportions/topology/range-of-
    # motion preview) authored BEFORE committing to real CAD/build work --
    # the human-approval gate the `decide_sketch_needed` mechanical skill
    # triggers for non-trivial or revision builds. Linked via a PARENT_OF
    # edge to whatever existing work product it reviews (revision case);
    # not linked to anything yet for a brand-new design (nothing built to
    # link to). `metadata.approved`/`metadata.approved_at` track the gate.
    DESIGN_SKETCH = "design_sketch"
    # Lifecycle-stage additions (follow-up to MET-747's CAD-lifecycle mapping):
    # a hazard/risk log (hazard, cause, effect, severity x likelihood ->
    # risk score, mitigation) produced by the compliance domain's
    # `analyze_hazards` skill.
    HAZARD_ANALYSIS = "hazard_analysis"
    # A cross-discipline component/interface map (block diagram + interface
    # table) produced by the `define_system_architecture` skill -- captures
    # "what talks to what" before detailed design starts.
    SYSTEM_ARCHITECTURE = "system_architecture"
    # A structured drawing-package spec (dimensions, GD&T callouts, surface
    # finishes, inspection requirements) for a CAD_MODEL part, produced by
    # `generate_technical_drawing`. Not a rendered 2D vector drawing --
    # MetaForge has no TechDraw-equivalent generator; this persists the
    # callout DATA a real drawing would encode, reviewable on its own.
    TECHNICAL_DRAWING = "technical_drawing"
    # A generated regulatory checklist (regime/requirement/evidence rows),
    # produced by `record_compliance_checklist` -- the persisted twin of
    # what `generate_checklist` already computes in-session.
    COMPLIANCE_CHECKLIST = "compliance_checklist"
    # A purchase-order-style record (line items, quantities, unit costs,
    # distributor, lead time) produced by `create_procurement_record`,
    # usually linked back to the BOM it was sourced from.
    PROCUREMENT_RECORD = "procurement_record"


class ConstraintSeverity(StrEnum):
    """How critical a constraint violation is."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ConstraintStatus(StrEnum):
    """Current evaluation state of a constraint."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    UNEVALUATED = "unevaluated"
    SKIPPED = "skipped"


class ComponentLifecycle(StrEnum):
    """Production status of a physical component."""

    ACTIVE = "active"
    NRND = "nrnd"
    EOL = "eol"
    OBSOLETE = "obsolete"
    UNKNOWN = "unknown"


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
    SUPERSEDES = "supersedes"
    # MET-430: a Datasheet describes a Component by MPN. Edges are
    # Datasheet --DESCRIBES--> Component.
    DESCRIBES = "describes"
