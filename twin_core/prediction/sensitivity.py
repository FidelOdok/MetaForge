"""One-at-a-time finite-difference sensitivity ranking (FORGE-317, target
lifecycle spec App. A "Solver" / step 15: sensitivity analysis on critical
parameters).

Scope cut, following the same discipline as every prior step of this epic:

- Ranks exactly the parameters the ticket's own acceptance criterion names
  for each metric -- ``wall_thickness_mm``/``length_mm`` for
  ``tip_deflection``, ``wall_thickness_mm``/material for ``mass`` -- NOT a
  general "sweep any Design IR parameter" engine. "Design IR" (
  ``twin_core/design_ir/``) turned out to be a CAD-*authoring* intermediate
  representation (a sequence of FreeCAD/CadQuery operations), not an
  engineering-analysis parameter model, and the real arm's own geometry
  was authored via a raw ``cadquery.execute_script`` call rather than
  through Design IR at all -- there is no live Design IR document for the
  actual yardstick part to sweep. A narrower, concrete first cut over the
  two functions ``twin_core/prediction/evaluator.py`` actually has (
  ``hollow_tube_tip_deflection_mm``/``hollow_tube_mass_kg``) makes the
  acceptance criterion literally true without inventing that larger
  system.
- The material axis is categorical, not continuous: "sensitivity" for it
  is the actual margin delta from swapping to each candidate material
  (still ranked the same way as the continuous axes -- by |delta margin|
  -- not a derivative, since "per unit of what" doesn't mean anything for
  a discrete choice).
- Tier 1 (torque) doesn't exist (FORGE-315's own finding) and isn't needed
  -- both named metrics here are cheap closed-form tier-0 calcs.

Pure math, no twin/MCP dependency -- mirrors ``twin_core/prediction/
evaluator.py``'s own split from its orchestration layer,
``api_gateway/twin/sensitivity.py``.
"""

from __future__ import annotations

from pydantic import BaseModel

from twin_core.prediction.evaluator import hollow_tube_mass_kg, hollow_tube_tip_deflection_mm


class ParameterSensitivity(BaseModel):
    """One parameter's effect on a metric's margin.

    ``sensitivity`` is ``delta_margin / delta_parameter`` for a continuous
    axis (units: margin-unit per parameter-unit, e.g. mm of margin per mm
    of wall thickness) or the raw ``delta_margin`` for a categorical axis
    (``is_categorical=True``, e.g. swapping material) -- these are NOT
    directly comparable magnitudes across axis types, only rank order
    within each ranking's own list matters.
    """

    parameter: str
    is_categorical: bool = False
    baseline_value: float | str
    perturbed_value: float | str
    baseline_margin: float
    perturbed_margin: float
    sensitivity: float


class SensitivityRanking(BaseModel):
    metric: str
    baseline_value: float
    limit: float
    baseline_margin: float
    rankings: list[ParameterSensitivity]


def rank_deflection_sensitivity(
    *,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    wall_thickness_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
    limit_mm: float,
    delta_fraction: float = 0.1,
) -> SensitivityRanking:
    """One-at-a-time finite differences of ``tip_deflection`` margin
    against ``wall_thickness_mm`` and ``length_mm`` (the two parameters
    the ticket's own acceptance criterion names), ranked by
    ``|delta_margin / delta_parameter|`` descending -- the parameter whose
    small change moves the margin most is listed first.
    """

    def margin(length: float, wall_thickness: float) -> float:
        value = hollow_tube_tip_deflection_mm(
            length_mm=length,
            width_mm=width_mm,
            height_mm=height_mm,
            wall_thickness_mm=wall_thickness,
            load_n=load_n,
            youngs_modulus_mpa=youngs_modulus_mpa,
        )
        return limit_mm - value

    baseline_value = hollow_tube_tip_deflection_mm(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        wall_thickness_mm=wall_thickness_mm,
        load_n=load_n,
        youngs_modulus_mpa=youngs_modulus_mpa,
    )
    baseline_margin = limit_mm - baseline_value

    entries: list[ParameterSensitivity] = []
    for name, base_val, perturb in (
        (
            "wall_thickness_mm",
            wall_thickness_mm,
            lambda d: margin(length_mm, wall_thickness_mm + d),
        ),
        ("length_mm", length_mm, lambda d: margin(length_mm + d, wall_thickness_mm)),
    ):
        delta = base_val * delta_fraction
        perturbed_margin = perturb(delta)
        entries.append(
            ParameterSensitivity(
                parameter=name,
                baseline_value=base_val,
                perturbed_value=base_val + delta,
                baseline_margin=baseline_margin,
                perturbed_margin=perturbed_margin,
                sensitivity=(perturbed_margin - baseline_margin) / delta,
            )
        )
    entries.sort(key=lambda e: abs(e.sensitivity), reverse=True)
    return SensitivityRanking(
        metric="tip_deflection",
        baseline_value=baseline_value,
        limit=limit_mm,
        baseline_margin=baseline_margin,
        rankings=entries,
    )


def rank_mass_sensitivity(
    *,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    wall_thickness_mm: float,
    density_kg_m3: float,
    limit_kg: float,
    material_name: str = "baseline",
    candidate_material_densities: dict[str, float] | None = None,
    delta_fraction: float = 0.1,
) -> SensitivityRanking:
    """One-at-a-time finite difference of ``mass`` margin against
    ``wall_thickness_mm`` (continuous), plus a ranked comparison against
    each candidate material's real density (categorical -- ranked by the
    resulting margin delta, not a derivative). ``candidate_material_densities``
    is caller-supplied (e.g.
    ``tool_registry.tools.cadquery.materials.MATERIAL_DENSITY_KG_M3``,
    FORGE-234's own table) so this module never guesses at material names.
    """

    def margin(wall_thickness: float, density: float) -> float:
        mass_kg = hollow_tube_mass_kg(
            length_mm=length_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            wall_thickness_mm=wall_thickness,
            density_kg_m3=density,
        )
        return limit_kg - mass_kg

    baseline_value = hollow_tube_mass_kg(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        wall_thickness_mm=wall_thickness_mm,
        density_kg_m3=density_kg_m3,
    )
    baseline_margin = limit_kg - baseline_value

    entries: list[ParameterSensitivity] = []
    delta = wall_thickness_mm * delta_fraction
    perturbed_margin = margin(wall_thickness_mm + delta, density_kg_m3)
    entries.append(
        ParameterSensitivity(
            parameter="wall_thickness_mm",
            baseline_value=wall_thickness_mm,
            perturbed_value=wall_thickness_mm + delta,
            baseline_margin=baseline_margin,
            perturbed_margin=perturbed_margin,
            sensitivity=(perturbed_margin - baseline_margin) / delta,
        )
    )

    for name, density in (candidate_material_densities or {}).items():
        if name == material_name:
            continue  # the baseline material itself -- zero delta by definition
        candidate_margin = margin(wall_thickness_mm, density)
        entries.append(
            ParameterSensitivity(
                parameter=f"material={name}",
                is_categorical=True,
                baseline_value=material_name,
                perturbed_value=name,
                baseline_margin=baseline_margin,
                perturbed_margin=candidate_margin,
                sensitivity=candidate_margin - baseline_margin,
            )
        )

    entries.sort(key=lambda e: abs(e.sensitivity), reverse=True)
    return SensitivityRanking(
        metric="mass",
        baseline_value=baseline_value,
        limit=limit_kg,
        baseline_margin=baseline_margin,
        rankings=entries,
    )
