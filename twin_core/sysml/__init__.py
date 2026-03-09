"""SysML v2 mapping and integration module for the Digital Twin.

Provides bidirectional mapping between MetaForge graph nodes and SysML v2
model elements, JSON serialization following the SysML v2 REST API format,
and a feasibility evaluation for production MBSE tool integration.
"""

from twin_core.sysml.mapper import SysMLMapper
from twin_core.sysml.models import (
    ConnectionUsage,
    ConstraintUsage,
    InterfaceUsage,
    Package,
    PartUsage,
    RequirementUsage,
    SysMLElement,
)
from twin_core.sysml.serializer import SysMLSerializer

__all__ = [
    "ConnectionUsage",
    "ConstraintUsage",
    "InterfaceUsage",
    "Package",
    "PartUsage",
    "RequirementUsage",
    "SysMLElement",
    "SysMLMapper",
    "SysMLSerializer",
]
