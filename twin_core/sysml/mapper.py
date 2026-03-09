"""Bidirectional mapper between MetaForge graph nodes and SysML v2 elements.

Provides conversion in both directions:
  - MetaForge -> SysML v2: for exporting designs to MBSE tools
  - SysML v2 -> MetaForge: for importing models from MBSE tools

Mapping rules:
  - Artifact (CAD_MODEL, SCHEMATIC, PCB_LAYOUT, BOM, etc.) -> PartUsage
  - Artifact (PRD, DOCUMENTATION, TEST_PLAN) -> RequirementUsage
  - Constraint -> ConstraintUsage
  - Relationship (EdgeBase) -> ConnectionUsage
  - Component -> PartUsage (with component-specific properties)
"""

from __future__ import annotations

from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.artifact import Artifact
from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.component import Component
from twin_core.models.constraint import Constraint
from twin_core.models.enums import (
    ArtifactType,
    ConstraintSeverity,
    ConstraintStatus,
    EdgeType,
    NodeType,
)
from twin_core.sysml.models import (
    ConnectionUsage,
    ConstraintUsage,
    Package,
    PartUsage,
    RequirementUsage,
    SysMLElement,
    SysMLElementType,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.sysml.mapper")

# Artifact types that map to RequirementUsage rather than PartUsage
_REQUIREMENT_ARTIFACT_TYPES: set[ArtifactType] = {
    ArtifactType.PRD,
    ArtifactType.DOCUMENTATION,
    ArtifactType.TEST_PLAN,
}

# Mapping from EdgeType to ConnectionUsage connection_kind
_EDGE_TYPE_TO_CONNECTION_KIND: dict[EdgeType, str] = {
    EdgeType.DEPENDS_ON: "dependency",
    EdgeType.IMPLEMENTS: "realization",
    EdgeType.VALIDATES: "verification",
    EdgeType.CONTAINS: "composition",
    EdgeType.VERSIONED_BY: "version",
    EdgeType.CONSTRAINED_BY: "constraint",
    EdgeType.PRODUCED_BY: "production",
    EdgeType.USES_COMPONENT: "usage",
    EdgeType.PARENT_OF: "containment",
    EdgeType.CONFLICTS_WITH: "conflict",
}

# Reverse mapping from connection_kind to EdgeType
_CONNECTION_KIND_TO_EDGE_TYPE: dict[str, EdgeType] = {
    v: k for k, v in _EDGE_TYPE_TO_CONNECTION_KIND.items()
}


class SysMLMapper:
    """Bidirectional mapper between MetaForge graph schema and SysML v2 elements."""

    # ------------------------------------------------------------------
    # MetaForge -> SysML v2
    # ------------------------------------------------------------------

    def node_to_sysml(self, node: NodeBase) -> SysMLElement:
        """Convert a MetaForge graph node to its SysML v2 equivalent.

        Dispatches based on node_type:
          - ARTIFACT -> PartUsage or RequirementUsage (depending on artifact type)
          - CONSTRAINT -> ConstraintUsage
          - COMPONENT -> PartUsage
          - Others -> generic SysMLElement
        """
        with tracer.start_as_current_span("sysml.map_node_to_sysml") as span:
            span.set_attribute("node.type", str(node.node_type))
            span.set_attribute("node.id", str(node.id))

            if node.node_type == NodeType.ARTIFACT and isinstance(node, Artifact):
                return self._artifact_to_sysml(node)
            elif node.node_type == NodeType.CONSTRAINT and isinstance(
                node, Constraint
            ):
                return self._constraint_to_sysml(node)
            elif node.node_type == NodeType.COMPONENT and isinstance(node, Component):
                return self._component_to_sysml(node)
            else:
                logger.info(
                    "mapping_generic_node",
                    node_type=str(node.node_type),
                    node_id=str(node.id),
                )
                return SysMLElement(
                    **{"@id": node.id, "@type": SysMLElementType.ELEMENT}
                )

    def edge_to_sysml(self, edge: EdgeBase) -> ConnectionUsage:
        """Convert a MetaForge edge to a SysML v2 ConnectionUsage."""
        with tracer.start_as_current_span("sysml.map_edge_to_sysml") as span:
            span.set_attribute("edge.type", str(edge.edge_type))

            kind = _EDGE_TYPE_TO_CONNECTION_KIND.get(edge.edge_type, "association")
            logger.debug(
                "mapping_edge_to_connection",
                edge_type=str(edge.edge_type),
                connection_kind=kind,
            )
            return ConnectionUsage(
                **{
                    "@type": SysMLElementType.CONNECTION_USAGE,
                    "name": f"{edge.edge_type.value}_{edge.source_id}_{edge.target_id}",
                },
                source_id=edge.source_id,
                target_id=edge.target_id,
                connection_kind=kind,
                metadata=edge.metadata,
            )

    def graph_to_package(
        self,
        nodes: list[NodeBase],
        edges: list[EdgeBase],
        package_name: str = "MetaForge Export",
    ) -> Package:
        """Export an entire subgraph as a SysML v2 Package.

        Converts all nodes and edges, collecting them under a single Package
        with member references.
        """
        with tracer.start_as_current_span("sysml.graph_to_package") as span:
            span.set_attribute("graph.node_count", len(nodes))
            span.set_attribute("graph.edge_count", len(edges))

            elements: list[SysMLElement] = []
            member_ids: list[UUID] = []

            for node in nodes:
                element = self.node_to_sysml(node)
                elements.append(element)
                member_ids.append(element.element_id)

            for edge in edges:
                connection = self.edge_to_sysml(edge)
                elements.append(connection)
                member_ids.append(connection.element_id)

            package = Package(
                **{"@type": SysMLElementType.PACKAGE},
                name=package_name,
                members=member_ids,
                description=f"Exported from MetaForge Digital Twin ({len(nodes)} nodes, {len(edges)} edges)",
            )

            logger.info(
                "graph_exported_to_package",
                package_name=package_name,
                element_count=len(elements),
            )

            # Store elements in metadata for retrieval
            package.metadata["elements"] = [
                str(e.element_id) for e in elements
            ]

            return package

    # ------------------------------------------------------------------
    # SysML v2 -> MetaForge
    # ------------------------------------------------------------------

    def sysml_to_node(self, element: SysMLElement) -> NodeBase:
        """Convert a SysML v2 element to a MetaForge graph node.

        Dispatches based on element @type:
          - PartUsage -> Artifact (CAD_MODEL) or Component
          - RequirementUsage -> Artifact (PRD)
          - ConstraintUsage -> Constraint
          - Others -> raises ValueError
        """
        with tracer.start_as_current_span("sysml.map_sysml_to_node") as span:
            span.set_attribute("element.type", str(element.element_type))
            span.set_attribute("element.id", str(element.element_id))

            if element.element_type == SysMLElementType.PART_USAGE:
                assert isinstance(element, PartUsage)
                return self._part_usage_to_node(element)
            elif element.element_type == SysMLElementType.REQUIREMENT_USAGE:
                assert isinstance(element, RequirementUsage)
                return self._requirement_usage_to_node(element)
            elif element.element_type == SysMLElementType.CONSTRAINT_USAGE:
                assert isinstance(element, ConstraintUsage)
                return self._constraint_usage_to_node(element)
            else:
                raise ValueError(
                    f"Cannot convert SysML element type {element.element_type} "
                    f"to a MetaForge node. Supported types: PartUsage, "
                    f"RequirementUsage, ConstraintUsage."
                )

    def sysml_to_edge(self, connection: ConnectionUsage) -> EdgeBase:
        """Convert a SysML v2 ConnectionUsage to a MetaForge edge."""
        with tracer.start_as_current_span("sysml.map_sysml_to_edge") as span:
            span.set_attribute("connection.kind", connection.connection_kind)

            edge_type = _CONNECTION_KIND_TO_EDGE_TYPE.get(
                connection.connection_kind, EdgeType.DEPENDS_ON
            )

            if connection.source_id is None or connection.target_id is None:
                raise ValueError(
                    "ConnectionUsage must have both source_id and target_id "
                    "for conversion to a MetaForge edge."
                )

            logger.debug(
                "mapping_connection_to_edge",
                connection_kind=connection.connection_kind,
                edge_type=str(edge_type),
            )

            return EdgeBase(
                source_id=connection.source_id,
                target_id=connection.target_id,
                edge_type=edge_type,
                metadata=connection.metadata,
            )

    # ------------------------------------------------------------------
    # Private helpers: MetaForge -> SysML
    # ------------------------------------------------------------------

    def _artifact_to_sysml(self, artifact: Artifact) -> SysMLElement:
        """Map an Artifact to either RequirementUsage or PartUsage."""
        if artifact.type in _REQUIREMENT_ARTIFACT_TYPES:
            logger.debug(
                "mapping_artifact_to_requirement",
                artifact_type=str(artifact.type),
                artifact_id=str(artifact.id),
            )
            return RequirementUsage(
                **{
                    "@id": artifact.id,
                    "@type": SysMLElementType.REQUIREMENT_USAGE,
                },
                name=artifact.name,
                requirement_text=artifact.metadata.get("requirement_text", ""),
                requirement_id=str(artifact.id),
                source=artifact.created_by,
            )
        else:
            logger.debug(
                "mapping_artifact_to_part",
                artifact_type=str(artifact.type),
                artifact_id=str(artifact.id),
            )
            return PartUsage(
                **{
                    "@id": artifact.id,
                    "@type": SysMLElementType.PART_USAGE,
                },
                name=artifact.name,
                domain=artifact.domain,
                file_path=artifact.file_path,
                properties={
                    "artifact_type": str(artifact.type),
                    "format": artifact.format,
                    "content_hash": artifact.content_hash,
                    **artifact.metadata,
                },
            )

    def _constraint_to_sysml(self, constraint: Constraint) -> ConstraintUsage:
        """Map a Constraint to ConstraintUsage."""
        logger.debug(
            "mapping_constraint_to_constraint_usage",
            constraint_id=str(constraint.id),
        )
        return ConstraintUsage(
            **{
                "@id": constraint.id,
                "@type": SysMLElementType.CONSTRAINT_USAGE,
            },
            name=constraint.name,
            expression=constraint.expression,
            severity=str(constraint.severity),
            status=str(constraint.status),
            is_cross_domain=constraint.cross_domain,
            metadata=constraint.metadata,
        )

    def _component_to_sysml(self, component: Component) -> PartUsage:
        """Map a Component to PartUsage with component-specific properties."""
        logger.debug(
            "mapping_component_to_part",
            part_number=component.part_number,
        )
        return PartUsage(
            **{
                "@id": component.id,
                "@type": SysMLElementType.PART_USAGE,
            },
            name=component.part_number,
            properties={
                "manufacturer": component.manufacturer,
                "description": component.description,
                "package": component.package,
                "lifecycle": str(component.lifecycle),
                "datasheet_url": component.datasheet_url,
                "unit_cost": component.unit_cost,
                "lead_time_days": component.lead_time_days,
                "quantity": component.quantity,
                "is_component": True,
                **component.specs,
            },
        )

    # ------------------------------------------------------------------
    # Private helpers: SysML -> MetaForge
    # ------------------------------------------------------------------

    def _part_usage_to_node(self, part: PartUsage) -> NodeBase:
        """Map a PartUsage to either an Artifact or Component."""
        is_component = part.properties.get("is_component", False)

        if is_component:
            logger.debug(
                "mapping_part_to_component", part_name=part.name
            )
            specs = {
                k: v
                for k, v in part.properties.items()
                if k
                not in {
                    "manufacturer",
                    "description",
                    "package",
                    "lifecycle",
                    "datasheet_url",
                    "unit_cost",
                    "lead_time_days",
                    "quantity",
                    "is_component",
                }
            }
            return Component(
                id=part.element_id,
                part_number=part.name,
                manufacturer=part.properties.get("manufacturer", ""),
                description=part.properties.get("description", ""),
                package=part.properties.get("package", ""),
                datasheet_url=part.properties.get("datasheet_url", ""),
                unit_cost=part.properties.get("unit_cost"),
                lead_time_days=part.properties.get("lead_time_days"),
                quantity=part.properties.get("quantity", 1),
                specs=specs,
            )
        else:
            artifact_type_str = part.properties.get(
                "artifact_type", str(ArtifactType.CAD_MODEL)
            )
            # Parse the ArtifactType, default to CAD_MODEL
            try:
                artifact_type = ArtifactType(artifact_type_str)
            except ValueError:
                artifact_type = ArtifactType.CAD_MODEL

            logger.debug(
                "mapping_part_to_artifact",
                part_name=part.name,
                artifact_type=str(artifact_type),
            )
            return Artifact(
                id=part.element_id,
                name=part.name,
                type=artifact_type,
                domain=part.domain or "general",
                file_path=part.file_path or "",
                content_hash=part.properties.get("content_hash", ""),
                format=part.properties.get("format", ""),
                created_by="sysml_import",
                metadata={
                    k: v
                    for k, v in part.properties.items()
                    if k not in {"artifact_type", "format", "content_hash"}
                },
            )

    def _requirement_usage_to_node(self, req: RequirementUsage) -> Artifact:
        """Map a RequirementUsage to an Artifact of type PRD."""
        logger.debug(
            "mapping_requirement_to_artifact", req_name=req.name
        )
        return Artifact(
            id=req.element_id,
            name=req.name or "Imported Requirement",
            type=ArtifactType.PRD,
            domain="requirements",
            file_path="",
            content_hash="",
            format="text",
            created_by=req.source or "sysml_import",
            metadata={
                "requirement_text": req.requirement_text,
                "requirement_id": req.requirement_id,
                "priority": req.priority,
            },
        )

    def _constraint_usage_to_node(self, cu: ConstraintUsage) -> Constraint:
        """Map a ConstraintUsage to a Constraint."""
        logger.debug(
            "mapping_constraint_usage_to_constraint",
            cu_name=cu.name,
        )
        # Parse severity, default to WARNING
        try:
            severity = ConstraintSeverity(cu.severity)
        except ValueError:
            severity = ConstraintSeverity.WARNING

        # Parse status, default to UNEVALUATED
        try:
            status = ConstraintStatus(cu.status)
        except ValueError:
            status = ConstraintStatus.UNEVALUATED

        return Constraint(
            id=cu.element_id,
            name=cu.name or "Imported Constraint",
            expression=cu.expression,
            severity=severity,
            status=status,
            domain="imported",
            cross_domain=cu.is_cross_domain,
            source="sysml_import",
            metadata=cu.metadata,
        )
