"""Unit tests for InterfaceQuantity/PredictedValue/MeasuredValue (FORGE-313)."""

import pytest
from pydantic import ValidationError

from twin_core.models.interface import InterfaceQuantity, MeasuredValue, PredictedValue


class TestInterfaceQuantity:
    def test_minimal_construction(self):
        q = InterfaceQuantity(metric="tip_deflection", unit="mm")
        assert q.metric == "tip_deflection"
        assert q.op == "<="
        assert q.predicted is None
        assert q.measured == []

    def test_requires_a_metric(self):
        with pytest.raises(ValidationError):
            InterfaceQuantity(metric="", unit="mm")

    def test_rejects_unrecognized_unit(self):
        with pytest.raises(ValidationError, match="not a recognized unit"):
            InterfaceQuantity(metric="tip_deflection", unit="not_a_real_unit_xyz")

    def test_empty_unit_is_allowed(self):
        # A dimensionless / not-yet-quantified metric is a real, legitimate
        # early-architecture state -- not every metric has a unit at all.
        q = InterfaceQuantity(metric="signal_integrity", unit="")
        assert q.unit == ""

    def test_predicted_and_measured_round_trip(self):
        q = InterfaceQuantity(
            metric="tip_deflection",
            unit="mm",
            limit=0.5,
            op="<=",
            owner="mechanical",
            discipline="mechanical",
            predicted=PredictedValue(value=0.3, band=0.05, tier="fea", evidence="ev-1"),
            measured=[MeasuredValue(value=0.31, source="dial indicator", timestamp="2026-09-29")],
        )
        dumped = q.model_dump()
        assert dumped["predicted"]["value"] == 0.3
        assert dumped["measured"][0]["source"] == "dial indicator"

    def test_j2_torque_example_from_acceptance_criterion(self):
        q = InterfaceQuantity(
            metric="J2_torque", unit="N*m", limit=12.0, op="<=", owner="actuator_lead"
        )
        assert q.limit == 12.0
