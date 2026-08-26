"""Material property library for CalculiX input decks.

CalculiX is unit-agnostic: it solves whatever numbers the deck contains. That
makes the *unit system* a fidelity concern rather than a formatting detail -- a
density expressed in kg/m3 next to a modulus in MPa silently produces stresses
that are wrong by twelve orders of magnitude.

Every property in this module is stored in the **N-mm-s-tonne-K** consistent
system, which is the standard companion to a millimetre mesh:

===================  ==============================  ==========================
Quantity             Unit                            Conversion from SI
===================  ==============================  ==========================
Length               mm                              m x 1e3
Force                N                               N x 1
Stress / modulus     MPa (N/mm2)                     Pa x 1e-6
Mass                 tonne (1000 kg)                 kg x 1e-3
Density              tonne/mm3                       kg/m3 x 1e-12
Time                 s                               s x 1
Energy               mJ (N*mm)                       J x 1e3
Power                mW (N*mm/s)                     W x 1e3
Conductivity         mW/(mm*K)                       W/(m*K) x 1  (identity)
Specific heat        mJ/(tonne*K)                    J/(kg*K) x 1e6
Expansion            1/K                             1/K x 1
===================  ==============================  ==========================

Stresses recovered from a deck written in this system come out in MPa, which is
what the rest of MetaForge (constraints, safety factors, work products) assumes.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# Conversion factors from SI into the N-mm-s-tonne-K consistent system.
_PA_TO_MPA = 1e-6
_KG_M3_TO_TONNE_MM3 = 1e-12
_J_KG_K_TO_MJ_TONNE_K = 1e6


class UnknownMaterialError(KeyError):
    """Raised when a material identifier cannot be resolved."""

    def __init__(self, identifier: str, known: list[str]) -> None:
        self.identifier = identifier
        self.known = known
        super().__init__(f"Unknown material '{identifier}'. Known materials: {', '.join(known)}")


@dataclass(frozen=True)
class Material:
    """Mechanical and thermal properties in the N-mm-s-tonne-K system.

    Attributes:
        key: Canonical lookup key (lowercase, underscore-separated).
        name: Human-readable name used as the CalculiX ``*MATERIAL`` name.
        youngs_modulus_mpa: Elastic modulus E, in MPa.
        poissons_ratio: Poisson's ratio nu, dimensionless.
        density_tonne_mm3: Mass density rho, in tonne/mm3.
        yield_strength_mpa: Yield strength, in MPa. Used for safety factors.
        ultimate_strength_mpa: Ultimate tensile strength, in MPa.
        thermal_conductivity: k, in mW/(mm*K) -- numerically equal to W/(m*K).
        thermal_expansion_per_k: Linear expansion coefficient alpha, in 1/K.
        specific_heat: c, in mJ/(tonne*K).
        reference_temperature_k: Temperature at which alpha is referenced.
    """

    key: str
    name: str
    youngs_modulus_mpa: float
    poissons_ratio: float
    density_tonne_mm3: float
    yield_strength_mpa: float
    ultimate_strength_mpa: float
    thermal_conductivity: float
    thermal_expansion_per_k: float
    specific_heat: float
    reference_temperature_k: float = 293.15

    def safety_factor(self, von_mises_mpa: float) -> float:
        """Return yield strength divided by an applied von Mises stress.

        A non-positive stress means the solve produced no load in this region;
        that is reported as infinite margin rather than a division error.
        """
        if von_mises_mpa <= 0.0:
            return float("inf")
        return self.yield_strength_mpa / von_mises_mpa


def _si(
    key: str,
    name: str,
    *,
    e_gpa: float,
    nu: float,
    density_kg_m3: float,
    yield_mpa: float,
    ultimate_mpa: float,
    conductivity_w_mk: float,
    expansion_per_k: float,
    specific_heat_j_kgk: float,
) -> Material:
    """Build a Material from familiar SI datasheet values.

    Datasheets quote GPa, kg/m3, W/(m*K) and J/(kg*K); this converts them once,
    here, so no call site has to remember the consistent-unit factors.
    """
    return Material(
        key=key,
        name=name,
        youngs_modulus_mpa=e_gpa * 1e9 * _PA_TO_MPA,
        poissons_ratio=nu,
        density_tonne_mm3=density_kg_m3 * _KG_M3_TO_TONNE_MM3,
        yield_strength_mpa=yield_mpa,
        ultimate_strength_mpa=ultimate_mpa,
        thermal_conductivity=conductivity_w_mk,
        thermal_expansion_per_k=expansion_per_k,
        specific_heat=specific_heat_j_kgk * _J_KG_K_TO_MJ_TONNE_K,
    )


#: Canonical material library. Values are room-temperature handbook figures for
#: the stated temper/grade; they are design-review inputs, not certifications.
MATERIALS: dict[str, Material] = {
    m.key: m
    for m in (
        _si(
            "al6061_t6",
            "Al6061-T6",
            e_gpa=68.9,
            nu=0.33,
            density_kg_m3=2700.0,
            yield_mpa=276.0,
            ultimate_mpa=310.0,
            conductivity_w_mk=167.0,
            expansion_per_k=23.6e-6,
            specific_heat_j_kgk=896.0,
        ),
        _si(
            "al7075_t6",
            "Al7075-T6",
            e_gpa=71.7,
            nu=0.33,
            density_kg_m3=2810.0,
            yield_mpa=503.0,
            ultimate_mpa=572.0,
            conductivity_w_mk=130.0,
            expansion_per_k=23.6e-6,
            specific_heat_j_kgk=960.0,
        ),
        _si(
            "steel_1018",
            "Steel-1018",
            e_gpa=205.0,
            nu=0.29,
            density_kg_m3=7870.0,
            yield_mpa=370.0,
            ultimate_mpa=440.0,
            conductivity_w_mk=51.9,
            expansion_per_k=11.5e-6,
            specific_heat_j_kgk=486.0,
        ),
        _si(
            "steel_4140",
            "Steel-4140",
            e_gpa=205.0,
            nu=0.29,
            density_kg_m3=7850.0,
            yield_mpa=655.0,
            ultimate_mpa=1020.0,
            conductivity_w_mk=42.6,
            expansion_per_k=12.3e-6,
            specific_heat_j_kgk=473.0,
        ),
        _si(
            "stainless_304",
            "Stainless-304",
            e_gpa=193.0,
            nu=0.29,
            density_kg_m3=8000.0,
            yield_mpa=215.0,
            ultimate_mpa=505.0,
            conductivity_w_mk=16.2,
            expansion_per_k=17.3e-6,
            specific_heat_j_kgk=500.0,
        ),
        _si(
            "ti6al4v",
            "Ti-6Al-4V",
            e_gpa=113.8,
            nu=0.342,
            density_kg_m3=4430.0,
            yield_mpa=880.0,
            ultimate_mpa=950.0,
            conductivity_w_mk=6.7,
            expansion_per_k=8.6e-6,
            specific_heat_j_kgk=526.3,
        ),
        _si(
            "copper_c110",
            "Copper-C110",
            e_gpa=117.0,
            nu=0.34,
            density_kg_m3=8940.0,
            yield_mpa=69.0,
            ultimate_mpa=220.0,
            conductivity_w_mk=391.0,
            expansion_per_k=17.0e-6,
            specific_heat_j_kgk=385.0,
        ),
        _si(
            "brass_360",
            "Brass-360",
            e_gpa=97.0,
            nu=0.31,
            density_kg_m3=8500.0,
            yield_mpa=124.0,
            ultimate_mpa=338.0,
            conductivity_w_mk=115.0,
            expansion_per_k=20.5e-6,
            specific_heat_j_kgk=380.0,
        ),
        _si(
            "abs",
            "ABS",
            e_gpa=2.3,
            nu=0.35,
            density_kg_m3=1040.0,
            yield_mpa=40.0,
            ultimate_mpa=44.0,
            conductivity_w_mk=0.17,
            expansion_per_k=90.0e-6,
            specific_heat_j_kgk=1400.0,
        ),
        _si(
            "pla",
            "PLA",
            e_gpa=3.5,
            nu=0.36,
            density_kg_m3=1240.0,
            yield_mpa=50.0,
            ultimate_mpa=60.0,
            conductivity_w_mk=0.13,
            expansion_per_k=68.0e-6,
            specific_heat_j_kgk=1800.0,
        ),
        _si(
            "petg",
            "PETG",
            e_gpa=2.1,
            nu=0.38,
            density_kg_m3=1270.0,
            yield_mpa=50.0,
            ultimate_mpa=53.0,
            conductivity_w_mk=0.29,
            expansion_per_k=68.0e-6,
            specific_heat_j_kgk=1200.0,
        ),
        _si(
            "nylon_pa12",
            "Nylon-PA12",
            e_gpa=1.7,
            nu=0.39,
            density_kg_m3=1010.0,
            yield_mpa=48.0,
            ultimate_mpa=48.0,
            conductivity_w_mk=0.25,
            expansion_per_k=110.0e-6,
            specific_heat_j_kgk=1200.0,
        ),
        _si(
            "polycarbonate",
            "Polycarbonate",
            e_gpa=2.4,
            nu=0.37,
            density_kg_m3=1200.0,
            yield_mpa=62.0,
            ultimate_mpa=66.0,
            conductivity_w_mk=0.20,
            expansion_per_k=65.0e-6,
            specific_heat_j_kgk=1200.0,
        ),
        _si(
            "fr4",
            "FR4",
            e_gpa=24.0,
            nu=0.136,
            density_kg_m3=1850.0,
            yield_mpa=310.0,
            ultimate_mpa=415.0,
            conductivity_w_mk=0.29,
            expansion_per_k=14.0e-6,
            specific_heat_j_kgk=1100.0,
        ),
    )
}

#: Spellings seen in PRDs, Twin work products and datasheets that should resolve
#: to a canonical key. Lookup also normalises case, spaces, dashes and dots, so
#: only genuinely different names need an entry here.
_ALIASES: dict[str, str] = {
    "al6061": "al6061_t6",
    "aluminium_6061": "al6061_t6",
    "aluminum_6061": "al6061_t6",
    "6061": "al6061_t6",
    "al7075": "al7075_t6",
    "aluminium_7075": "al7075_t6",
    "aluminum_7075": "al7075_t6",
    "7075": "al7075_t6",
    "steel": "steel_1018",
    "mild_steel": "steel_1018",
    "1018": "steel_1018",
    "4140": "steel_4140",
    "ss304": "stainless_304",
    "304": "stainless_304",
    "304_stainless": "stainless_304",
    "stainless_steel": "stainless_304",
    "titanium": "ti6al4v",
    "ti_6al_4v": "ti6al4v",
    "grade_5_titanium": "ti6al4v",
    "copper": "copper_c110",
    "c110": "copper_c110",
    "brass": "brass_360",
    "c360": "brass_360",
    "pc": "polycarbonate",
    "pa12": "nylon_pa12",
    "nylon": "nylon_pa12",
    "fr_4": "fr4",
}


def normalize_key(identifier: str) -> str:
    """Fold a free-form material name into a canonical lookup key.

    ``"Al6061-T6"``, ``"al 6061 t6"`` and ``"AL6061_T6"`` all fold to
    ``"al6061_t6"``.
    """
    folded = identifier.strip().lower()
    for char in (" ", "-", ".", "/"):
        folded = folded.replace(char, "_")
    while "__" in folded:
        folded = folded.replace("__", "_")
    return folded.strip("_")


def get_material(identifier: str) -> Material:
    """Resolve a material identifier to its properties.

    Args:
        identifier: Canonical key, alias, or datasheet spelling.

    Returns:
        The matching :class:`Material`.

    Raises:
        UnknownMaterialError: If the identifier resolves to nothing. Guessing a
            substitute would silently change the physics, so this is fatal.
    """
    key = normalize_key(identifier)
    key = _ALIASES.get(key, key)

    material = MATERIALS.get(key)
    if material is None:
        raise UnknownMaterialError(identifier, sorted(MATERIALS))

    return material


def resolve_material(
    identifier: str | None,
    overrides: dict[str, float] | None = None,
) -> Material:
    """Resolve a material and apply per-analysis property overrides.

    Callers that carry measured or vendor-specific values (a certified yield
    strength, a filled-polymer conductivity) can override individual properties
    without adding a library entry.

    Args:
        identifier: Material name; ``None`` defaults to ``al6061_t6``.
        overrides: Property name -> value, in the consistent unit system. Names
            that are not :class:`Material` fields are ignored with a warning so
            a stray key never fails an otherwise valid analysis.

    Returns:
        The resolved material, with overrides applied.
    """
    base = get_material(identifier or "al6061_t6")
    if not overrides:
        return base

    fields = {f for f in base.__dataclass_fields__ if f not in ("key", "name")}
    applied: dict[str, float] = {}
    for prop, value in overrides.items():
        if prop in fields:
            applied[prop] = float(value)
        else:
            logger.warning(
                "Ignoring unknown material property override",
                material=base.key,
                property=prop,
            )

    if not applied:
        return base

    logger.info(
        "Applied material property overrides",
        material=base.key,
        overrides=sorted(applied),
    )
    return Material(**{**base.__dict__, **applied})


def list_materials() -> list[str]:
    """Return every canonical material key, sorted."""
    return sorted(MATERIALS)
