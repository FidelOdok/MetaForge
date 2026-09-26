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


# FORGE-234: linear-elastic properties for CalculiX static stress analysis.
# (Young's modulus in MPa == N/mm^2, Poisson's ratio, dimensionless) --
# MPa, not Pa, because every FreeCAD/gmsh-generated mesh's node coordinates
# are in millimeters, and a consistent CalculiX unit system needs
# Length=mm + Force=N + Stress=MPa together. Mixing E in Pa with mm-scale
# geometry would silently understate stiffness by 1e6 -- displacement and
# stress wrong by six orders of magnitude, not a rounding error. Room-
# temperature nominal values, not a certified materials database -- same
# caveat as MATERIAL_DENSITY_KG_M3 above. carbon_fiber is an isotropic
# approximation (real carbon fiber is strongly anisotropic); good enough
# for an order-of-magnitude structural check, not a composite layup design.
MATERIAL_ELASTIC_MPA: dict[str, tuple[float, float]] = {
    "aluminum_6061": (68900.0, 0.33),
    "aluminum": (69000.0, 0.33),
    "steel": (200000.0, 0.30),
    "stainless_steel": (193000.0, 0.29),
    "titanium": (114000.0, 0.34),
    "brass": (100000.0, 0.34),
    "copper": (110000.0, 0.34),
    "abs": (2300.0, 0.35),
    "pla": (3500.0, 0.36),
    "petg": (2100.0, 0.40),
    "nylon": (2500.0, 0.39),
    "polycarbonate": (2400.0, 0.37),
    "acrylic": (3200.0, 0.37),
    "carbon_fiber": (135000.0, 0.30),
    "rubber": (5.0, 0.49),
}


def resolve_elastic_properties(
    material: str | None = None,
    youngs_modulus_mpa: float | None = None,
    poissons_ratio: float | None = None,
) -> tuple[float, float]:
    """Resolve ``(youngs_modulus_mpa, poissons_ratio)`` for a static FEA solve.

    An explicit ``(youngs_modulus_mpa, poissons_ratio)`` pair always wins.
    Otherwise looks up ``material`` (same name normalization as
    :func:`resolve_density_kg_m3`). Unlike density, an unrecognized or
    missing material RAISES rather than silently defaulting: a wrong
    elastic modulus feeds directly into a stress/displacement number an
    engineer might trust as real, so guessing "steel" for an unrecognized
    plastic would be a silent, potentially unsafe wrong answer -- not a
    harmless inertial estimate the way an approximate mass is.
    """
    if youngs_modulus_mpa is not None and poissons_ratio is not None:
        return youngs_modulus_mpa, poissons_ratio
    if not material:
        raise ValueError(
            "resolve_elastic_properties: provide either a recognized material name, "
            "or both youngs_modulus_mpa and poissons_ratio explicitly."
        )
    key = material.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in MATERIAL_ELASTIC_MPA:
        raise ValueError(
            f"resolve_elastic_properties: unknown material {material!r} -- accepted: "
            f"{sorted(MATERIAL_ELASTIC_MPA)}, or pass youngs_modulus_mpa + "
            "poissons_ratio explicitly."
        )
    return MATERIAL_ELASTIC_MPA[key]
