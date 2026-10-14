"""Material figures come only from the library (FORGE-446, FORGE-457)."""

from __future__ import annotations

import pytest

from tool_registry.material_context import extract, using
from tool_registry.tools.cadquery.materials import (
    MaterialNotResolvedError,
    material_source,
    resolve_density_kg_m3,
    resolve_elastic_properties,
    resolve_thermal_conductivity_w_mk,
    resolve_yield_mpa,
)

pytestmark = pytest.mark.no_material_library

AL = {
    "name": "aluminium_6061_t6",
    "properties": {
        "density_kg_m3": {"value": 2700, "source_ref": "https://example.com/ds"},
        "youngs_modulus_mpa": {"value": 68900},
        "poisson_ratio": {"value": 0.33},
        "yield_strength_mpa": {"value": 240},
        "thermal_conductivity_w_mk": {"value": 167},
    },
}


def test_figures_come_from_the_call_and_names_are_normalised() -> None:
    with using(extract({"material": "Aluminium 6061-T6", "material_properties": AL})):
        assert resolve_density_kg_m3("aluminium_6061_t6") == 2700
        assert resolve_density_kg_m3("ALUMINIUM-6061-T6") == 2700
        assert resolve_elastic_properties("aluminium_6061_t6") == (68900, 0.33)
        assert resolve_yield_mpa("aluminium_6061_t6") == 240
        assert resolve_thermal_conductivity_w_mk("aluminium_6061_t6") == 167
        assert "https://example.com/ds" in (
            material_source("aluminium_6061_t6", "density_kg_m3") or ""
        )


def test_an_explicit_figure_always_wins() -> None:
    assert resolve_density_kg_m3("anything", density_kg_m3=1.0) == 1.0
    assert resolve_yield_mpa(yield_mpa=999.0) == 999.0
    assert resolve_elastic_properties(None, 1.0, 0.3) == (1.0, 0.3)


def test_an_unresolved_material_is_refused_not_water() -> None:
    # FORGE-457: this used to be 1000 kg/m3, silently.
    with pytest.raises(MaterialNotResolvedError, match="not resolved against the material"):
        resolve_density_kg_m3("unobtainium")
    with pytest.raises(MaterialNotResolvedError, match="name a library material"):
        resolve_density_kg_m3("")


def test_a_missing_property_names_the_entry_and_the_fix() -> None:
    no_k = {"name": "aluminium_6061_t6", "properties": {"density_kg_m3": {"value": 2700}}}
    with using(extract({"material": "aluminium_6061_t6", "material_properties": no_k})):
        with pytest.raises(MaterialNotResolvedError, match="has no thermal_conductivity_w_mk"):
            resolve_thermal_conductivity_w_mk("aluminium_6061_t6")


def test_extract_finds_nested_object_and_candidate_forms() -> None:
    args = {
        "parts": [{"material": "steel", "material_properties": {**AL, "name": "steel"}}],
        "fea": {"material": {"name": "abs", "material_properties": {**AL, "name": "abs"}}},
        "candidate_materials": ["pla"],
        "candidate_material_properties": {"pla": {**AL, "name": "pla"}},
    }
    found = extract(args)
    assert set(found) == {"steel", "abs", "pla"}
    assert "material_properties" not in args["parts"][0]
    assert "material_properties" not in args["fea"]["material"]
    assert "candidate_material_properties" not in args
