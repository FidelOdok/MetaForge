"""Material properties for the call in progress, from the library (FORGE-446).

There used to be four hard-coded tables here (density, elastic, yield,
thermal conductivity: sixteen generic names, no sources), and an unknown
material silently got the density of water (FORGE-457). They are gone.

Material properties live in the library (``twin_core.library.catalog``,
on the gateway). The dispatcher resolves every material a tool call names
against it and passes the figures with the call
(``tool_registry.material_context``); these functions read them. So:

* an explicit figure passed by the caller always wins;
* otherwise the library's figure for that material, with its source;
* otherwise :class:`MaterialNotResolvedError`, naming the material and the
  property. Never a default, never a guess: a wrong density is a wrong
  mass and inertia, and a wrong modulus, yield or conductivity is a wrong
  number an engineer may trust.

A material the library does not hold, or holds without the property asked
for, is refused, and the message says how to fix it (record the property
with ``library.update_material``, or pass the figure explicitly).
"""

from __future__ import annotations

from tool_registry.material_context import lookup

__all__ = [
    "MaterialNotResolvedError",
    "material_source",
    "resolve_density_kg_m3",
    "resolve_elastic_properties",
    "resolve_thermal_conductivity_w_mk",
    "resolve_yield_mpa",
]


class MaterialNotResolvedError(ValueError):
    """A material, or one of its properties, is not available from the library."""


def _figure(func: str, material: str | None, prop: str, explicit_name: str) -> float:
    if not material:
        raise MaterialNotResolvedError(
            f"{func}: name a library material, or pass {explicit_name} explicitly"
        )
    block = lookup(material)
    if block is None:
        raise MaterialNotResolvedError(
            f"{func}: material {material!r} was not resolved against the material library "
            "for this call. Name a library material (library.list_materials), or pass "
            f"{explicit_name} explicitly."
        )
    figure = (block.get("properties") or {}).get(prop)
    if not isinstance(figure, dict) or figure.get("value") is None:
        raise MaterialNotResolvedError(
            f"{func}: the library entry {block.get('name', material)!r} has no {prop}. "
            f"Record it with its source (library.update_material), or pass {explicit_name} "
            "explicitly."
        )
    return float(figure["value"])


def material_source(material: str | None, prop: str) -> str | None:
    """Where the library's figure for ``prop`` came from, for a result to cite."""
    block = lookup(material)
    figure = ((block or {}).get("properties") or {}).get(prop)
    if isinstance(figure, dict):
        ref = figure.get("source_ref") or figure.get("source_kind")
        name = (block or {}).get("name", material)
        return f"library {name}: {ref}" if ref else f"library {name}"
    return None


def resolve_density_kg_m3(material: str, density_kg_m3: float | None = None) -> float:
    """Density (kg/m^3): explicit, else the library's, else refused."""
    if density_kg_m3 is not None:
        return density_kg_m3
    return _figure("resolve_density_kg_m3", material, "density_kg_m3", "density_kg_m3")


def resolve_elastic_properties(
    material: str | None = None,
    youngs_modulus_mpa: float | None = None,
    poissons_ratio: float | None = None,
) -> tuple[float, float]:
    """``(youngs_modulus_mpa, poissons_ratio)`` for a static FEA solve.

    An explicit pair always wins; otherwise both come from the library.
    MPa because the meshes are in mm (Length=mm, Force=N, Stress=MPa).
    """
    if youngs_modulus_mpa is not None and poissons_ratio is not None:
        return youngs_modulus_mpa, poissons_ratio
    explicit = "youngs_modulus_mpa and poissons_ratio"
    e = (
        youngs_modulus_mpa
        if youngs_modulus_mpa is not None
        else _figure("resolve_elastic_properties", material, "youngs_modulus_mpa", explicit)
    )
    nu = (
        poissons_ratio
        if poissons_ratio is not None
        else _figure("resolve_elastic_properties", material, "poisson_ratio", explicit)
    )
    return e, nu


def resolve_yield_mpa(material: str | None = None, yield_mpa: float | None = None) -> float:
    """Yield strength (MPa) for a safety-factor check: explicit, else the library's."""
    if yield_mpa is not None:
        return yield_mpa
    return _figure("resolve_yield_mpa", material, "yield_strength_mpa", "yield_mpa")


def resolve_thermal_conductivity_w_mk(
    material: str | None = None, thermal_conductivity_w_mk: float | None = None
) -> float:
    """Thermal conductivity in W/(m*K), SI.

    Callers building a mm-consistent deck multiply by 1e-3 at the call site.
    """
    if thermal_conductivity_w_mk is not None:
        return thermal_conductivity_w_mk
    return _figure(
        "resolve_thermal_conductivity_w_mk",
        material,
        "thermal_conductivity_w_mk",
        "thermal_conductivity_w_mk",
    )
