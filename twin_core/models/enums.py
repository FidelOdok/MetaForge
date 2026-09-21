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
    # FORGE-43: a generic node for the Engineering Intent & Requirements
    # Harness's ontology (intent/need/objective/assumption/question/risk/
    # verification_case/evidence -- see EngineeringEntity.entity_type).
    # Requirement/Constraint keep using CONSTRAINT (already real, evaluable,
    # gate-integrated); Decision keeps using WorkProductType.DESIGN_DECISION.
    ENGINEERING_ENTITY = "engineering_entity"
    # FORGE-51: a named, approved snapshot of specific (entity, revision)
    # pairs (see twin_core/models/baseline.py). Distinct from a WorkProduct
    # -- a baseline references controlled entities by revision, it doesn't
    # hold content of its own.
    BASELINE = "baseline"
    # FORGE-51: the prior field values of a Constraint/EngineeringEntity
    # captured just before an update overwrites it, so
    # TwinAPI.get_constraint_revision/get_engineering_entity_revision can
    # answer "what was REQ-012@3" after the entity has moved on to @4 --
    # required for a Baseline's ``includes`` references to mean anything.
    # Never listed alongside real CONSTRAINT/ENGINEERING_ENTITY nodes;
    # list_constraints/list_engineering_entities filter it out by node_type.
    REVISION_SNAPSHOT = "revision_snapshot"
    # FORGE-66 (Phase 7): the mandatory container for a non-trivial change --
    # a proposed Patch plus its trigger/observation/impact/approval state,
    # carried through PROPOSED -> ... -> COMMITTED | ROLLED_BACK (see
    # twin_core/models/engineering_change_transaction.py).
    ENGINEERING_CHANGE_TRANSACTION = "engineering_change_transaction"


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


class AuthorityState(StrEnum):
    """Where a controlled object (Constraint/EngineeringEntity) stands in
    the harness's authority lifecycle (FORGE-51, spec section 25).

    Never conflated with ``confidence`` (twin_core.models.confidence) -- a
    high-confidence model prediction does not imply approval. Nothing in
    this codebase derives ``authority`` from ``confidence``; the only code
    path that advances authority to BASELINED is
    ``twin_core.transactions.baseline.create_baseline``, when an entity's
    current revision is actually included in an approved Baseline.
    """

    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    BASELINED = "baselined"
    VERIFIED = "verified"


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
    # FORGE-43: Engineering Intent & Requirements Harness relation vocabulary
    # not already covered by the edge types above. Already-covered relations
    # reuse the existing members rather than duplicating them: IMPLEMENTS
    # (spec's "implemented_by" / requirement-decomposition -- see FORGE-46),
    # VALIDATES ("validated_by"), CONFLICTS_WITH, SUPERSEDES, DEPENDS_ON,
    # CONSTRAINED_BY ("constrains").
    DERIVES_FROM = "derives_from"
    SATISFIES = "satisfies"
    MOTIVATES = "motivates"
    REFINES = "refines"
    DECOMPOSES_INTO = "decomposes_into"
    ALLOCATED_TO = "allocated_to"
    VERIFIED_BY = "verified_by"
    SUPPORTED_BY = "supported_by"
    ASSUMES = "assumes"
    RISKS = "risks"
    INVALIDATES = "invalidates"
    GENERATED_FROM = "generated_from"
    AFFECTED_BY = "affected_by"
    OWNED_BY = "owned_by"
    # FORGE-51: entity --INCLUDED_IN_BASELINE--> Baseline, one edge per
    # Baseline.includes member, for real graph traversal ("which baselines
    # does this requirement belong to") on top of the denormalized
    # Baseline.includes list (same non-source-of-truth relationship
    # parent_refs already has to its own DERIVES_FROM/... edges).
    INCLUDED_IN_BASELINE = "included_in_baseline"
