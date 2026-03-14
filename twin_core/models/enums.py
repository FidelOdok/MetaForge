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
