"""Unit tests for the Baseline/BaselineMember/Confidence models (FORGE-51)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from twin_core.models.baseline import Baseline, BaselineMember
from twin_core.models.confidence import Confidence
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState, ConstraintSeverity


class TestConfidence:
    def test_value_must_be_within_zero_and_one(self):
        with pytest.raises(ValidationError):
            Confidence(value=1.5, basis="model_inference")

    def test_valid_confidence_constructs(self):
        c = Confidence(value=0.96, basis="model_inference")
        assert c.value == 0.96


class TestAuthorityDefaults:
    def test_constraint_defaults_to_proposed(self):
        c = Constraint(
            name="x",
            expression="true",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
        )
        assert c.authority == AuthorityState.PROPOSED
        assert c.confidence is None

    def test_engineering_entity_defaults_to_proposed(self):
        e = EngineeringEntity(entity_type="intent", statement="x")
        assert e.authority == AuthorityState.PROPOSED
        # StrEnum equality against the plain string still holds (FORGE-44
        # callers that compare against "proposed" keep working).
        assert e.authority == "proposed"

    def test_confidence_and_authority_are_independent(self):
        """A high-confidence value must never imply an authority change."""
        e = EngineeringEntity(
            entity_type="intent",
            statement="x",
            confidence=Confidence(value=0.99, basis="model_inference"),
        )
        assert e.authority == AuthorityState.PROPOSED


class TestBaselineModel:
    def test_requires_at_least_the_declared_fields(self):
        b = Baseline(
            name="BL-1",
            includes=[BaselineMember(entity_kind="constraint", entity_id=uuid4(), revision=1)],
            approved_by=["user:1"],
        )
        assert b.name == "BL-1"
        assert len(b.includes) == 1
