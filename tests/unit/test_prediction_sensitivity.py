"""Unit tests for twin_core.prediction.sensitivity (FORGE-317)."""

from __future__ import annotations

import pytest

from twin_core.prediction.evaluator import hollow_rect_moment_of_inertia_mm4, hollow_tube_mass_kg
from twin_core.prediction.sensitivity import (
    rank_deflection_sensitivity,
    rank_mass_sensitivity,
)


class TestHollowRectMomentOfInertia:
    def test_matches_solid_beam_when_wall_too_thick_for_a_cavity(self) -> None:
        # wall_thickness_mm >= min(width,height)/2 -> no cavity fits -> solid.
        solid = 40.0 * 60.0**3 / 12.0
        hollow = hollow_rect_moment_of_inertia_mm4(40.0, 60.0, wall_thickness_mm=30.0)
        assert hollow == pytest.approx(solid)

    def test_thinner_wall_gives_smaller_moment_of_inertia(self) -> None:
        thick_wall = hollow_rect_moment_of_inertia_mm4(40.0, 60.0, wall_thickness_mm=10.0)
        thin_wall = hollow_rect_moment_of_inertia_mm4(40.0, 60.0, wall_thickness_mm=2.0)
        assert thin_wall < thick_wall

    def test_zero_or_negative_wall_thickness_raises(self) -> None:
        with pytest.raises(ValueError):
            hollow_rect_moment_of_inertia_mm4(40.0, 60.0, wall_thickness_mm=0.0)


class TestHollowTubeMassKg:
    def test_thinner_wall_gives_less_mass(self) -> None:
        thick = hollow_tube_mass_kg(
            length_mm=360, width_mm=40, height_mm=60, wall_thickness_mm=10, density_kg_m3=2700
        )
        thin = hollow_tube_mass_kg(
            length_mm=360, width_mm=40, height_mm=60, wall_thickness_mm=2, density_kg_m3=2700
        )
        assert thin < thick

    def test_effectively_solid_beyond_max_wall_thickness(self) -> None:
        solid_volume_mm3 = 360 * 40 * 60
        mass = hollow_tube_mass_kg(
            length_mm=360, width_mm=40, height_mm=60, wall_thickness_mm=30, density_kg_m3=2700
        )
        assert mass == pytest.approx(solid_volume_mm3 * 2700 / 1e9)


class TestRankDeflectionSensitivity:
    def test_wall_thickness_increase_improves_margin_length_worsens_it(self) -> None:
        ranking = rank_deflection_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            load_n=50,
            youngs_modulus_mpa=68900,
            limit_mm=0.5,
        )
        by_param = {e.parameter: e for e in ranking.rankings}
        # Thicker wall -> stiffer -> less deflection -> MORE margin (positive).
        assert by_param["wall_thickness_mm"].sensitivity > 0
        # Longer beam -> more deflection -> LESS margin (negative).
        assert by_param["length_mm"].sensitivity < 0

    def test_rankings_sorted_by_magnitude_descending(self) -> None:
        ranking = rank_deflection_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            load_n=50,
            youngs_modulus_mpa=68900,
            limit_mm=0.5,
        )
        magnitudes = [abs(e.sensitivity) for e in ranking.rankings]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_metric_and_limit_recorded(self) -> None:
        ranking = rank_deflection_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            load_n=50,
            youngs_modulus_mpa=68900,
            limit_mm=0.5,
        )
        assert ranking.metric == "tip_deflection"
        assert ranking.limit == 0.5
        assert ranking.baseline_value > 0


class TestRankMassSensitivity:
    def test_heavier_candidate_material_worsens_margin(self) -> None:
        ranking = rank_mass_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            density_kg_m3=2700,  # aluminum
            limit_kg=4.5,
            material_name="aluminum_6061",
            candidate_material_densities={
                "aluminum_6061": 2700,
                "titanium": 4500,
                "carbon_fiber": 1600,
            },
        )
        by_param = {e.parameter: e for e in ranking.rankings}
        assert by_param["material=titanium"].sensitivity < 0
        assert by_param["material=titanium"].is_categorical is True

    def test_lighter_candidate_material_improves_margin(self) -> None:
        ranking = rank_mass_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            density_kg_m3=2700,
            limit_kg=4.5,
            material_name="aluminum_6061",
            candidate_material_densities={"aluminum_6061": 2700, "carbon_fiber": 1600},
        )
        by_param = {e.parameter: e for e in ranking.rankings}
        assert by_param["material=carbon_fiber"].sensitivity > 0

    def test_baseline_material_excluded_from_candidates(self) -> None:
        ranking = rank_mass_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            density_kg_m3=2700,
            limit_kg=4.5,
            material_name="aluminum_6061",
            candidate_material_densities={"aluminum_6061": 2700, "titanium": 4500},
        )
        assert "material=aluminum_6061" not in {e.parameter for e in ranking.rankings}

    def test_thicker_wall_worsens_mass_margin(self) -> None:
        ranking = rank_mass_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            density_kg_m3=2700,
            limit_kg=4.5,
            material_name="aluminum_6061",
        )
        by_param = {e.parameter: e for e in ranking.rankings}
        assert by_param["wall_thickness_mm"].sensitivity < 0

    def test_no_candidates_gives_only_wall_thickness_entry(self) -> None:
        ranking = rank_mass_sensitivity(
            length_mm=360,
            width_mm=40,
            height_mm=60,
            wall_thickness_mm=5,
            density_kg_m3=2700,
            limit_kg=4.5,
        )
        assert len(ranking.rankings) == 1
        assert ranking.rankings[0].parameter == "wall_thickness_mm"
