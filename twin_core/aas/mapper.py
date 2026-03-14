"""Maps Digital Twin graph nodes to AAS submodels.

Supports four IDTA-standard submodel types:
  - DigitalNameplate (IDTA-02006)
  - BillOfMaterials (IDTA-02011)
  - TechnicalData (IDTA-02003)
  - Documentation (IDTA-02004)
"""

from __future__ import annotations

import structlog

from observability.tracing import get_tracer
from twin_core.aas.models import (
    AASEnvironment,
    AssetAdministrationShell,
    AssetInformation,
    AssetKind,
    DataTypeDefXsd,
    Key,
    KeyType,
    ModellingKind,
    Property,
    Reference,
    Submodel,
    SubmodelElement,
    SubmodelElementCollection,
)
from twin_core.models.component import Component
from twin_core.models.constraint import Constraint
from twin_core.models.enums import NodeType, WorkProductType
from twin_core.models.relationship import SubGraph
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.aas.mapper")

# IDTA semantic IDs for standard submodel templates
SEMANTIC_ID_NAMEPLATE = "https://admin-shell.io/zvei/nameplate/2/0/Nameplate"
SEMANTIC_ID_BOM = "https://admin-shell.io/idta/HierarchicalStructures/1/0/Submodel"
SEMANTIC_ID_TECHNICAL_DATA = "https://admin-shell.io/ZVEI/TechnicalData/Submodel/1/2"
SEMANTIC_ID_DOCUMENTATION = "https://admin-shell.io/vdi/2770/1/0/Documentation"


def _make_semantic_ref(semantic_id: str) -> Reference:
    """Create a Reference with a single GlobalReference key."""
    return Reference(
        type="ExternalReference",
        keys=[Key(type=KeyType.GLOBAL_REFERENCE, value=semantic_id)],
    )


def _make_submodel_ref(submodel_id: str) -> Reference:
    """Create a Reference pointing to a Submodel by ID."""
    return Reference(
        type="ModelReference",
        keys=[Key(type=KeyType.SUBMODEL, value=submodel_id)],
    )


