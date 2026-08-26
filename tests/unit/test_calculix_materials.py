"""Unit tests for the CalculiX material property library.

The library's job is to hand CalculiX numbers in one consistent unit system
(N-mm-s-tonne-K). A property converted with the wrong factor produces a deck
that solves cleanly and reports stresses that are wrong by orders of magnitude,
so the conversions are asserted against hand-computed values rather than
against the library's own output.
"""

from __future__ import annotations

import pytest

from tool_registry.tools.calculix.materials import (
    MATERIALS,
    Material,
    UnknownMaterialError,
    get_material,
    list_materials,
    normalize_key,
    resolve_material,
)


class TestUnitConversions:
    def test_youngs_modulus_is_in_mpa(self) -> None:
        """Al6061-T6 is 68.9 GPa, which is 68900 MPa."""
        assert get_material("al6061_t6").youngs_modulus_mpa == pytest.approx(68900.0)

    def test_density_is_in_tonne_per_mm3(self) -> None:
        """2700 kg/m3 is 2.7e-9 tonne/mm3 -- twelve orders of magnitude."""
        assert get_material("al6061_t6").density_tonne_mm3 == pytest.approx(2.7e-9)

    def test_conductivity_is_numerically_equal_to_si(self) -> None:
        """W/(m*K) and mW/(mm*K) coincide, so the value passes through."""
        assert get_material("al6061_t6").thermal_conductivity == pytest.approx(167.0)

    def test_specific_heat_scales_by_1e6(self) -> None:
        """896 J/(kg*K) is 8.96e8 mJ/(tonne*K)."""
        assert get_material("al6061_t6").specific_heat == pytest.approx(8.96e8)

    def test_expansion_is_unchanged(self) -> None:
        """1/K is the same in both systems."""
        assert get_material("al6061_t6").thermal_expansion_per_k == pytest.approx(23.6e-6)

    def test_every_material_has_physical_properties(self) -> None:
        """No entry may carry a zero or negative property that divides."""
        for key, material in MATERIALS.items():
            assert material.youngs_modulus_mpa > 0, key
            assert 0.0 < material.poissons_ratio < 0.5, key
            assert material.density_tonne_mm3 > 0, key
            assert material.yield_strength_mpa > 0, key
            assert material.thermal_conductivity > 0, key
            assert material.specific_heat > 0, key

    def test_yield_never_exceeds_ultimate(self) -> None:
        """A yield strength above ultimate would be a transcription error."""
        for key, material in MATERIALS.items():
            assert material.yield_strength_mpa <= material.ultimate_strength_mpa, key


class TestLookup:
    @pytest.mark.parametrize(
        "spelling",
        ["Al6061-T6", "al6061_t6", "AL6061 T6", "al6061-t6", "  Al6061-T6  "],
    )
    def test_datasheet_spellings_resolve(self, spelling: str) -> None:
        assert get_material(spelling).key == "al6061_t6"

    @pytest.mark.parametrize(
        ("alias", "expected"),
        [
            ("aluminum_6061", "al6061_t6"),
            ("titanium", "ti6al4v"),
            ("Ti-6Al-4V", "ti6al4v"),
            ("mild steel", "steel_1018"),
            ("SS304", "stainless_304"),
            ("PC", "polycarbonate"),
            ("nylon", "nylon_pa12"),
        ],
    )
    def test_aliases_resolve(self, alias: str, expected: str) -> None:
        assert get_material(alias).key == expected

    def test_unknown_material_raises_and_lists_options(self) -> None:
        """Guessing a substitute would silently change the physics."""
        with pytest.raises(UnknownMaterialError) as excinfo:
            get_material("unobtainium")
        assert "unobtainium" in str(excinfo.value)
        assert "al6061_t6" in str(excinfo.value)

    def test_normalize_key_folds_separators(self) -> None:
        assert normalize_key("  Al 6061--T6 ") == "al_6061_t6"

    def test_list_materials_is_sorted(self) -> None:
        listed = list_materials()
        assert listed == sorted(listed)
        assert "ti6al4v" in listed


class TestResolveMaterial:
    def test_defaults_to_aluminium(self) -> None:
        assert resolve_material(None).key == "al6061_t6"

    def test_override_replaces_a_property(self) -> None:
        """A certified yield strength should not require a library entry."""
        resolved = resolve_material("steel_1018", {"yield_strength_mpa": 400.0})
        assert resolved.yield_strength_mpa == 400.0
        assert resolved.key == "steel_1018"
        assert resolved.youngs_modulus_mpa == get_material("steel_1018").youngs_modulus_mpa

    def test_unknown_override_is_ignored_not_fatal(self) -> None:
        """A stray key must not fail an otherwise valid analysis."""
        resolved = resolve_material("steel_1018", {"not_a_property": 1.0})
        assert resolved == get_material("steel_1018")

    def test_override_does_not_mutate_the_library(self) -> None:
        resolve_material("steel_1018", {"yield_strength_mpa": 1.0})
        assert get_material("steel_1018").yield_strength_mpa == 370.0

    def test_material_is_immutable(self) -> None:
        with pytest.raises(Exception):
            get_material("steel_1018").yield_strength_mpa = 1.0  # type: ignore[misc]


class TestSafetyFactor:
    def test_safety_factor_is_yield_over_stress(self) -> None:
        material = get_material("al6061_t6")
        assert material.safety_factor(92.0) == pytest.approx(3.0)

    def test_zero_stress_reports_infinite_margin(self) -> None:
        """An unloaded region has no margin to divide, not a zero margin."""
        assert get_material("al6061_t6").safety_factor(0.0) == float("inf")

    def test_negative_stress_reports_infinite_margin(self) -> None:
        assert get_material("al6061_t6").safety_factor(-5.0) == float("inf")

    def test_yielding_material_has_safety_factor_below_one(self) -> None:
        material = get_material("al6061_t6")
        assert material.safety_factor(material.yield_strength_mpa * 2) == pytest.approx(0.5)


class TestMaterialDataclass:
    def test_si_helper_round_trips_a_known_material(self) -> None:
        """Steel 1018 at 205 GPa must come back as 205000 MPa."""
        steel = get_material("steel_1018")
        assert isinstance(steel, Material)
        assert steel.youngs_modulus_mpa == pytest.approx(205000.0)
        assert steel.name == "Steel-1018"
