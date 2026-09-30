"""Tests for FEA accuracy controls (FORGE-280)."""

from __future__ import annotations

import pytest

from tool_registry.tools.calculix.accuracy import (
    assess_stress_accuracy,
    check_mesh_convergence,
    cross_check_cantilever_bending,
    cross_check_cantilever_frequency,
    cross_check_thermal_steady_state,
)


class TestAssessStressAccuracy:
    def test_uniform_field_is_not_suspicious(self) -> None:
        nodes = {i: 50.0 + i * 0.1 for i in range(1, 21)}  # tight spread
        result = assess_stress_accuracy({"nodes": nodes})
        assert result["suspicious"] is False
        assert result["reason"] is None
        assert result["max_to_median_ratio"] < 5.0

    def test_single_hot_node_is_flagged_suspicious(self) -> None:
        nodes = {i: 10.0 for i in range(1, 20)}
        nodes[20] = 200.0  # one node 20x the rest -- a classic singularity
        result = assess_stress_accuracy({"nodes": nodes})
        assert result["suspicious"] is True
        assert result["reason"] is not None
        assert "concentration" in result["reason"]
        assert result["max_to_median_ratio"] == pytest.approx(20.0)

    def test_empty_nodes_is_not_suspicious(self) -> None:
        result = assess_stress_accuracy({"nodes": {}})
        assert result["suspicious"] is False
        assert result["max_to_median_ratio"] is None

    def test_missing_nodes_key_is_not_suspicious(self) -> None:
        result = assess_stress_accuracy({})
        assert result["suspicious"] is False

    def test_zero_median_does_not_divide_by_zero(self) -> None:
        result = assess_stress_accuracy({"nodes": {1: 0.0, 2: 0.0, 3: 5.0}})
        assert result["suspicious"] is False
        assert result["max_to_median_ratio"] is None

    def test_threshold_boundary_exactly_at_ratio_is_not_suspicious(self) -> None:
        # median 10, max exactly 5x -> not > threshold, so not suspicious
        nodes = {1: 10.0, 2: 10.0, 3: 10.0, 4: 50.0}
        result = assess_stress_accuracy({"nodes": nodes})
        assert result["max_to_median_ratio"] == pytest.approx(5.0)
        assert result["suspicious"] is False


class TestCrossCheckCantileverBending:
    def test_matches_hand_calc_within_tolerance(self) -> None:
        # 100mm long, 10x20mm rectangular section, 500N tip load.
        # M = 500*100 = 50000 N.mm, c = 10mm, I = 10*20^3/12 = 6666.67 mm^4
        # sigma = 50000*10/6666.67 = 75.0 MPa
        result = cross_check_cantilever_bending(
            length_mm=100,
            width_mm=10,
            height_mm=20,
            force_n=500,
            fea_max_stress_mpa=76.0,
        )
        assert result["hand_calc_stress_mpa"] == pytest.approx(75.0, abs=0.1)
        assert result["within_tolerance"] is True
        assert result["percent_difference"] < 5.0

    def test_flags_a_real_mismatch(self) -> None:
        result = cross_check_cantilever_bending(
            length_mm=100,
            width_mm=10,
            height_mm=20,
            force_n=500,
            fea_max_stress_mpa=750.0,  # 10x too high -- the FORGE-239 bug shape
            tolerance_pct=20.0,
        )
        assert result["within_tolerance"] is False
        assert result["percent_difference"] > 100

    def test_custom_tolerance_is_honored(self) -> None:
        result = cross_check_cantilever_bending(
            length_mm=100,
            width_mm=10,
            height_mm=20,
            force_n=500,
            fea_max_stress_mpa=80.0,  # ~6.7% off hand calc
            tolerance_pct=5.0,
        )
        assert result["within_tolerance"] is False

    def test_non_positive_dimensions_raise(self) -> None:
        with pytest.raises(ValueError, match="must all be positive"):
            cross_check_cantilever_bending(
                length_mm=0, width_mm=10, height_mm=20, force_n=500, fea_max_stress_mpa=75.0
            )

    def test_non_positive_tolerance_raises(self) -> None:
        with pytest.raises(ValueError, match="tolerance_pct must be positive"):
            cross_check_cantilever_bending(
                length_mm=100,
                width_mm=10,
                height_mm=20,
                force_n=500,
                fea_max_stress_mpa=75.0,
                tolerance_pct=0,
            )


