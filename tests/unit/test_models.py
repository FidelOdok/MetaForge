"""Unit tests for Digital Twin Pydantic models."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from twin_core.models import (
    AgentNode,
    BOMItem,
    Component,
    ComponentLifecycle,
    ConstrainedByEdge,
    Constraint,
    ConstraintSeverity,
    ConstraintStatus,
    DependsOnEdge,
    DesignElement,
    DeviceInstance,
    EdgeBase,
    EdgeType,
    NodeType,
    SubGraph,
    TwinModel,
    UsesComponentEdge,
    Version,
    VersionDiff,
    WorkProduct,
    WorkProductChange,
    WorkProductType,
)

# --- WorkProduct ---


class TestArtifact:
    def test_create_with_defaults(self):
        a = WorkProduct(
            name="main_schematic",
            type=WorkProductType.SCHEMATIC,
            domain="electronics",
            file_path="eda/kicad/main.kicad_sch",
            content_hash="abc123",
            format="kicad_sch",
            created_by="human",
        )
        assert isinstance(a.id, UUID)
        assert a.node_type == NodeType.WORK_PRODUCT
        assert a.name == "main_schematic"
        assert a.metadata == {}
        assert a.created_at is not None

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            WorkProduct(
                name="test",
                type=WorkProductType.BOM,
                domain="electronics",
                # missing file_path, content_hash, format, created_by
            )

    def test_invalid_work_product_type(self):
        with pytest.raises(ValidationError):
            WorkProduct(
                name="test",
                type="not_a_real_type",
                domain="electronics",
                file_path="x",
                content_hash="x",
                format="x",
                created_by="human",
            )

    def test_serialization_roundtrip(self):
        a = WorkProduct(
            name="bom",
            type=WorkProductType.BOM,
            domain="electronics",
            file_path="bom/bom.csv",
            content_hash="def456",
            format="csv",
            created_by="agent",
            metadata={"total_cost": 42.5},
        )
        data = a.model_dump()
        restored = WorkProduct.model_validate(data)
        assert restored.id == a.id
        assert restored.metadata == {"total_cost": 42.5}


# --- Constraint ---


class TestConstraint:
    def test_create_with_defaults(self):
        c = Constraint(
            name="max_voltage_3v3",
            expression="ctx.work_product('power_budget').metadata.get('max_voltage', 0) <= 3.3",
            severity=ConstraintSeverity.ERROR,
            domain="electronics",
            source="user",
        )
        assert c.node_type == NodeType.CONSTRAINT
        assert c.status == ConstraintStatus.UNEVALUATED
        assert c.cross_domain is False

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            Constraint(
                name="test",
                expression="True",
                severity="critical",
                domain="mech",
                source="user",
            )


# --- Version ---


class TestVersion:
    def test_create_with_defaults(self):
        v = Version(
            branch_name="main",
            commit_message="Initial commit",
            snapshot_hash="snap123",
            author="human",
        )
        assert v.node_type == NodeType.VERSION
        assert v.parent_id is None
        assert v.work_product_ids == []

    def test_with_parent(self):
        parent_id = uuid4()
        v = Version(
            branch_name="agent/mechanical/stress-fix",
            parent_id=parent_id,
            commit_message="Fix stress",
            snapshot_hash="snap456",
            author="mechanical_agent",
        )
        assert v.parent_id == parent_id


class TestVersionDiff:
    def test_create(self):
        a_id = uuid4()
        diff = VersionDiff(
            version_a=uuid4(),
            version_b=uuid4(),
            changes=[
                WorkProductChange(
                    work_product_id=a_id,
                    change_type="modified",
                    old_content_hash="old",
                    new_content_hash="new",
                )
            ],
        )
        assert len(diff.changes) == 1
        assert diff.changes[0].work_product_id == a_id


# --- Component ---


class TestComponent:
    def test_create_with_defaults(self):
        c = Component(
            part_number="STM32F407VG",
            manufacturer="STMicroelectronics",
        )
        assert c.node_type == NodeType.COMPONENT
        assert c.lifecycle == ComponentLifecycle.ACTIVE
        assert c.quantity == 1
        assert c.unit_cost is None

    def test_full_component(self):
        c = Component(
            part_number="RC0402FR-071KL",
            manufacturer="Yageo",
            description="1K 1% 0402 resistor",
            package="0402",
            lifecycle=ComponentLifecycle.ACTIVE,
            specs={"resistance": 1000, "tolerance": 0.01},
            alternates=["ERJ-2RKF1001X"],
            unit_cost=0.003,
            lead_time_days=14,
            quantity=50,
        )
        assert c.specs["resistance"] == 1000
        assert len(c.alternates) == 1


# --- AgentNode ---


class TestAgentNode:
    def test_create_with_defaults(self):
        a = AgentNode(
            agent_type="mechanical",
            domain="mechanical",
            session_id=uuid4(),
        )
        assert a.node_type == NodeType.AGENT
        assert a.status == "running"
        assert a.skills_used == []
        assert a.completed_at is None


# --- Edges ---


class TestEdgeBase:
    def test_create(self):
        e = EdgeBase(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.DEPENDS_ON,
        )
        assert e.metadata == {}
        assert e.created_at is not None


class TestDependsOnEdge:
    def test_defaults(self):
        e = DependsOnEdge(
            source_id=uuid4(),
            target_id=uuid4(),
        )
        assert e.edge_type == EdgeType.DEPENDS_ON
        assert e.dependency_type == "hard"
        assert e.description == ""

    def test_soft_dependency(self):
        e = DependsOnEdge(
            source_id=uuid4(),
            target_id=uuid4(),
            dependency_type="soft",
            description="Optional reference",
        )
        assert e.dependency_type == "soft"


class TestUsesComponentEdge:
    def test_defaults(self):
        e = UsesComponentEdge(
            source_id=uuid4(),
            target_id=uuid4(),
        )
        assert e.edge_type == EdgeType.USES_COMPONENT
        assert e.reference_designator == ""
        assert e.quantity == 1


class TestConstrainedByEdge:
    def test_defaults(self):
        e = ConstrainedByEdge(
            source_id=uuid4(),
            target_id=uuid4(),
        )
        assert e.edge_type == EdgeType.CONSTRAINED_BY
        assert e.scope == "local"
        assert e.priority == 0


# --- SubGraph ---


class TestSubGraph:
    def test_create_empty(self):
        sg = SubGraph(root_id=uuid4(), depth=2)
        assert sg.nodes == []
        assert sg.edges == []


# --- BOMItem ---


class TestBOMItem:
    def test_create_with_defaults(self):
        item = BOMItem(
            part_number="STM32F407VG",
            manufacturer="STMicroelectronics",
        )
        assert isinstance(item.id, UUID)
        assert item.node_type == NodeType.BOM_ITEM
        assert item.quantity == 1
        assert item.specifications == {}
        assert item.global_asset_id is None
        assert item.supplier is None
        assert item.reference_designators == []

    def test_full_bom_item(self):
        item = BOMItem(
            part_number="STM32F407VG",
            manufacturer="STMicroelectronics",
            description="ARM Cortex-M4 MCU",
            quantity=2,
            reference_designators=["U1", "U2"],
            unit_cost=8.50,
            specifications={
                "countryOfOrigin": "CN",
                "rohsCompliance": "compliant",
                "reachCompliance": "compliant",
                "customsTariffNumber": "8542.31",
                "weightGrams": 2.5,
            },
            global_asset_id="urn:metaforge:bom:STMicroelectronics:STM32F407VG",
            supplier="DigiKey",
        )
        assert item.supplier == "DigiKey"
        assert item.global_asset_id == "urn:metaforge:bom:STMicroelectronics:STM32F407VG"
        assert item.specifications["rohsCompliance"] == "compliant"
        assert item.specifications["weightGrams"] == 2.5

    def test_serialization_roundtrip(self):
        item = BOMItem(
            part_number="RC0402FR-071KL",
            manufacturer="Yageo",
            supplier="Mouser",
            global_asset_id="urn:metaforge:bom:Yageo:RC0402FR-071KL",
        )
        data = item.model_dump()
        restored = BOMItem.model_validate(data)
        assert restored.id == item.id
        assert restored.supplier == "Mouser"
        assert restored.global_asset_id == item.global_asset_id


# --- DeviceInstance ---


class TestDeviceInstance:
    def test_create_with_defaults(self):
        dev = DeviceInstance(
            serial_number="SN-2026-00042",
            product_id="drone-fc",
        )
        assert dev.node_type == NodeType.DEVICE_INSTANCE
        assert dev.firmware_version == ""
        assert dev.hardware_revision == ""
        assert dev.global_asset_id is None
        assert dev.manufactured_at is None

    def test_with_global_asset_id(self):
        dev = DeviceInstance(
            serial_number="SN-2026-00042",
            product_id="drone-fc",
            firmware_version="1.2.0",
            hardware_revision="rev-C",
            global_asset_id="urn:metaforge:device:SN-2026-00042",
        )
        assert dev.global_asset_id == "urn:metaforge:device:SN-2026-00042"
        assert dev.firmware_version == "1.2.0"

    def test_serialization_roundtrip(self):
        dev = DeviceInstance(
            serial_number="SN-001",
            product_id="widget-v2",
            global_asset_id="urn:metaforge:device:SN-001",
        )
        data = dev.model_dump()
        restored = DeviceInstance.model_validate(data)
        assert restored.id == dev.id
        assert restored.global_asset_id == dev.global_asset_id


# --- TwinModel ---


class TestTwinModel:
    def test_create_with_defaults(self):
        tm = TwinModel(
            product_id="drone-fc",
            version="1.0.0",
            name="Drone Flight Controller",
        )
        assert tm.node_type == NodeType.TWIN_MODEL
        assert tm.description == ""
        assert tm.global_asset_id is None
        assert tm.created_at is not None

    def test_with_global_asset_id(self):
        tm = TwinModel(
            product_id="drone-fc",
            version="1.0.0",
            name="Drone Flight Controller",
            global_asset_id="urn:metaforge:model:drone-fc:1.0.0",
        )
        assert tm.global_asset_id == "urn:metaforge:model:drone-fc:1.0.0"

    def test_serialization_roundtrip(self):
        tm = TwinModel(
            product_id="widget",
            version="2.1.0",
            name="Widget v2.1",
            global_asset_id="urn:metaforge:model:widget:2.1.0",
        )
        data = tm.model_dump()
        restored = TwinModel.model_validate(data)
        assert restored.id == tm.id
        assert restored.global_asset_id == tm.global_asset_id


# --- DesignElement ---


class TestDesignElement:
    def test_create_with_defaults(self):
        de = DesignElement(name="Power Module")
        assert de.node_type == NodeType.DESIGN_ELEMENT
        assert de.parameters == {}
        assert de.element_type == ""
        assert de.domain == ""

    def test_with_aas_parameters(self):
        de = DesignElement(
            name="Main Board",
            element_type="pcba",
            domain="electronics",
            parameters={
                "hardwareVersion": "rev-C",
                "softwareVersion": "1.2.0",
            },
        )
        assert de.parameters["hardwareVersion"] == "rev-C"
        assert de.parameters["softwareVersion"] == "1.2.0"

    def test_serialization_roundtrip(self):
        de = DesignElement(
            name="Sensor Array",
            parameters={"hardwareVersion": "rev-A"},
        )
        data = de.model_dump()
        restored = DesignElement.model_validate(data)
        assert restored.id == de.id
        assert restored.parameters == {"hardwareVersion": "rev-A"}
