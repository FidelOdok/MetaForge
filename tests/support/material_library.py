"""A test material library (FORGE-446).

The production code reads material figures only from the library. Tests
that drive CAD, FEA and optimiser code directly need figures for the names
they use, so this holds the values the retired hard-coded table had, as a
test double, the same way ``InMemoryTwinAPI`` stands in for the twin. These
are test fixtures, not sourced engineering data: nothing outside tests
imports them.
"""

from __future__ import annotations

from typing import Any

#: name -> property -> value, in the library's units.
TEST_MATERIALS: dict[str, dict[str, float]] = {
    "abs": {
        "density_kg_m3": 1040.0,
        "youngs_modulus_mpa": 2300.0,
        "poisson_ratio": 0.35,
        "yield_strength_mpa": 40.0,
        "thermal_conductivity_w_mk": 0.17,
    },
    "acrylic": {
        "density_kg_m3": 1180.0,
        "youngs_modulus_mpa": 3200.0,
        "poisson_ratio": 0.37,
        "yield_strength_mpa": 72.0,
        "thermal_conductivity_w_mk": 0.2,
    },
    "aluminum": {
        "density_kg_m3": 2700.0,
        "youngs_modulus_mpa": 69000.0,
        "poisson_ratio": 0.33,
        "yield_strength_mpa": 95.0,
        "thermal_conductivity_w_mk": 235.0,
    },
    "aluminium_6061_t6": {
        "density_kg_m3": 2700.0,
        "youngs_modulus_mpa": 68900.0,
        "poisson_ratio": 0.33,
        "yield_strength_mpa": 276.0,
        "thermal_conductivity_w_mk": 167.0,
    },
    "brass": {
        "density_kg_m3": 8500.0,
        "youngs_modulus_mpa": 100000.0,
        "poisson_ratio": 0.34,
        "yield_strength_mpa": 124.0,
        "thermal_conductivity_w_mk": 120.0,
    },
    "carbon_fiber": {
        "density_kg_m3": 1600.0,
        "youngs_modulus_mpa": 135000.0,
        "poisson_ratio": 0.3,
        "yield_strength_mpa": 600.0,
        "thermal_conductivity_w_mk": 5.0,
    },
    "copper": {
        "density_kg_m3": 8960.0,
        "youngs_modulus_mpa": 110000.0,
        "poisson_ratio": 0.34,
        "yield_strength_mpa": 70.0,
        "thermal_conductivity_w_mk": 401.0,
    },
    "nylon": {
        "density_kg_m3": 1150.0,
        "youngs_modulus_mpa": 2500.0,
        "poisson_ratio": 0.39,
        "yield_strength_mpa": 45.0,
        "thermal_conductivity_w_mk": 0.25,
    },
    "petg": {
        "density_kg_m3": 1270.0,
        "youngs_modulus_mpa": 2100.0,
        "poisson_ratio": 0.4,
        "yield_strength_mpa": 50.0,
        "thermal_conductivity_w_mk": 0.2,
    },
    "pla": {
        "density_kg_m3": 1250.0,
        "youngs_modulus_mpa": 3500.0,
        "poisson_ratio": 0.36,
        "yield_strength_mpa": 50.0,
        "thermal_conductivity_w_mk": 0.13,
    },
    "polycarbonate": {
        "density_kg_m3": 1200.0,
        "youngs_modulus_mpa": 2400.0,
        "poisson_ratio": 0.37,
        "yield_strength_mpa": 62.0,
        "thermal_conductivity_w_mk": 0.2,
    },
    "rubber": {
        "density_kg_m3": 1200.0,
        "youngs_modulus_mpa": 5.0,
        "poisson_ratio": 0.49,
        "yield_strength_mpa": 15.0,
        "thermal_conductivity_w_mk": 0.15,
    },
    "stainless_steel": {
        "density_kg_m3": 8000.0,
        "youngs_modulus_mpa": 193000.0,
        "poisson_ratio": 0.29,
        "yield_strength_mpa": 215.0,
        "thermal_conductivity_w_mk": 16.2,
    },
    "steel": {
        "density_kg_m3": 7850.0,
        "youngs_modulus_mpa": 200000.0,
        "poisson_ratio": 0.3,
        "yield_strength_mpa": 250.0,
        "thermal_conductivity_w_mk": 50.0,
    },
    "titanium": {
        "density_kg_m3": 4500.0,
        "youngs_modulus_mpa": 114000.0,
        "poisson_ratio": 0.34,
        "yield_strength_mpa": 275.0,
        "thermal_conductivity_w_mk": 17.0,
    },
    "wood": {"density_kg_m3": 600.0},
}


#: Titles as the real library has them, so title lookups behave the same.
TITLES = {"aluminium_6061_t6": "Aluminium 6061-T6"}


def block(name: str) -> dict[str, Any]:
    """The figures the dispatcher would attach for ``name``."""
    props = TEST_MATERIALS[name]
    return {
        "name": name,
        "entry_id": f"ma_test_{name}",
        "title": TITLES.get(name, name),
        "properties": {
            k: {"value": v, "unit": "", "source_kind": "user", "source_ref": "test double"}
            for k, v in props.items()
        },
    }


def figures() -> dict[str, dict[str, Any]]:
    """Every test material, keyed as the per-call context keys them."""
    from tool_registry.material_context import key

    return {key(n): block(n) for n in TEST_MATERIALS}


async def source(name: str) -> dict[str, Any]:
    """A material source over the test library, with the library's refusal."""
    from tool_registry.material_context import key
    from twin_core.library.material_resolver import MaterialResolutionError

    for n in TEST_MATERIALS:
        if key(name) in (key(n), key(TITLES.get(n, n))):
            return block(n)
    raise MaterialResolutionError(name, [])
