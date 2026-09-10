"""Unit tests for the material-density lookup used by URDF export."""

from __future__ import annotations

from tool_registry.tools.cadquery.materials import (
    DEFAULT_DENSITY_KG_M3,
    MATERIAL_DENSITY_KG_M3,
    resolve_density_kg_m3,
)


class TestResolveDensity:
    def test_known_material_name(self):
        assert resolve_density_kg_m3("aluminum_6061") == 2700.0

    def test_case_and_separator_insensitive(self):
        assert resolve_density_kg_m3("Aluminum 6061") == 2700.0
        assert resolve_density_kg_m3("ALUMINUM-6061") == 2700.0

    def test_explicit_density_overrides_material(self):
        assert resolve_density_kg_m3("steel", density_kg_m3=1.0) == 1.0

    def test_unrecognized_material_falls_back_to_default(self):
        assert resolve_density_kg_m3("unobtainium") == DEFAULT_DENSITY_KG_M3

    def test_empty_material_falls_back_to_default(self):
        assert resolve_density_kg_m3("") == DEFAULT_DENSITY_KG_M3

    def test_every_table_entry_is_a_positive_finite_density(self):
        for name, density in MATERIAL_DENSITY_KG_M3.items():
            assert density > 0, f"{name} has a non-positive density"
