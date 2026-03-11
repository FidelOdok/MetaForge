"""Unit tests for the shared.types facade package (MET-208).

Verifies that:
1. All types are importable from shared.types
2. Re-exported types are identity-equal to their source
3. Models can be created and serialized round-trip
4. Common type aliases resolve correctly
5. __all__ is complete and consistent
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest


class TestSharedTypesImport:
    """Test that all types are importable from shared.types."""

    def test_import_all_types(self) -> None:
        """Happy path: every name in __all__ is importable."""
        import shared.types as st
        from shared.types import __all__ as all_names

        for name in all_names:
            attr = getattr(st, name, None)
            assert attr is not None, f"shared.types.{name} is not importable"

    def test_all_list_is_nonempty(self) -> None:
        """__all__ should contain a substantial number of exports."""
        from shared.types import __all__ as all_names

        # We expect at least 40 re-exports
        assert len(all_names) >= 40, f"Expected >=40 exports, got {len(all_names)}"


class TestIdentityReExports:
    """Re-exported types must be the exact same object as the source."""

    def test_artifact_identity(self) -> None:
        from shared.types import Artifact
        from twin_core.models import Artifact as SourceArtifact

        assert Artifact is SourceArtifact

    def test_constraint_identity(self) -> None:
        from shared.types import Constraint
        from twin_core.models import Constraint as SourceConstraint

        assert Constraint is SourceConstraint

    def test_node_base_identity(self) -> None:
        from shared.types import NodeBase
        from twin_core.models import NodeBase as SourceNodeBase

        assert NodeBase is SourceNodeBase

    def test_event_identity(self) -> None:
        from orchestrator.event_bus.events import Event as SourceEvent
        from shared.types import Event

        assert Event is SourceEvent

    def test_event_type_identity(self) -> None:
        from orchestrator.event_bus.events import EventType as SourceEventType
        from shared.types import EventType

        assert EventType is SourceEventType

    def test_gate_stage_identity(self) -> None:
        from digital_twin.thread.gate_engine.models import GateStage as SourceGateStage
        from shared.types import GateStage

        assert GateStage is SourceGateStage

    def test_gate_definition_identity(self) -> None:
        from digital_twin.thread.gate_engine.models import (
            GateDefinition as SourceGateDefinition,
        )
        from shared.types import GateDefinition

        assert GateDefinition is SourceGateDefinition

    def test_tool_manifest_identity(self) -> None:
        from mcp_core.schemas import ToolManifest as SourceToolManifest
        from shared.types import ToolManifest

        assert ToolManifest is SourceToolManifest

    def test_skill_definition_identity(self) -> None:
        from shared.types import SkillDefinition
        from skill_registry.schema_validator import (
            SkillDefinition as SourceSkillDefinition,
        )

        assert SkillDefinition is SourceSkillDefinition

    def test_readiness_score_identity(self) -> None:
        from digital_twin.thread.gate_engine.models import (
            ReadinessScore as SourceReadinessScore,
        )
        from shared.types import ReadinessScore

        assert ReadinessScore is SourceReadinessScore


class TestArtifactRoundTrip:
    """Artifact model creation and JSON serialization round-trip."""

    def test_create_and_serialize(self) -> None:
        from shared.types import Artifact, ArtifactType, NodeType

        artifact = Artifact(
            node_type=NodeType.ARTIFACT,
            name="main-schematic",
            type=ArtifactType.SCHEMATIC,
            domain="electronics",
            file_path="eda/kicad/main.kicad_sch",
            content_hash="abc123",
            format="kicad_sch",
            created_by="test-agent",
        )
        data = artifact.model_dump()
        restored = Artifact.model_validate(data)

        assert restored.name == "main-schematic"
        assert restored.type == ArtifactType.SCHEMATIC
        assert restored.file_path == "eda/kicad/main.kicad_sch"
        assert isinstance(restored.id, UUID)

    def test_json_round_trip(self) -> None:
        from shared.types import Artifact, ArtifactType, NodeType

        artifact = Artifact(
            node_type=NodeType.ARTIFACT,
            name="pcb-layout",
            type=ArtifactType.PCB_LAYOUT,
            domain="electronics",
            file_path="eda/kicad/main.kicad_pcb",
            content_hash="def456",
            format="kicad_pcb",
            created_by="test-agent",
        )
        json_str = artifact.model_dump_json()
        restored = Artifact.model_validate_json(json_str)

        assert restored.name == artifact.name
        assert restored.id == artifact.id


class TestEventModel:
    """Event model creation."""

    def test_create_event(self) -> None:
        from shared.types import Event, EventType

        event = Event(
            id=str(uuid4()),
            type=EventType.ARTIFACT_CREATED,
            timestamp=datetime.now(UTC).isoformat(),
            source="test-agent",
            data={"artifact_id": str(uuid4())},
        )
        assert event.type == EventType.ARTIFACT_CREATED
        assert event.source == "test-agent"

    def test_event_serialization(self) -> None:
        from shared.types import Event, EventType

        event = Event(
            id="abc-123",
            type=EventType.SESSION_STARTED,
            timestamp="2025-01-01T00:00:00Z",
            source="orchestrator",
        )
        data = event.model_dump()
        restored = Event.model_validate(data)
        assert restored.id == "abc-123"
        assert restored.type == EventType.SESSION_STARTED


class TestCommonTypeAliases:
    """Common type aliases resolve to the expected types."""

    def test_node_id_is_uuid(self) -> None:
        from shared.types import NodeId

        assert NodeId is UUID

    def test_version_id_is_uuid(self) -> None:
        from shared.types import VersionId

        assert VersionId is UUID

    def test_artifact_id_is_uuid(self) -> None:
        from shared.types import ArtifactId

        assert ArtifactId is UUID

    def test_timestamp_is_datetime(self) -> None:
        from shared.types import Timestamp

        assert Timestamp is datetime

    def test_session_id_is_uuid(self) -> None:
        from shared.types import SessionId

        assert SessionId is UUID

    def test_component_id_is_uuid(self) -> None:
        from shared.types import ComponentId

        assert ComponentId is UUID

    def test_constraint_id_is_uuid(self) -> None:
        from shared.types import ConstraintId

        assert ConstraintId is UUID


class TestGateModels:
    """Gate engine model creation from shared.types."""

    def test_gate_criterion_creation(self) -> None:
        from shared.types import GateCriterion, GateCriterionType

        criterion = GateCriterion(
            type=GateCriterionType.BOM_RISK,
            name="BOM Risk Check",
            description="Ensure BOM risk score is below threshold",
            weight=0.3,
            threshold=80.0,
        )
        assert criterion.type == GateCriterionType.BOM_RISK
        assert criterion.weight == 0.3

    def test_readiness_score_creation(self) -> None:
        from shared.types import GateStage, ReadinessScore

        score = ReadinessScore(
            stage=GateStage.EVT,
            overall_score=85.0,
            ready=True,
            evaluated_at=datetime.now(UTC),
        )
        assert score.stage == GateStage.EVT
        assert score.ready is True


class TestMcpSchemas:
    """MCP schema models from shared.types."""

    def test_tool_manifest_creation(self) -> None:
        from shared.types import ToolManifest

        manifest = ToolManifest(
            tool_id="calculix.run_fea",
            adapter_id="calculix",
            name="Run FEA",
            description="Run finite element analysis",
            capability="fea",
        )
        assert manifest.tool_id == "calculix.run_fea"

    def test_tool_call_request_creation(self) -> None:
        from shared.types import ToolCallRequest

        request = ToolCallRequest(
            tool_id="kicad.run_erc",
            arguments={"schematic_path": "main.kicad_sch"},
        )
        assert request.tool_id == "kicad.run_erc"
        assert request.timeout_seconds == 120  # default


class TestSkillDefinitionModel:
    """SkillDefinition from shared.types."""

    def test_skill_definition_creation(self) -> None:
        from shared.types import SkillDefinition

        defn = SkillDefinition(
            name="validate_stress",
            version="1.0.0",
            domain="mechanical",
            agent="mechanical",
            description="Validate stress analysis results from FEA",
            phase=1,
            input_schema="schema.ValidateStressInput",
            output_schema="schema.ValidateStressOutput",
        )
        assert defn.name == "validate_stress"
        assert defn.phase == 1

    def test_skill_definition_validation_error(self) -> None:
        """Invalid skill name should raise a validation error."""
        from pydantic import ValidationError

        from shared.types import SkillDefinition

        with pytest.raises(ValidationError):
            SkillDefinition(
                name="Invalid-Name",  # must be snake_case
                version="1.0.0",
                domain="mechanical",
                agent="mechanical",
                description="A description that is long enough",
                phase=1,
                input_schema="schema.Input",
                output_schema="schema.Output",
            )
