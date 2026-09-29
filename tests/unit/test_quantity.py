"""Unit tests for Quantity (FORGE-311)."""

import pytest
from pydantic import ValidationError

from twin_core.models.quantity import IncompatibleUnitsError, Quantity, is_valid_unit


class TestIsValidUnit:
    def test_recognizes_real_units(self):
        assert is_valid_unit("kg")
        assert is_valid_unit("mm")
        assert is_valid_unit("N*m")

    def test_recognizes_registered_currencies(self):
        assert is_valid_unit("usd")
        assert is_valid_unit("gbp")

    def test_rejects_nonsense_unit(self):
        assert not is_valid_unit("not_a_real_unit_xyz")

    def test_never_raises(self):
        assert is_valid_unit("") is False
        assert is_valid_unit("!!!") is False


class TestQuantityConstruction:
    def test_valid_unit_constructs(self):
        q = Quantity(value=1.0, unit="kg")
        assert q.value == 1.0

    def test_invalid_unit_rejected(self):
        with pytest.raises(ValidationError):
            Quantity(value=1.0, unit="not_a_real_unit_xyz")


class TestConversion:
    def test_converts_compatible_units(self):
        q = Quantity(value=1.2, unit="m")
        converted = q.to("mm")
        assert converted.value == pytest.approx(1200.0)
        assert converted.unit == "mm"

    def test_deflection_mm_to_m_acceptance_example(self):
        # Ticket's own acceptance example: 0.5mm limit vs. a value in metres.
        limit = Quantity(value=0.5, unit="mm")
        actual = Quantity(value=0.0003, unit="m")
        assert actual.to("mm").value == pytest.approx(0.3)
        assert actual.to("mm").value < limit.value

    def test_incompatible_dimension_raises(self):
        with pytest.raises(IncompatibleUnitsError):
            Quantity(value=4.5, unit="kg").to("mm")

    def test_mass_kg_vs_mm_acceptance_example(self):
        # Ticket's own acceptance example: mass <= 4.5kg against a value in
        # mm is rejected.
        with pytest.raises(IncompatibleUnitsError):
            Quantity(value=12.0, unit="mm").to("kg")

    def test_uncertainty_scales_with_conversion(self):
        q = Quantity(value=1.0, unit="m", uncertainty=0.01)
        converted = q.to("mm")
        assert converted.uncertainty == pytest.approx(10.0)

    def test_uncertainty_none_stays_none(self):
        q = Quantity(value=1.0, unit="m")
        assert q.to("mm").uncertainty is None

    def test_cross_currency_conversion_rejected(self):
        with pytest.raises(IncompatibleUnitsError):
            Quantity(value=100.0, unit="usd").to("gbp")

    def test_same_currency_round_trips(self):
        q = Quantity(value=100.0, unit="usd")
        assert q.to("usd").value == pytest.approx(100.0)


class TestCompatibleWith:
    def test_true_for_same_dimension(self):
        assert Quantity(value=1.0, unit="kg").compatible_with("g")

    def test_false_for_different_dimension(self):
        assert not Quantity(value=1.0, unit="kg").compatible_with("mm")


class TestSerialization:
    def test_round_trips_through_dict(self):
        q = Quantity(value=2.5, unit="kg", uncertainty=0.1)
        restored = Quantity.from_dict(q.to_dict())
        assert restored == q

    def test_omits_uncertainty_key_when_none(self):
        q = Quantity(value=2.5, unit="kg")
        d = q.to_dict()
        assert "uncertainty" not in d

    def test_from_dict_tolerates_missing_uncertainty(self):
        restored = Quantity.from_dict({"value": 1.0, "unit": "kg"})
        assert restored.uncertainty is None