class TestCrossCheckCantileverFrequency:
    # 100mm long steel (E=200000 MPa, density=7850 kg/m^3), 10x20mm
    # rectangular section -- same beam geometry as the bending tests above.
    # f1 = (3.516/(2*pi*L^2)) * sqrt(E*I/(rho*A)), I=width*height^3/12=
    # 6666.67mm^4, A=200mm^2, rho=7.85e-9 tonne/mm^3 -- hand-computed
    # (independently of the function under test) to ~1631 Hz.
    def test_matches_hand_calc_within_tolerance(self) -> None:
        result = cross_check_cantilever_frequency(
            length_mm=100,
            width_mm=10,
            height_mm=20,
            density_kg_m3=7850,
            youngs_modulus_mpa=200000,
            fea_first_mode_hz=1650.0,
        )
        assert result["hand_calc_first_mode_hz"] == pytest.approx(1631, rel=0.02)
        assert result["within_tolerance"] is True
        assert result["percent_difference"] < 5.0

    def test_flags_a_real_mismatch(self) -> None:
        result = cross_check_cantilever_frequency(
            length_mm=100,
            width_mm=10,
            height_mm=20,
            density_kg_m3=7850,
            youngs_modulus_mpa=200000,
            fea_first_mode_hz=16310.0,  # 10x too high
            tolerance_pct=20.0,
        )
        assert result["within_tolerance"] is False
        assert result["percent_difference"] > 100

    def test_custom_tolerance_is_honored(self) -> None:
        # ~6.5% off the ~1631Hz hand calc.
        result = cross_check_cantilever_frequency(
            length_mm=100,
            width_mm=10,
            height_mm=20,
            density_kg_m3=7850,
            youngs_modulus_mpa=200000,
            fea_first_mode_hz=1737.0,
            tolerance_pct=5.0,
        )
        assert result["within_tolerance"] is False

    def test_non_positive_dimensions_raise(self) -> None:
        with pytest.raises(ValueError, match="must all be positive"):
            cross_check_cantilever_frequency(
                length_mm=0,
                width_mm=10,
                height_mm=20,
                density_kg_m3=7850,
                youngs_modulus_mpa=200000,
                fea_first_mode_hz=1631.0,
            )

    def test_non_positive_density_raises(self) -> None:
        with pytest.raises(ValueError, match="density_kg_m3 must be positive"):
            cross_check_cantilever_frequency(
                length_mm=100,
                width_mm=10,
                height_mm=20,
                density_kg_m3=0,
                youngs_modulus_mpa=200000,
                fea_first_mode_hz=1631.0,
            )

    def test_non_positive_modulus_raises(self) -> None:
        with pytest.raises(ValueError, match="youngs_modulus_mpa must be positive"):
            cross_check_cantilever_frequency(
                length_mm=100,
                width_mm=10,
                height_mm=20,
                density_kg_m3=7850,
                youngs_modulus_mpa=0,
                fea_first_mode_hz=1631.0,
            )

    def test_non_positive_tolerance_raises(self) -> None:
        with pytest.raises(ValueError, match="tolerance_pct must be positive"):
            cross_check_cantilever_frequency(
                length_mm=100,
                width_mm=10,
                height_mm=20,
                density_kg_m3=7850,
                youngs_modulus_mpa=200000,
                fea_first_mode_hz=1631.0,
                tolerance_pct=0,
            )