class AASMapper:
    """Maps a Digital Twin SubGraph to an AASEnvironment.

    The mapper inspects each node in the subgraph and routes it to the
    appropriate AAS submodel based on its node_type and type field.
    """

    def __init__(self, asset_id: str, asset_name: str) -> None:
        self._asset_id = asset_id
        self._asset_name = asset_name

    def map_subgraph(self, subgraph: SubGraph) -> AASEnvironment:
        """Convert a SubGraph into a complete AAS environment.

        Args:
            subgraph: The graph subset to serialize.

        Returns:
            An AASEnvironment with shell, submodels, and references.
        """
        with tracer.start_as_current_span("aas.map_subgraph") as span:
            span.set_attribute("aas.asset_id", self._asset_id)
            span.set_attribute("aas.node_count", len(subgraph.nodes))

            logger.info(
                "mapping_subgraph_to_aas",
                asset_id=self._asset_id,
                node_count=len(subgraph.nodes),
                edge_count=len(subgraph.edges),
            )

            # Classify nodes
            work_products: list[WorkProduct] = []
            components: list[Component] = []
            constraints: list[Constraint] = []

            for node in subgraph.nodes:
                if node.node_type == NodeType.WORK_PRODUCT and isinstance(node, WorkProduct):
                    work_products.append(node)
                elif node.node_type == NodeType.COMPONENT and isinstance(node, Component):
                    components.append(node)
                elif node.node_type == NodeType.CONSTRAINT and isinstance(node, Constraint):
                    constraints.append(node)

            # Build submodels
            submodels: list[Submodel] = []

            nameplate = self._build_nameplate_submodel(work_products)
            submodels.append(nameplate)

            bom = self._build_bom_submodel(components)
            submodels.append(bom)

            tech_data = self._build_technical_data_submodel(constraints)
            submodels.append(tech_data)

            doc_artifacts = [a for a in work_products if a.type == WorkProductType.DOCUMENTATION]
            documentation = self._build_documentation_submodel(doc_artifacts)
            submodels.append(documentation)

            # Build shell
            shell = AssetAdministrationShell(
                id=f"urn:metaforge:aas:{self._asset_id}",
                idShort=self._asset_name,
                assetInformation=AssetInformation(
                    assetKind=AssetKind.INSTANCE,
                    globalAssetId=self._asset_id,
                ),
                submodels=[_make_submodel_ref(sm.id) for sm in submodels],
            )

            env = AASEnvironment(
                assetAdministrationShells=[shell],
                submodels=submodels,
            )

            logger.info(
                "aas_mapping_complete",
                asset_id=self._asset_id,
                submodel_count=len(submodels),
                shell_id=shell.id,
            )

            return env

    def _build_nameplate_submodel(self, work_products: list[WorkProduct]) -> Submodel:
        """Build DigitalNameplate submodel from work_product metadata."""
        with tracer.start_as_current_span("aas.build_nameplate"):
            elements: list[SubmodelElement] = [
                Property(
                    idShort="ManufacturerName",
                    valueType=DataTypeDefXsd.STRING,
                    value="MetaForge",
                ),
                Property(
                    idShort="ManufacturerProductDesignation",
                    valueType=DataTypeDefXsd.STRING,
                    value=self._asset_name,
                ),
            ]

            # Add work_product summary
            if work_products:
                elements.append(
                    Property(
                        idShort="ArtifactCount",
                        valueType=DataTypeDefXsd.INT,
                        value=str(len(work_products)),
                    )
                )

                # Collect unique domains
                domains = sorted({a.domain for a in work_products})
                if domains:
                    elements.append(
                        Property(
                            idShort="EngineeringDomains",
                            valueType=DataTypeDefXsd.STRING,
                            value=", ".join(domains),
                        )
                    )

            submodel_id = f"urn:metaforge:sm:nameplate:{self._asset_id}"
            return Submodel(
                id=submodel_id,
                idShort="Nameplate",
                semanticId=_make_semantic_ref(SEMANTIC_ID_NAMEPLATE),
                kind=ModellingKind.INSTANCE,
                submodelElements=elements,
            )

    def _build_bom_submodel(self, components: list[Component]) -> Submodel:
        """Build BillOfMaterials submodel from Component nodes."""
        with tracer.start_as_current_span("aas.build_bom") as span:
            span.set_attribute("aas.component_count", len(components))

            elements: list[SubmodelElement] = []

            for idx, comp in enumerate(components):
                line_item = SubmodelElementCollection(
                    idShort=f"BOMLineItem_{idx}",
                    value=[
                        Property(
                            idShort="PartNumber",
                            valueType=DataTypeDefXsd.STRING,
                            value=comp.part_number,
                        ),
                        Property(
                            idShort="Manufacturer",
                            valueType=DataTypeDefXsd.STRING,
                            value=comp.manufacturer,
                        ),
                        Property(
                            idShort="Description",
                            valueType=DataTypeDefXsd.STRING,
                            value=comp.description,
                        ),
                        Property(
                            idShort="Quantity",
                            valueType=DataTypeDefXsd.INT,
                            value=str(comp.quantity),
                        ),
                        Property(
                            idShort="Lifecycle",
                            valueType=DataTypeDefXsd.STRING,
                            value=str(comp.lifecycle),
                        ),
                    ],
                )

                if comp.package:
                    line_item.value.append(
                        Property(
                            idShort="Package",
                            valueType=DataTypeDefXsd.STRING,
                            value=comp.package,
                        )
                    )

                if comp.unit_cost is not None:
                    line_item.value.append(
                        Property(
                            idShort="UnitCost",
                            valueType=DataTypeDefXsd.DOUBLE,
                            value=str(comp.unit_cost),
                        )
                    )

                elements.append(line_item)

            submodel_id = f"urn:metaforge:sm:bom:{self._asset_id}"
            return Submodel(
                id=submodel_id,
                idShort="BillOfMaterials",
                semanticId=_make_semantic_ref(SEMANTIC_ID_BOM),
                kind=ModellingKind.INSTANCE,
                submodelElements=elements,
            )

    def _build_technical_data_submodel(self, constraints: list[Constraint]) -> Submodel:
        """Build TechnicalData submodel from Constraint nodes."""
        with tracer.start_as_current_span("aas.build_technical_data") as span:
            span.set_attribute("aas.constraint_count", len(constraints))

            elements: list[SubmodelElement] = []

            for constraint in constraints:
                constraint_element = SubmodelElementCollection(
                    idShort=f"Constraint_{constraint.name.replace(' ', '_')}",
                    value=[
                        Property(
                            idShort="Name",
                            valueType=DataTypeDefXsd.STRING,
                            value=constraint.name,
                        ),
                        Property(
                            idShort="Expression",
                            valueType=DataTypeDefXsd.STRING,
                            value=constraint.expression,
                        ),
                        Property(
                            idShort="Severity",
                            valueType=DataTypeDefXsd.STRING,
                            value=str(constraint.severity),
                        ),
                        Property(
                            idShort="Status",
                            valueType=DataTypeDefXsd.STRING,
                            value=str(constraint.status),
                        ),
                        Property(
                            idShort="Domain",
                            valueType=DataTypeDefXsd.STRING,
                            value=constraint.domain,
                        ),
                        Property(
                            idShort="CrossDomain",
                            valueType=DataTypeDefXsd.BOOLEAN,
                            value=str(constraint.cross_domain).lower(),
                        ),
                    ],
                )

                if constraint.message:
                    constraint_element.value.append(
                        Property(
                            idShort="Message",
                            valueType=DataTypeDefXsd.STRING,
                            value=constraint.message,
                        )
                    )

                elements.append(constraint_element)

            submodel_id = f"urn:metaforge:sm:technical_data:{self._asset_id}"
            return Submodel(
                id=submodel_id,
                idShort="TechnicalData",
                semanticId=_make_semantic_ref(SEMANTIC_ID_TECHNICAL_DATA),
                kind=ModellingKind.INSTANCE,
                submodelElements=elements,
            )

    def _build_documentation_submodel(self, doc_artifacts: list[WorkProduct]) -> Submodel:
        """Build Documentation submodel from documentation-type Artifacts."""
        with tracer.start_as_current_span("aas.build_documentation") as span:
            span.set_attribute("aas.doc_count", len(doc_artifacts))

            elements: list[SubmodelElement] = []

            for idx, work_product in enumerate(doc_artifacts):
                doc_element = SubmodelElementCollection(
                    idShort=f"Document_{idx}",
                    value=[
                        Property(
                            idShort="Title",
                            valueType=DataTypeDefXsd.STRING,
                            value=work_product.name,
                        ),
                        Property(
                            idShort="FilePath",
                            valueType=DataTypeDefXsd.STRING,
                            value=work_product.file_path,
                        ),
                        Property(
                            idShort="Format",
                            valueType=DataTypeDefXsd.STRING,
                            value=work_product.format,
                        ),
                        Property(
                            idShort="Domain",
                            valueType=DataTypeDefXsd.STRING,
                            value=work_product.domain,
                        ),
                        Property(
                            idShort="ContentHash",
                            valueType=DataTypeDefXsd.STRING,
                            value=work_product.content_hash,
                        ),
                    ],
                )
                elements.append(doc_element)

            submodel_id = f"urn:metaforge:sm:documentation:{self._asset_id}"
            return Submodel(
                id=submodel_id,
                idShort="Documentation",
                semanticId=_make_semantic_ref(SEMANTIC_ID_DOCUMENTATION),
                kind=ModellingKind.INSTANCE,
                submodelElements=elements,
            )
