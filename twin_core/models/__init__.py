"""Digital Twin graph models — all node types, edge types, and enumerations."""

from twin_core.models.agent import AgentNode
from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.baseline import Baseline, BaselineMember, BaselineResult
from twin_core.models.bom_item import BOMItem
from twin_core.models.component import Component
from twin_core.models.confidence import Confidence
from twin_core.models.constraint import Constraint
from twin_core.models.datasheet import Datasheet
from twin_core.models.design_element import DesignElement
from twin_core.models.device_instance import DeviceInstance
from twin_core.models.engineering_entity import EngineeringEntity, EngineeringEntityType
from twin_core.models.enums import (
    AuthorityState,
    ComponentLifecycle,
    ConstraintSeverity,
    ConstraintStatus,
    EdgeType,
    NodeType,
    WorkProductType,
)
from twin_core.models.patch import (
    Patch,
    PatchOp,
    PatchOperation,
    PatchOperationResult,
    PatchResult,
)
from twin_core.models.relationship import (
    ConstrainedByEdge,
    DependsOnEdge,
    SubGraph,
    UsesComponentEdge,
)
from twin_core.models.revision_snapshot import RevisionSnapshot
from twin_core.models.twin_model import TwinModel
from twin_core.models.version import Version, VersionDiff, WorkProductChange
from twin_core.models.work_product import WorkProduct

__all__ = [
    # Enums
    "NodeType",
    "WorkProductType",
    "ConstraintSeverity",
    "ConstraintStatus",
    "ComponentLifecycle",
    "EdgeType",
    "AuthorityState",
    # Base
    "NodeBase",
    "EdgeBase",
    # Nodes
    "WorkProduct",
    "Constraint",
    "Version",
    "Component",
    "AgentNode",
    "BOMItem",
    "DeviceInstance",
    "TwinModel",
    "DesignElement",
    "Datasheet",
    "EngineeringEntity",
    "EngineeringEntityType",
    # Patch/transaction (FORGE-50)
    "Patch",
    "PatchOp",
    "PatchOperation",
    "PatchOperationResult",
    "PatchResult",
    # Baselines + revision history (FORGE-51)
    "Baseline",
    "BaselineMember",
    "BaselineResult",
    "RevisionSnapshot",
    "Confidence",
    # Typed edges
    "DependsOnEdge",
    "UsesComponentEdge",
    "ConstrainedByEdge",
    # Responses
    "SubGraph",
    "WorkProductChange",
    "VersionDiff",
]