class TestCrossCheckThermalSteadyState:
    # 50mm conduction path, 10x10mm (100mm^2) cross-section, aluminum
    # (205 W/(m*K)), 5W dissipation, 20C sink. R = L/(k*A) =
    # 50/(0.205*100) = 2.439 K/W, T_rise = 5*2.439 = 12.195C, peak =
    # 32.195C -- hand-computed (independently of the function under test).
    def test_matches_hand_calc_within_tolerance(self) -> None:
        result = cross_check_thermal_steady_state(
            conduction_length_mm=50,
            cross_section_area_mm2=100,
            thermal_conductivity_w_mk=205,
            power_dissipation_w=5,
            sink_temp_c=20,
            fea_peak_temp_c=32.2,
        )
        assert result["hand_calc_peak_temp_c"] == pytest.approx(32.2, rel=0.01)
        assert result["within_tolerance"] is True
        assert result["percent_difference"] < 5.0

    def test_flags_a_real_mismatch(self) -> None:
        result = cross_check_thermal_steady_state(
            conduction_length_mm=50,
            cross_section_area_mm2=100,
            thermal_conductivity_w_mk=205,
            power_dissipation_w=5,
            sink_temp_c=20,
            fea_peak_temp_c=200.0,  # wildly too high
            tolerance_pct=20.0,
        )
        assert result["within_tolerance"] is False
        assert result["percent_difference"] > 100

    def test_custom_tolerance_is_honored(self) -> None:
        # ~13% off the ~12.2C hand-calc temperature rise.
        result = cross_check_thermal_steady_state(
            conduction_length_mm=50,
            cross_section_area_mm2=100,
            thermal_conductivity_w_mk=205,
            power_dissipation_w=5,
            sink_temp_c=20,
            fea_peak_temp_c=33.8,
            tolerance_pct=5.0,
        )
        assert result["within_tolerance"] is False

    def test_non_positive_conduction_length_raises(self) -> None:
        with pytest.raises(ValueError, match="conduction_length_mm must be positive"):
            cross_check_thermal_steady_state(
                conduction_length_mm=0,
                cross_section_area_mm2=100,
                thermal_conductivity_w_mk=205,
                power_dissipation_w=5,
                sink_temp_c=20,
                fea_peak_temp_c=32.2,
            )

    def test_non_positive_area_raises(self) -> None:
        with pytest.raises(ValueError, match="cross_section_area_mm2 must be positive"):
            cross_check_thermal_steady_state(
                conduction_length_mm=50,
                cross_section_area_mm2=0,
                thermal_conductivity_w_mk=205,
                power_dissipation_w=5,
                sink_temp_c=20,
                fea_peak_temp_c=32.2,
            )

    def test_non_positive_conductivity_raises(self) -> None:
        with pytest.raises(ValueError, match="thermal_conductivity_w_mk must be positive"):
            cross_check_thermal_steady_state(
                conduction_length_mm=50,
                cross_section_area_mm2=100,
                thermal_conductivity_w_mk=0,
                power_dissipation_w=5,
                sink_temp_c=20,
                fea_peak_temp_c=32.2,
            )

    def test_non_positive_tolerance_raises(self) -> None:
        with pytest.raises(ValueError, match="tolerance_pct must be positive"):
            cross_check_thermal_steady_state(
                conduction_length_mm=50,
                cross_section_area_mm2=100,
                thermal_conductivity_w_mk=205,
                power_dissipation_w=5,
                sink_temp_c=20,
                fea_peak_temp_c=32.2,
                tolerance_pct=0,
            )


class TestCheckMeshConvergence:
    def test_converged_when_last_change_is_small(self) -> None:
        points = [
            {"element_size_mm": 4.0, "max_von_mises_mpa": 100.0},
            {"element_size_mm": 2.0, "max_von_mises_mpa": 110.0},
            {"element_size_mm": 1.0, "max_von_mises_mpa": 111.0},
        ]
        result = check_mesh_convergence(points, tolerance_pct=5.0)
        assert result["converged"] is True
        assert "Converged" in result["recommendation"]
        # coarsest first
        assert result["points"][0]["element_size_mm"] == 4.0
        assert result["points"][-1]["element_size_mm"] == 1.0
        assert len(result["changes"]) == 2
        assert result["changes"][-1]["percent_change"] == pytest.approx(0.909, abs=0.01)

    def test_not_converged_when_still_changing(self) -> None:
        points = [
            {"element_size_mm": 4.0, "max_von_mises_mpa": 100.0},
            {"element_size_mm": 2.0, "max_von_mises_mpa": 140.0},
        ]
        result = check_mesh_convergence(points, tolerance_pct=5.0)
        assert result["converged"] is False
        assert "Not converged" in result["recommendation"]

    def test_order_of_input_does_not_matter(self) -> None:
        points_asc = [
            {"element_size_mm": 1.0, "max_von_mises_mpa": 111.0},
            {"element_size_mm": 4.0, "max_von_mises_mpa": 100.0},
            {"element_size_mm": 2.0, "max_von_mises_mpa": 110.0},
        ]
        result = check_mesh_convergence(points_asc, tolerance_pct=5.0)
        assert [p["element_size_mm"] for p in result["points"]] == [4.0, 2.0, 1.0]

    def test_fewer_than_two_points_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 2 points"):
            check_mesh_convergence([{"element_size_mm": 1.0, "max_von_mises_mpa": 100.0}])

    def test_non_positive_element_size_raises(self) -> None:
        with pytest.raises(ValueError, match="element_size_mm must be positive"):
            check_mesh_convergence(
                [
                    {"element_size_mm": 0.0, "max_von_mises_mpa": 100.0},
                    {"element_size_mm": 1.0, "max_von_mises_mpa": 100.0},
                ]
            )
