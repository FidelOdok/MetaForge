"""Unit tests for twin_core.prediction.evaluator (FORGE-315)."""

from __future__ import annotations

import pytest

from twin_core.prediction.evaluator import (
    cantilever_tip_deflection_mm,
    evaluate_tip_deflection_tier0,
)


class TestCantileverTipDeflectionMm:
    def test_matches_hand_computed_value(self) -> None:
        # delta = F L^3 / (3 E I); L=100mm, w=10mm, h=10mm -> I = 10*1000/12 = 833.33mm^4
        # E = 70000 MPa (aluminum), F = 50N
        # delta = 50 * 1e6 / (3 * 70000 * 833.333) = 50000000 / 175000000 = 0.28571...mm
        value = cantilever_tip_deflection_mm(
            length_mm=100.0, width_mm=10.0, height_mm=10.0, load_n=50.0, youngs_modulus_mpa=70000.0
        )
        assert value == pytest.approx(0.285714, rel=1e-4)

    def test_zero_load_gives_zero_deflection(self) -> None:
        value = cantilever_tip_deflection_mm(
            length_mm=100.0, width_mm=10.0, height_mm=10.0, load_n=0.0, youngs_modulus_mpa=70000.0
        )
        assert value == 0.0

    def test_longer_beam_deflects_more(self) -> None:
        short = cantilever_tip_deflection_mm(
            length_mm=100.0, width_mm=10.0, height_mm=10.0, load_n=50.0, youngs_modulus_mpa=70000.0
        )
        long = cantilever_tip_deflection_mm(
            length_mm=200.0, width_mm=10.0, height_mm=10.0, load_n=50.0, youngs_modulus_mpa=70000.0
        )
        # L^3 scaling -> 8x deflection for 2x length.
        assert long == pytest.approx(short * 8, rel=1e-6)

    def test_stiffer_material_deflects_less(self) -> None:
        soft = cantilever_tip_deflection_mm(
            length_mm=100.0, width_mm=10.0, height_mm=10.0, load_n=50.0, youngs_modulus_mpa=70000.0
        )
        stiff = cantilever_tip_deflection_mm(
            length_mm=100.0, width_mm=10.0, height_mm=10.0, load_n=50.0, youngs_modulus_mpa=210000.0
        )
        assert stiff == pytest.approx(soft / 3, rel=1e-6)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"length_mm": 0, "width_mm": 10, "height_mm": 10, "load_n": 1, "youngs_modulus_mpa": 1},
            {
                "length_mm": 10,
                "width_mm": -1,
                "height_mm": 10,
                "load_n": 1,
                "youngs_modulus_mpa": 1,
            },
            {"length_mm": 10, "width_mm": 10, "height_mm": 0, "load_n": 1, "youngs_modulus_mpa": 1},
            {
                "length_mm": 10,
                "width_mm": 10,
                "height_mm": 10,
                "load_n": 1,
                "youngs_modulus_mpa": 0,
            },
        ],
    )
    def test_invalid_dimensions_or_modulus_raise(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            cantilever_tip_deflection_mm(**kwargs)

    def test_negative_load_raises(self) -> None:
        with pytest.raises(ValueError, match="load_n"):
            cantilever_tip_deflection_mm(
                length_mm=100, width_mm=10, height_mm=10, load_n=-1, youngs_modulus_mpa=70000
            )


class TestEvaluateTipDeflectionTier0:
    def test_no_limit_returns_raw_estimate_no_escalation(self) -> None:
        result = evaluate_tip_deflection_tier0(
            length_mm=360, width_mm=40, height_mm=60, load_n=20, youngs_modulus_mpa=70000
        )
        assert result.limit_mm is None
        assert result.margin_mm is None
        assert result.escalate is False
        assert result.band_mm == 0.0
        assert result.value_mm > 0

    def test_well_within_band_does_not_escalate(self) -> None:
        # A tiny load against a generous limit -> value near zero, margin
        # near the full limit, nowhere near the band -> no escalation.
        result = evaluate_tip_deflection_tier0(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            load_n=0.01,
            youngs_modulus_mpa=70000,
            limit_mm=0.5,
            band_fraction=0.2,
        )
        assert result.escalate is False
        assert result.margin_mm is not None
        assert result.margin_mm > result.band_mm

    def test_deflection_deep_over_limit_escalates(self) -> None:
        # I = 40*60^3/12 = 720000mm^4; coefficient = L^3/(3EI) = 46656000/
        # 151200000000 = 3.0857e-4 mm/N. load_n=1500 -> value_mm ~= 0.4629mm,
        # margin ~= 0.0371mm < band (0.1mm) -> escalate.
        near = evaluate_tip_deflection_tier0(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            load_n=1500,
            youngs_modulus_mpa=70000,
            limit_mm=0.5,
            band_fraction=0.2,
        )
        assert near.escalate is True

    def test_margin_exactly_at_band_boundary_escalates(self) -> None:
        # margin == k*band is NOT "< k*band" -- construct exactly that case
        # and confirm the boundary is handled by strict inequality (escalate
        # is False exactly AT the boundary, True just inside it).
        result_at_boundary = evaluate_tip_deflection_tier0(
            length_mm=100,
            width_mm=10,
            height_mm=10,
            load_n=0,
            youngs_modulus_mpa=70000,
            limit_mm=1.0,
            band_fraction=1.0,
            escalation_k=1.0,
        )
        # value_mm=0 -> margin_mm = 1.0 = limit_mm; band_mm = 1.0*1.0 = 1.0.
        # margin (1.0) is NOT < band (1.0) -> no escalation.
        assert result_at_boundary.margin_mm == pytest.approx(1.0)
        assert result_at_boundary.band_mm == pytest.approx(1.0)
        assert result_at_boundary.escalate is False

    def test_escalation_k_widens_the_no_escalate_zone(self) -> None:
        # margin ~= 0.4954mm, band = 0.2*0.5 = 0.1mm -- k=0.1 -> k*band=0.01
        # (margin not < it, no escalate); k=5.0 -> k*band=0.5 (margin < it,
        # escalate) -- deterministic on both sides of the same margin/band.
        kwargs = dict(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            load_n=15,
            youngs_modulus_mpa=70000,
            limit_mm=0.5,
            band_fraction=0.2,
        )
        loose = evaluate_tip_deflection_tier0(**kwargs, escalation_k=0.1)
        strict = evaluate_tip_deflection_tier0(**kwargs, escalation_k=5.0)
        assert loose.escalate is False
        assert strict.escalate is True
