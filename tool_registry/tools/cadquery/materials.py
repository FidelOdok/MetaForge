"""Material density lookup for physical (mass/inertia) properties.

``material`` has been accepted as a free-text string on ``create_parametric``/
``generate_cad_script`` since Phase 1, but nothing anywhere converted it to a
density -- geometry-only volume/inertia (unit-density, per
``Solid.MatrixOfInertia()``) was the only mass-adjacent data MetaForge ever
computed. URDF's ``<inertial>`` block needs real mass and mass-moments, so a
density table is the missing piece, not a nice-to-have.

Values are room-temperature nominal densities (kg/m^3) for common engineering
materials, not a certified materials database -- callers needing precision
for a specific alloy/temper should pass ``density_kg_m3`` explicitly rather
than rely on the name lookup.
"""

from __future__ import annotations

MATERIAL_DENSITY_KG_M3: dict[str, float] = {
    "aluminum_6061": 2700.0,
    "aluminum": 2700.0,
    "steel": 7850.0,
    "stainless_steel": 8000.0,
    "titanium": 4500.0,
    "brass": 8500.0,
    "copper": 8960.0,
    "abs": 1040.0,
    "pla": 1250.0,
    "petg": 1270.0,
    "nylon": 1150.0,
    "polycarbonate": 1200.0,
    "acrylic": 1180.0,
    "wood": 600.0,
    "carbon_fiber": 1600.0,
    "rubber": 1200.0,
}

DEFAULT_DENSITY_KG_M3 = 1000.0  # water -- a neutral fallback, never silently zero/undefined


def resolve_density_kg_m3(material: str, density_kg_m3: float | None = None) -> float:
    """Resolve a material name (or explicit override) to a density.

    An explicit ``density_kg_m3`` always wins. Otherwise looks up
    ``material`` case-insensitively with underscores/spaces/hyphens
    normalized (``"Aluminum 6061"`` and ``"aluminum-6061"`` both match
    ``"aluminum_6061"``); an unrecognized or empty name falls back to
    :data:`DEFAULT_DENSITY_KG_M3` rather than raising, since a URDF export
    should never hard-fail just because the material string was informal.
    """
    if density_kg_m3 is not None:
        return density_kg_m3
    key = material.strip().lower().replace(" ", "_").replace("-", "_")
    return MATERIAL_DENSITY_KG_M3.get(key, DEFAULT_DENSITY_KG_M3)
