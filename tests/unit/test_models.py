"""Unit tests for Digital Twin Pydantic models."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from twin_core.models import (
    AgentNode,
    Artifact,
    ArtifactChange,
    ArtifactType,
    Component,
    ComponentLifecycle,
    ConstrainedByEdge,
    Constraint,
    ConstraintSeverity,
    ConstraintStatus,
    DependsOnEdge,
    EdgeBase,
    EdgeType,
    NodeType,
    SubGraph,
    UsesComponentEdge,
    Version,
    VersionDiff,
)

# --- Artifact ---


class TestArtifact:
    def test_create_with_defaults(self):
        a = Artifact(
            name="main_schematic",
            type=ArtifactType.SCHEMATIC,
            domain="electronics",
            file_path="eda/kicad/main.kicad_sch",
            content_hash="abc123",
            format="kicad_sch",
            created_by="human",
        )
        assert isinstance(a.id, UUID)
        assert a.node_type == NodeType.ARTIFACT
        assert a.name == "main_schematic"
        assert a.metadata == {}
        assert a.created_at is not None

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            Artifact(
                name="test",
                type=ArtifactType.BOM,
                domain="electronics",
                # missing file_path, content_hash, format, created_by
            )

    def test_invalid_artifact_type(self):
        with pytest.raises(ValidationError):
            Artifact(
                name="test",
                type="not_a_real_type",
                domain="electronics",
                file_path="x",
                content_hash="x",
                format="x",
                created_by="human",
            )

    def test_serialization_roundtrip(self):
        a = Artifact(
            name="bom",
            type=ArtifactType.BOM,
            domain="electronics",
            file_path="bom/bom.csv",
            content_hash="def456",
            format="csv",
            created_by="agent",
            metadata={"total_cost": 42.5},
        )
        data = a.model_dump()
        restored = Artifact.model_validate(data)
        assert restored.id == a.id
        assert restored.metadata == {"total_cost": 42.5}


# --- Constraint ---


class TestConstraint:
    def test_create_with_defaults(self):
        c = Constraint(
            name="max_voltage_3v3",
            expression="ctx.artifact('power_budget').metadata.get('max_voltage', 0) <= 3.3",
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
        assert v.artifact_ids == []

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
                ArtifactChange(
                    artifact_id=a_id,
                    change_type="modified",
                    old_content_hash="old",
                    new_content_hash="new",
                )
            ],
        )
        assert len(diff.changes) == 1
        assert diff.changes[0].artifact_id == a_id


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
