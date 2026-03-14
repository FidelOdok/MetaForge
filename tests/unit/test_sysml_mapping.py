"""Tests for SysML v2 mapping, serialization, and evaluation (MET-165).

Covers:
  - Node type mapping (both directions)
  - JSON serialization round-trip
  - Graph-to-SysML export
  - SysML-to-graph import
  - Feasibility evaluation
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.models.base import EdgeBase
from twin_core.models.component import Component
from twin_core.models.constraint import Constraint
from twin_core.models.enums import (
    ConstraintSeverity,
    ConstraintStatus,
    EdgeType,
    NodeType,
    WorkProductType,
)
from twin_core.models.work_product import WorkProduct
from twin_core.sysml.evaluation import FeasibilityReport, evaluate_sysml_feasibility
from twin_core.sysml.mapper import SysMLMapper
from twin_core.sysml.models import (
    ConnectionUsage,
    ConstraintUsage,
    Package,
    PartUsage,
    RequirementUsage,
    SysMLElement,
    SysMLElementType,
)
from twin_core.sysml.serializer import SysMLSerializer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mapper() -> SysMLMapper:
    return SysMLMapper()


@pytest.fixture
def serializer() -> SysMLSerializer:
    return SysMLSerializer()


@pytest.fixture
def cad_artifact() -> WorkProduct:
    return WorkProduct(
        name="motor_mount",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="cad/motor_mount.step",
        content_hash="abc123",
        format="STEP",
        created_by="mech_agent",
        metadata={"material": "aluminum"},
    )


@pytest.fixture
def schematic_artifact() -> WorkProduct:
    return WorkProduct(
        name="power_supply",
        type=WorkProductType.SCHEMATIC,
        domain="electronics",
        file_path="eda/power_supply.kicad_sch",
        content_hash="def456",
        format="KiCad",
        created_by="elec_agent",
    )


@pytest.fixture
def prd_artifact() -> WorkProduct:
    return WorkProduct(
        name="Drone FC Requirements",
        type=WorkProductType.PRD,
        domain="requirements",
        file_path="PRD.md",
        content_hash="req789",
        format="markdown",
        created_by="product_owner",
        metadata={"requirement_text": "The FC shall support 6-axis IMU"},
    )


@pytest.fixture
def test_plan_artifact() -> WorkProduct:
    return WorkProduct(
        name="Vibration Test Plan",
        type=WorkProductType.TEST_PLAN,
        domain="testing",
        file_path="tests/vibration.md",
        content_hash="test111",
        format="markdown",
        created_by="test_engineer",
    )


@pytest.fixture
def constraint() -> Constraint:
    return Constraint(
        name="max_board_temp",
        expression="board_temp < 85",
        severity=ConstraintSeverity.ERROR,
        status=ConstraintStatus.PASS,
        domain="thermal",
        cross_domain=True,
        source="thermal_analysis",
        metadata={"unit": "celsius"},
    )


@pytest.fixture
def component() -> Component:
    return Component(
        part_number="STM32F405RGT6",
        manufacturer="STMicroelectronics",
        description="ARM Cortex-M4 MCU",
        package="LQFP-64",
        datasheet_url="https://example.com/ds",
        unit_cost=4.50,
        lead_time_days=14,
        quantity=1,
        specs={"clock_mhz": 168, "flash_kb": 1024},
    )


@pytest.fixture
def edge(cad_artifact: WorkProduct, schematic_artifact: WorkProduct) -> EdgeBase:
    return EdgeBase(
        source_id=cad_artifact.id,
        target_id=schematic_artifact.id,
        edge_type=EdgeType.DEPENDS_ON,
        metadata={"reason": "mechanical clearance"},
    )


# ---------------------------------------------------------------------------
# Node type mapping: MetaForge -> SysML
# ---------------------------------------------------------------------------


class TestNodeToSysML:
    """Test MetaForge node -> SysML v2 element conversion."""

    def test_cad_artifact_to_part_usage(
        self, mapper: SysMLMapper, cad_artifact: WorkProduct
    ) -> None:
        result = mapper.node_to_sysml(cad_artifact)
        assert isinstance(result, PartUsage)
        assert result.element_type == SysMLElementType.PART_USAGE
        assert result.name == "motor_mount"
        assert result.domain == "mechanical"
        assert result.file_path == "cad/motor_mount.step"
        assert result.properties["work_product_type"] == "cad_model"
        assert result.properties["format"] == "STEP"
        assert result.properties["material"] == "aluminum"
        assert result.element_id == cad_artifact.id

    def test_schematic_artifact_to_part_usage(
        self, mapper: SysMLMapper, schematic_artifact: WorkProduct
    ) -> None:
        result = mapper.node_to_sysml(schematic_artifact)
        assert isinstance(result, PartUsage)
        assert result.element_type == SysMLElementType.PART_USAGE
        assert result.name == "power_supply"
        assert result.properties["work_product_type"] == "schematic"

    def test_prd_artifact_to_requirement_usage(
        self, mapper: SysMLMapper, prd_artifact: WorkProduct
    ) -> None:
        result = mapper.node_to_sysml(prd_artifact)
        assert isinstance(result, RequirementUsage)
        assert result.element_type == SysMLElementType.REQUIREMENT_USAGE
        assert result.name == "Drone FC Requirements"
        assert result.requirement_text == "The FC shall support 6-axis IMU"
        assert result.source == "product_owner"
        assert result.element_id == prd_artifact.id

    def test_test_plan_artifact_to_requirement_usage(
        self, mapper: SysMLMapper, test_plan_artifact: WorkProduct
    ) -> None:
        result = mapper.node_to_sysml(test_plan_artifact)
        assert isinstance(result, RequirementUsage)
        assert result.element_type == SysMLElementType.REQUIREMENT_USAGE

    def test_constraint_to_constraint_usage(
        self, mapper: SysMLMapper, constraint: Constraint
    ) -> None:
        result = mapper.node_to_sysml(constraint)
        assert isinstance(result, ConstraintUsage)
        assert result.element_type == SysMLElementType.CONSTRAINT_USAGE
        assert result.name == "max_board_temp"
        assert result.expression == "board_temp < 85"
        assert result.severity == "error"
        assert result.status == "pass"
        assert result.is_cross_domain is True
        assert result.element_id == constraint.id

    def test_component_to_part_usage(self, mapper: SysMLMapper, component: Component) -> None:
        result = mapper.node_to_sysml(component)
        assert isinstance(result, PartUsage)
        assert result.element_type == SysMLElementType.PART_USAGE
        assert result.name == "STM32F405RGT6"
        assert result.properties["manufacturer"] == "STMicroelectronics"
        assert result.properties["is_component"] is True
        assert result.properties["clock_mhz"] == 168
        assert result.element_id == component.id

    def test_edge_to_connection_usage(self, mapper: SysMLMapper, edge: EdgeBase) -> None:
        result = mapper.edge_to_sysml(edge)
        assert isinstance(result, ConnectionUsage)
        assert result.element_type == SysMLElementType.CONNECTION_USAGE
        assert result.connection_kind == "dependency"
        assert result.source_id == edge.source_id
        assert result.target_id == edge.target_id


# ---------------------------------------------------------------------------
# Node type mapping: SysML -> MetaForge
# ---------------------------------------------------------------------------


class TestSysMLToNode:
    """Test SysML v2 element -> MetaForge node conversion."""

    def test_part_usage_to_artifact(self, mapper: SysMLMapper) -> None:
        part = PartUsage(
            name="motor_mount",
            domain="mechanical",
            file_path="cad/motor_mount.step",
            properties={
                "work_product_type": "cad_model",
                "format": "STEP",
                "content_hash": "abc123",
                "material": "aluminum",
            },
        )
        result = mapper.sysml_to_node(part)
        assert isinstance(result, WorkProduct)
        assert result.node_type == NodeType.WORK_PRODUCT
        assert result.name == "motor_mount"
        assert result.type == WorkProductType.CAD_MODEL
        assert result.domain == "mechanical"
        assert result.format == "STEP"
        assert result.metadata["material"] == "aluminum"

    def test_part_usage_to_component(self, mapper: SysMLMapper) -> None:
        part = PartUsage(
            name="STM32F405RGT6",
            properties={
                "is_component": True,
                "manufacturer": "STMicroelectronics",
                "description": "ARM Cortex-M4 MCU",
                "package": "LQFP-64",
                "clock_mhz": 168,
            },
        )
        result = mapper.sysml_to_node(part)
        assert isinstance(result, Component)
        assert result.node_type == NodeType.COMPONENT
        assert result.part_number == "STM32F405RGT6"
        assert result.manufacturer == "STMicroelectronics"
        assert result.specs["clock_mhz"] == 168

    def test_requirement_usage_to_artifact(self, mapper: SysMLMapper) -> None:
        req = RequirementUsage(
            name="IMU Requirement",
            requirement_text="The FC shall support 6-axis IMU",
            source="product_owner",
            priority="high",
        )
        result = mapper.sysml_to_node(req)
        assert isinstance(result, WorkProduct)
        assert result.node_type == NodeType.WORK_PRODUCT
        assert result.type == WorkProductType.PRD
        assert result.name == "IMU Requirement"
        assert result.metadata["requirement_text"] == "The FC shall support 6-axis IMU"
        assert result.metadata["priority"] == "high"

    def test_constraint_usage_to_constraint(self, mapper: SysMLMapper) -> None:
        cu = ConstraintUsage(
            name="max_board_temp",
            expression="board_temp < 85",
            severity="error",
            status="pass",
            is_cross_domain=True,
        )
        result = mapper.sysml_to_node(cu)
        assert isinstance(result, Constraint)
        assert result.node_type == NodeType.CONSTRAINT
        assert result.name == "max_board_temp"
        assert result.expression == "board_temp < 85"
        assert result.severity == ConstraintSeverity.ERROR
        assert result.status == ConstraintStatus.PASS
        assert result.cross_domain is True

    def test_constraint_usage_with_invalid_severity_defaults(self, mapper: SysMLMapper) -> None:
        cu = ConstraintUsage(
            name="test",
            expression="x > 0",
            severity="critical",  # not a valid ConstraintSeverity
            status="unknown",  # not a valid ConstraintStatus
        )
        result = mapper.sysml_to_node(cu)
        assert isinstance(result, Constraint)
        assert result.severity == ConstraintSeverity.WARNING
        assert result.status == ConstraintStatus.UNEVALUATED

    def test_connection_usage_to_edge(self, mapper: SysMLMapper) -> None:
        source_id = uuid4()
        target_id = uuid4()
        conn = ConnectionUsage(
            source_id=source_id,
            target_id=target_id,
            connection_kind="dependency",
        )
        result = mapper.sysml_to_edge(conn)
        assert isinstance(result, EdgeBase)
        assert result.edge_type == EdgeType.DEPENDS_ON
        assert result.source_id == source_id
        assert result.target_id == target_id

    def test_connection_usage_unknown_kind_defaults(self, mapper: SysMLMapper) -> None:
        conn = ConnectionUsage(
            source_id=uuid4(),
            target_id=uuid4(),
            connection_kind="unknown_kind",
        )
        result = mapper.sysml_to_edge(conn)
        assert result.edge_type == EdgeType.DEPENDS_ON

    def test_connection_usage_missing_ids_raises(self, mapper: SysMLMapper) -> None:
        conn = ConnectionUsage(connection_kind="dependency")
        with pytest.raises(ValueError, match="source_id and target_id"):
            mapper.sysml_to_edge(conn)

    def test_unsupported_element_type_raises(self, mapper: SysMLMapper) -> None:
        element = SysMLElement(name="generic")
        with pytest.raises(ValueError, match="Cannot convert"):
            mapper.sysml_to_node(element)


# ---------------------------------------------------------------------------
# Edge type mapping coverage
# ---------------------------------------------------------------------------


class TestEdgeTypeMapping:
    """Test all EdgeType values map to connection_kind and back."""

    @pytest.mark.parametrize(
        "edge_type,expected_kind",
        [
            (EdgeType.DEPENDS_ON, "dependency"),
            (EdgeType.IMPLEMENTS, "realization"),
            (EdgeType.VALIDATES, "verification"),
            (EdgeType.CONTAINS, "composition"),
            (EdgeType.CONSTRAINED_BY, "constraint"),
            (EdgeType.USES_COMPONENT, "usage"),
            (EdgeType.PARENT_OF, "containment"),
            (EdgeType.CONFLICTS_WITH, "conflict"),
        ],
    )
    def test_edge_type_round_trip(
        self,
        mapper: SysMLMapper,
        edge_type: EdgeType,
        expected_kind: str,
    ) -> None:
        source_id = uuid4()
        target_id = uuid4()
        edge = EdgeBase(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
        )
        conn = mapper.edge_to_sysml(edge)
        assert conn.connection_kind == expected_kind

        restored = mapper.sysml_to_edge(conn)
        assert restored.edge_type == edge_type
        assert restored.source_id == source_id
        assert restored.target_id == target_id


# ---------------------------------------------------------------------------
# JSON serialization round-trip
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    """Test JSON serialization and deserialization."""

    def test_part_usage_round_trip(self, serializer: SysMLSerializer) -> None:
        original = PartUsage(
            name="motor_mount",
            domain="mechanical",
            file_path="cad/motor_mount.step",
            properties={"work_product_type": "cad_model", "format": "STEP"},
        )
        json_data = serializer.to_json(original)
        assert json_data["@type"] == "PartUsage"
        assert json_data["name"] == "motor_mount"
        assert json_data["domain"] == "mechanical"

        restored = serializer.from_json(json_data)
        assert isinstance(restored, PartUsage)
        assert restored.name == original.name
        assert restored.domain == original.domain
        assert restored.element_id == original.element_id

    def test_requirement_usage_round_trip(self, serializer: SysMLSerializer) -> None:
        original = RequirementUsage(
            name="IMU Requirement",
            requirement_text="6-axis IMU support",
            source="product_owner",
        )
        json_data = serializer.to_json(original)
        assert json_data["@type"] == "RequirementUsage"

        restored = serializer.from_json(json_data)
        assert isinstance(restored, RequirementUsage)
        assert restored.requirement_text == original.requirement_text

    def test_constraint_usage_round_trip(self, serializer: SysMLSerializer) -> None:
        original = ConstraintUsage(
            name="max_temp",
            expression="temp < 85",
            severity="error",
            is_cross_domain=True,
        )
        json_data = serializer.to_json(original)
        assert json_data["@type"] == "ConstraintUsage"

        restored = serializer.from_json(json_data)
        assert isinstance(restored, ConstraintUsage)
        assert restored.expression == original.expression
        assert restored.is_cross_domain is True

    def test_connection_usage_round_trip(self, serializer: SysMLSerializer) -> None:
        source_id = uuid4()
        target_id = uuid4()
        original = ConnectionUsage(
            source_id=source_id,
            target_id=target_id,
            connection_kind="dependency",
        )
        json_data = serializer.to_json(original)
        assert json_data["@type"] == "ConnectionUsage"

        restored = serializer.from_json(json_data)
        assert isinstance(restored, ConnectionUsage)
        assert restored.connection_kind == "dependency"

    def test_package_round_trip(self, serializer: SysMLSerializer) -> None:
        member_ids = [uuid4(), uuid4()]
        original = Package(
            name="Test Package",
            members=member_ids,
            description="A test package",
        )
        json_data = serializer.to_json(original)
        assert json_data["@type"] == "Package"
        assert json_data["name"] == "Test Package"

        restored = serializer.from_json(json_data)
        assert isinstance(restored, Package)
        assert restored.name == "Test Package"
        assert len(restored.members) == 2

    def test_list_serialization(self, serializer: SysMLSerializer) -> None:
        elements = [
            PartUsage(name="part_a"),
            RequirementUsage(name="req_b"),
        ]
        json_list = serializer.to_json_list(elements)
        assert len(json_list) == 2
        assert json_list[0]["@type"] == "PartUsage"
        assert json_list[1]["@type"] == "RequirementUsage"

        restored = serializer.from_json_list(json_list)
        assert len(restored) == 2
        assert isinstance(restored[0], PartUsage)
        assert isinstance(restored[1], RequirementUsage)

    def test_api_response_format(self, serializer: SysMLSerializer) -> None:
        elements = [PartUsage(name="part_a")]
        response = serializer.to_api_response(elements, project_id="proj-123")
        assert response["@type"] == "ElementList"
        assert response["projectId"] == "proj-123"
        assert response["totalSize"] == 1
        assert len(response["elements"]) == 1


# ---------------------------------------------------------------------------
# Graph-to-SysML export
# ---------------------------------------------------------------------------


class TestGraphExport:
    """Test exporting a MetaForge graph to SysML v2 Package."""

    def test_graph_to_package(
        self,
        mapper: SysMLMapper,
        cad_artifact: WorkProduct,
        prd_artifact: WorkProduct,
        constraint: Constraint,
        edge: EdgeBase,
    ) -> None:
        nodes = [cad_artifact, prd_artifact, constraint]
        edges = [edge]

        package = mapper.graph_to_package(nodes, edges, "Drone FC Export")
        assert isinstance(package, Package)
        assert package.name == "Drone FC Export"
        # 3 nodes + 1 edge = 4 members
        assert len(package.members) == 4
        assert "3 nodes, 1 edges" in package.description

    def test_empty_graph_to_package(self, mapper: SysMLMapper) -> None:
        package = mapper.graph_to_package([], [], "Empty")
        assert isinstance(package, Package)
        assert len(package.members) == 0


# ---------------------------------------------------------------------------
# SysML-to-graph import
# ---------------------------------------------------------------------------


class TestGraphImport:
    """Test importing SysML v2 elements to MetaForge graph nodes."""

    def test_full_import_pipeline(self, mapper: SysMLMapper, serializer: SysMLSerializer) -> None:
        """End-to-end: create SysML elements, serialize, deserialize, map to graph."""
        # Create SysML elements
        part = PartUsage(
            name="imu_sensor",
            domain="electronics",
            properties={"work_product_type": "schematic"},
        )
        req = RequirementUsage(
            name="Vibration Tolerance",
            requirement_text="Survive 10G vibration",
        )
        cu = ConstraintUsage(
            name="power_budget",
            expression="total_power < 5.0",
            severity="warning",
        )

        # Serialize to JSON
        json_list = serializer.to_json_list([part, req, cu])
        assert len(json_list) == 3

        # Deserialize back
        restored_elements = serializer.from_json_list(json_list)
        assert len(restored_elements) == 3

        # Map to MetaForge nodes
        nodes = [mapper.sysml_to_node(e) for e in restored_elements]
        assert len(nodes) == 3
        assert isinstance(nodes[0], WorkProduct)
        assert isinstance(nodes[1], WorkProduct)
        assert isinstance(nodes[2], Constraint)

        # Verify content
        assert nodes[0].name == "imu_sensor"
        assert nodes[1].metadata["requirement_text"] == "Survive 10G vibration"
        assert nodes[2].expression == "total_power < 5.0"

    def test_export_import_round_trip(
        self,
        mapper: SysMLMapper,
        serializer: SysMLSerializer,
        cad_artifact: WorkProduct,
        constraint: Constraint,
    ) -> None:
        """Round-trip: MetaForge -> SysML -> JSON -> SysML -> MetaForge."""
        # Export to SysML
        sysml_part = mapper.node_to_sysml(cad_artifact)
        sysml_constraint = mapper.node_to_sysml(constraint)

        # Serialize
        part_json = serializer.to_json(sysml_part)
        constraint_json = serializer.to_json(sysml_constraint)

        # Deserialize
        restored_part = serializer.from_json(part_json)
        restored_constraint = serializer.from_json(constraint_json)

        # Import back to MetaForge
        node_part = mapper.sysml_to_node(restored_part)
        node_constraint = mapper.sysml_to_node(restored_constraint)

        # Verify round-trip fidelity
        assert isinstance(node_part, WorkProduct)
        assert node_part.name == cad_artifact.name
        assert node_part.type == cad_artifact.type
        assert node_part.domain == cad_artifact.domain
        assert node_part.id == cad_artifact.id

        assert isinstance(node_constraint, Constraint)
        assert node_constraint.name == constraint.name
        assert node_constraint.expression == constraint.expression
        assert node_constraint.severity == constraint.severity
        assert node_constraint.id == constraint.id


# ---------------------------------------------------------------------------
# Feasibility evaluation
# ---------------------------------------------------------------------------


class TestFeasibilityEvaluation:
    """Test the feasibility assessment module."""

    def test_evaluation_returns_report(self) -> None:
        report = evaluate_sysml_feasibility()
        assert isinstance(report, FeasibilityReport)
        assert report.overall_feasibility == "medium"
        assert report.total_effort_weeks > 0

    def test_mapping_coverage_populated(self) -> None:
        report = evaluate_sysml_feasibility()
        assert len(report.mapping_coverage) > 0
        coverages = {mc.coverage for mc in report.mapping_coverage}
        assert "full" in coverages
        assert "partial" in coverages

    def test_gaps_identified(self) -> None:
        report = evaluate_sysml_feasibility()
        assert len(report.gaps) > 0
        categories = {g.category for g in report.gaps}
        assert "model" in categories
        assert "api" in categories

    def test_effort_estimates_sum(self) -> None:
        report = evaluate_sysml_feasibility()
        assert len(report.effort_estimates) > 0
        total = sum(e.effort_weeks for e in report.effort_estimates)
        assert total == report.total_effort_weeks
        assert total > 10  # Expect meaningful effort estimate

    def test_recommendations_provided(self) -> None:
        report = evaluate_sysml_feasibility()
        assert len(report.recommendations) > 0
        # Check that recommendations mention key technologies
        all_recs = " ".join(report.recommendations)
        assert "SysON" in all_recs or "MCP" in all_recs
