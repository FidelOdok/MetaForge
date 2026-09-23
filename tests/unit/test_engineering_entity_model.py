"""EngineeringEntity node model (FORGE-44, epic FORGE-35).

One generic node type for the Engineering Intent & Requirements Harness's
intent/need/objective/assumption/question/risk/verification_case/evidence
entities -- see the module docstring in ``twin_core/models/engineering_entity.py``
for why this is one class with an ``entity_type`` discriminator rather than
eight bespoke ones.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from twin_core.models import EngineeringEntity, NodeType


class TestEngineeringEntity:
    def test_create_with_defaults(self) -> None:
        e = EngineeringEntity(
            entity_type="intent",
            statement="Build a desktop quadruped platform for indoor demos.",
        )
        assert e.node_type == NodeType.ENGINEERING_ENTITY
        assert isinstance(e.id, UUID)
        assert e.status == "proposed"
        assert e.authority == "proposed"
        assert e.title is None
        assert e.source_refs == []
        assert e.parent_refs == []
        assert e.tags == []
        assert e.metadata == {}
        assert e.project_id is None

    @pytest.mark.parametrize(
        "entity_type",
        [
            "intent",
            "stakeholder_need",
            "objective",
            "assumption",
            "question",
            "risk",
            "verification_case",
            "evidence",
            "budget",
            "invariant",
            "waiver",
            "release_approval",
        ],
    )
    def test_every_spec_entity_type_is_accepted(self, entity_type: str) -> None:
        e = EngineeringEntity(entity_type=entity_type, statement="x")
        assert e.entity_type == entity_type

    def test_unknown_entity_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EngineeringEntity(entity_type="not_a_real_type", statement="x")

    def test_parent_refs_and_metadata_round_trip(self) -> None:
        e = EngineeringEntity(
            entity_type="stakeholder_need",
            statement="The operator needs the robot to operate safely indoors.",
            parent_refs=["INT-001"],
            metadata={"stakeholder": "STK-OPERATOR"},
            tags=["safety"],
            source_refs=["message-0183"],
        )
        assert e.parent_refs == ["INT-001"]
        assert e.metadata == {"stakeholder": "STK-OPERATOR"}
        assert e.tags == ["safety"]
        assert e.source_refs == ["message-0183"]

    def test_model_dump_round_trip(self) -> None:
        e = EngineeringEntity(entity_type="risk", statement="Actuator torque may be insufficient.")
        data = e.model_dump()
        restored = EngineeringEntity.model_validate(data)
        assert restored.id == e.id
        assert restored.entity_type == "risk"

    def test_default_mutable_fields_are_not_shared_across_instances(self) -> None:
        a = EngineeringEntity(entity_type="question", statement="a")
        b = EngineeringEntity(entity_type="question", statement="b")
        a.tags.append("blocker")
        assert b.tags == []
