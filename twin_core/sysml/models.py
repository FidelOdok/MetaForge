"""SysML v2 Pydantic models aligned with the SysML v2 API/Services specification.

These models represent the subset of SysML v2 element types that map to
MetaForge Digital Twin node types. The JSON schema follows the SysML v2
REST API conventions (camelCase keys, @type discriminator, @id identifiers).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SysMLElementType(StrEnum):
    """SysML v2 element metatype discriminator values."""

    ELEMENT = "Element"
    PACKAGE = "Package"
    PART_USAGE = "PartUsage"
    REQUIREMENT_USAGE = "RequirementUsage"
    CONSTRAINT_USAGE = "ConstraintUsage"
    CONNECTION_USAGE = "ConnectionUsage"
    INTERFACE_USAGE = "InterfaceUsage"


class SysMLElement(BaseModel):
    """Base SysML v2 element following the REST API JSON schema.

    All SysML v2 elements have an ``@id``, ``@type``, and optional
    ``name`` / ``qualifiedName`` per the KerML/SysML v2 metamodel.
    """

    element_id: UUID = Field(default_factory=uuid4, alias="@id")
    element_type: SysMLElementType = Field(default=SysMLElementType.ELEMENT, alias="@type")
    name: str = ""
    qualified_name: str = Field(default="", alias="qualifiedName")
    owner_id: UUID | None = Field(default=None, alias="ownerId")
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"populate_by_name": True}


class Package(SysMLElement):
    """SysML v2 Package — a namespace container for owned elements."""

    element_type: SysMLElementType = Field(default=SysMLElementType.PACKAGE, alias="@type")
    members: list[UUID] = Field(default_factory=list)
    description: str = ""


class PartUsage(SysMLElement):
    """SysML v2 PartUsage — a usage of a part definition.

    Maps to MetaForge WorkProduct nodes of physical types (CAD_MODEL, SCHEMATIC,
    PCB_LAYOUT, BOM, etc.) and to Component nodes.
    """

    element_type: SysMLElementType = Field(default=SysMLElementType.PART_USAGE, alias="@type")
    part_definition_id: UUID | None = None
    domain: str = ""
    file_path: str = ""
    properties: dict = Field(default_factory=dict)


class RequirementUsage(SysMLElement):
    """SysML v2 RequirementUsage — a usage of a requirement definition.

    Maps to MetaForge WorkProduct nodes with type PRD, DOCUMENTATION, or
    TEST_PLAN where the work_product captures a requirement.
    """

    element_type: SysMLElementType = Field(
        default=SysMLElementType.REQUIREMENT_USAGE, alias="@type"
    )
    requirement_text: str = ""
    requirement_id: str = ""
    priority: str = ""
    source: str = ""


class ConstraintUsage(SysMLElement):
    """SysML v2 ConstraintUsage — a usage of a constraint definition.

    Maps to MetaForge Constraint nodes.
    """

    element_type: SysMLElementType = Field(default=SysMLElementType.CONSTRAINT_USAGE, alias="@type")
    expression: str = ""
    severity: str = ""
    status: str = ""
    is_cross_domain: bool = False


class ConnectionUsage(SysMLElement):
    """SysML v2 ConnectionUsage — a connection between parts.

    Maps to MetaForge Relationship edges (EdgeBase with various EdgeTypes).
    """

    element_type: SysMLElementType = Field(default=SysMLElementType.CONNECTION_USAGE, alias="@type")
    source_id: UUID | None = None
    target_id: UUID | None = None
    connection_kind: str = ""


class InterfaceUsage(SysMLElement):
    """SysML v2 InterfaceUsage — an interface between parts.

    Used for cross-domain interfaces (e.g., mechanical-to-electronics
    mounting interface, firmware-to-hardware pin interface).
    """

    element_type: SysMLElementType = Field(default=SysMLElementType.INTERFACE_USAGE, alias="@type")
    source_id: UUID | None = None
    target_id: UUID | None = None
    interface_kind: str = ""
    protocol: str = ""
