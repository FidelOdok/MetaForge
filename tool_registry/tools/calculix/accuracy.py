"""FEA accuracy controls (FORGE-280).

Independent checks, each usable on its own:

- ``assess_stress_accuracy``: flags a likely stress-concentration/BC
  artifact from a single ``extract_results`` call's own nodal stress
  distribution -- no second run needed.
- ``cross_check_cantilever_bending``: an Euler-Bernoulli hand calc for the
  textbook case (a beam check FORGE-239 caught manually, see that ticket's
  own note on this gap) -- compares an FEA max stress against the
  analytical answer within a tolerance.
- ``cross_check_cantilever_frequency`` (FORGE-281): the modal sibling of
  the above, a closed-form first-bending-mode natural frequency.
- ``cross_check_thermal_steady_state`` (FORGE-282): a 1D steady-state
  conduction hand calc (the thermal sibling of the beam check), for the
  same fixed-temperature-sink model ``build_thermal_deck`` solves.
- ``check_mesh_convergence``: given results from the same analysis run at
  two or more element sizes (the caller runs ``calculix.run_fea`` itself at
  each size -- this does not orchestrate that sweep), reports whether
  refining the mesh further would still change the answer meaningfully.
"""

from __future__ import annotations

import math
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# FORGE-281: the first root of a fixed-free (cantilever) beam's transcendental
# frequency equation cos(bL)cosh(bL) = -1, squared -- the standard constant in
# f1 = (beta1*L)^2 / (2*pi*L^2) * sqrt(EI/(rho*A)) for a uniform cantilever's
# first bending mode. A textbook constant (e.g. Blevins, "Formulas for
# Natural Frequency and Mode Shape"), not derived here.
_CANTILEVER_FIRST_MODE_BETA_L_SQUARED = 3.5160

# Empirically, a real converged stress field varies smoothly; a max more
# than this many times the median nodal value is characteristic of a
# textbook stress singularity (a point load/support applied to a single
# node/edge, which is mathematically unbounded as the mesh refines) rather
# than a real, mesh-independent peak.
_SUSPICIOUS_MAX_TO_MEDIAN_RATIO = 5.0


def assess_stress_accuracy(stress_data: dict[str, Any]) -> dict[str, Any]:
    """Flag a stress result whose max is disproportionate to its own field.

    Args:
        stress_data: the ``stress`` dict ``parse_frd_file``/``extract_results``
            produce -- must have a populated ``nodes`` mapping (node_id ->
            von Mises stress) to say anything; called before
            ``include_node_data=False`` strips it.

    Returns:
        ``{suspicious, reason, max_to_median_ratio}`` -- ``reason`` is
        ``None`` when not suspicious, otherwise a human-readable
        explanation naming the ratio and what to do about it.
    """
    nodes: dict[int, float] = stress_data.get("nodes", {})
    if not nodes:
        return {"suspicious": False, "reason": None, "max_to_median_ratio": None}

    values = sorted(nodes.values())
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    max_val = values[-1]

    if median <= 0:
        # A field that's all zero/negative (shouldn't happen for a von
        # Mises magnitude, but this is defensive) has no meaningful ratio.
        return {"suspicious": False, "reason": None, "max_to_median_ratio": None}

    ratio = max_val / median
    suspicious = ratio > _SUSPICIOUS_MAX_TO_MEDIAN_RATIO
    reason = (
        f"Max stress ({max_val:.1f}) is {ratio:.1f}x the median nodal stress "
        f"({median:.1f}) -- this pattern usually means a stress concentration "
        "at a point load or single-node boundary condition, not a converged "
        "physical peak. Cross-check with a finer mesh (calculix.check_mesh_"
        "convergence) or a hand calc before trusting this number for a "
        "safety-factor decision."
        if suspicious
        else None
    )
    return {
        "suspicious": suspicious,
        "reason": reason,
        "max_to_median_ratio": round(ratio, 2),
    }


def cross_check_cantilever_bending(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    force_n: float,
    fea_max_stress_mpa: float,
    tolerance_pct: float = 20.0,
) -> dict[str, Any]:
    """Euler-Bernoulli hand calc for a rectangular cantilever, tip-loaded.

    sigma = M * c / I, with M = F * L (fixed-free cantilever, tip point
    load), c = height / 2 (rectangular section, stress at the outer fiber),
    I = width * height**3 / 12 (rectangular second moment of area). All
    dimensions in mm and force in N give stress in N/mm^2 = MPa directly, no
    unit conversion needed.

    This is deliberately narrow -- one well-understood textbook case, not a
    general beam-theory solver. It exists because this is exactly the check
    a human did manually to catch FORGE-239's bad fixed_node_set (a beam
    problem where the FEA number was ~10x too stiff); automating that one
    check is worth more than a general solver nobody's case matches exactly.
    """
    if length_mm <= 0 or width_mm <= 0 or height_mm <= 0:
        raise ValueError("length_mm, width_mm, and height_mm must all be positive")
    if tolerance_pct <= 0:
        raise ValueError("tolerance_pct must be positive")

    moment_n_mm = force_n * length_mm
    c_mm = height_mm / 2
    i_mm4 = width_mm * height_mm**3 / 12
    hand_calc_stress_mpa = moment_n_mm * c_mm / i_mm4

    if hand_calc_stress_mpa == 0:
        percent_difference = 0.0 if fea_max_stress_mpa == 0 else float("inf")
    else:
        percent_difference = (
            abs(fea_max_stress_mpa - hand_calc_stress_mpa) / abs(hand_calc_stress_mpa) * 100
        )
    within_tolerance = percent_difference <= tolerance_pct

    logger.info(
        "cantilever_bending_cross_check",
        hand_calc_stress_mpa=round(hand_calc_stress_mpa, 2),
        fea_max_stress_mpa=fea_max_stress_mpa,
        percent_difference=round(percent_difference, 1),
        within_tolerance=within_tolerance,
    )

    return {
        "hand_calc_stress_mpa": round(hand_calc_stress_mpa, 2),
        "fea_max_stress_mpa": fea_max_stress_mpa,
        "percent_difference": round(percent_difference, 1),
        "tolerance_pct": tolerance_pct,
        "within_tolerance": within_tolerance,
    }


def cross_check_cantilever_frequency(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    density_kg_m3: float,
    youngs_modulus_mpa: float,
    fea_first_mode_hz: float,
    tolerance_pct: float = 20.0,
) -> dict[str, Any]:
    """Closed-form first-bending-mode natural frequency for a uniform
    rectangular cantilever, tip-free (FORGE-281) -- the modal sibling of
    :func:`cross_check_cantilever_bending`, same one-well-understood-
    textbook-case discipline and the same fixed-free/rectangular-section
    scope restriction (not a general beam-vibration solver).

    f1 = (beta1*L)^2 / (2*pi*L^2) * sqrt(E*I / (rho*A)), with I = width *
    height^3 / 12 (bending about the width-height section, height in the
    bending direction -- same convention as ``cross_check_cantilever_
    bending``) and A = width * height. ``density_kg_m3`` is converted
    internally to the tonne/mm^3 consistent with ``youngs_modulus_mpa``'s
    own mm+N+MPa system (see ``deck_builder.build_modal_deck``'s docstring
    for why kg/m^3 can't be used directly here).
    """
    if length_mm <= 0 or width_mm <= 0 or height_mm <= 0:
        raise ValueError("length_mm, width_mm, and height_mm must all be positive")
    if density_kg_m3 <= 0:
        raise ValueError("density_kg_m3 must be positive")
    if youngs_modulus_mpa <= 0:
        raise ValueError("youngs_modulus_mpa must be positive")
    if tolerance_pct <= 0:
        raise ValueError("tolerance_pct must be positive")

    density_tonne_mm3 = density_kg_m3 * 1e-12
    area_mm2 = width_mm * height_mm
    i_mm4 = width_mm * height_mm**3 / 12
    hand_calc_hz = (
        _CANTILEVER_FIRST_MODE_BETA_L_SQUARED
        / (2 * math.pi * length_mm**2)
        * math.sqrt(youngs_modulus_mpa * i_mm4 / (density_tonne_mm3 * area_mm2))
    )

    percent_difference = (
        abs(fea_first_mode_hz - hand_calc_hz) / hand_calc_hz * 100 if hand_calc_hz else float("inf")
    )
    within_tolerance = percent_difference <= tolerance_pct

    logger.info(
        "cantilever_frequency_cross_check",
        hand_calc_hz=round(hand_calc_hz, 3),
        fea_first_mode_hz=fea_first_mode_hz,
        percent_difference=round(percent_difference, 1),
        within_tolerance=within_tolerance,
    )

    return {
        "hand_calc_first_mode_hz": round(hand_calc_hz, 3),
        "fea_first_mode_hz": fea_first_mode_hz,
        "percent_difference": round(percent_difference, 1),
        "tolerance_pct": tolerance_pct,
        "within_tolerance": within_tolerance,
    }


def cross_check_thermal_steady_state(
    conduction_length_mm: float,
    cross_section_area_mm2: float,
    thermal_conductivity_w_mk: float,
    power_dissipation_w: float,
    sink_temp_c: float,
    fea_peak_temp_c: float,
    tolerance_pct: float = 20.0,
) -> dict[str, Any]:
    """1D steady-state conduction hand calc -- the thermal sibling of
    :func:`cross_check_cantilever_bending` (FORGE-282).

    T_peak = T_sink + Q * L / (k * A), the standard thermal-resistance
    formula (R = L / (k*A), the same conduction-resistance analogy an
    electrical-resistance calc uses) for heat Q flowing a straight-line
    path of length L and cross-sectional area A through a material of
    conductivity k -- the exact model :func:`~tool_registry.tools.calculix.
    deck_builder.build_thermal_deck` solves (a fixed-temperature sink, no
    convection). ``thermal_conductivity_w_mk`` is the ordinary SI W/(m*K)
    value (same convert-once-at-the-call-site convention as
    ``resolve_thermal_conductivity_w_mk`` -- converted internally here to
    W/(mm*K)).

    Deliberately narrow -- one well-understood textbook case (uniform
    straight conduction path, no convection), not a general thermal
    solver, matching the same one-case discipline as
    :func:`cross_check_cantilever_bending`/:func:`cross_check_cantilever_
    frequency`. It assumes the part's own conduction resistance dominates
    (i.e. this is the SAME idealization ``build_thermal_deck`` itself
    makes -- a fixed-temperature sink, not a convective one), so this
    check validates the FEA result against the deck's own model, not
    against a real open-air convective boundary.
    """
    if conduction_length_mm <= 0:
        raise ValueError("conduction_length_mm must be positive")
    if cross_section_area_mm2 <= 0:
        raise ValueError("cross_section_area_mm2 must be positive")
    if thermal_conductivity_w_mk <= 0:
        raise ValueError("thermal_conductivity_w_mk must be positive")
    if tolerance_pct <= 0:
        raise ValueError("tolerance_pct must be positive")

    conductivity_w_mm_k = thermal_conductivity_w_mk * 1e-3
    thermal_resistance_k_per_w = conduction_length_mm / (
        conductivity_w_mm_k * cross_section_area_mm2
    )
    temp_rise_c = power_dissipation_w * thermal_resistance_k_per_w
    hand_calc_peak_temp_c = sink_temp_c + temp_rise_c

    temp_rise_from_sink = fea_peak_temp_c - sink_temp_c
    if temp_rise_c == 0:
        percent_difference = 0.0 if temp_rise_from_sink == 0 else float("inf")
    else:
        percent_difference = abs(temp_rise_from_sink - temp_rise_c) / abs(temp_rise_c) * 100
    within_tolerance = percent_difference <= tolerance_pct

    logger.info(
        "thermal_steady_state_cross_check",
        hand_calc_peak_temp_c=round(hand_calc_peak_temp_c, 2),
        fea_peak_temp_c=fea_peak_temp_c,
        percent_difference=round(percent_difference, 1),
        within_tolerance=within_tolerance,
    )

    return {
        "hand_calc_peak_temp_c": round(hand_calc_peak_temp_c, 2),
        "fea_peak_temp_c": fea_peak_temp_c,
        "percent_difference": round(percent_difference, 1),
        "tolerance_pct": tolerance_pct,
        "within_tolerance": within_tolerance,
    }


def check_mesh_convergence(
    points: list[dict[str, float]],
    tolerance_pct: float = 5.0,
) -> dict[str, Any]:
    """Whether max stress has stopped changing meaningfully as the mesh refines.

    Args:
        points: one entry per element size already run, each
            ``{"element_size_mm": ..., "max_von_mises_mpa": ...}`` -- the
            caller runs ``calculix.run_fea``/``extract_results`` at each
            size itself; this only compares the results. At least 2 points.
        tolerance_pct: the max-stress change between the two finest sizes
            must be at or under this to call it converged.

    Returns:
        ``points`` sorted coarsest-to-finest, the ``changes`` between each
        consecutive pair, ``converged`` (bool, based on the last/finest
        pair only), and a human-readable ``recommendation``.
    """
    if len(points) < 2:
        raise ValueError("need at least 2 points (element sizes) to assess convergence")
    for p in points:
        if p["element_size_mm"] <= 0:
            raise ValueError("element_size_mm must be positive")

    sorted_points = sorted(points, key=lambda p: p["element_size_mm"], reverse=True)
    changes: list[dict[str, float]] = []
    for prev, curr in zip(sorted_points, sorted_points[1:]):
        prev_stress = prev["max_von_mises_mpa"]
        curr_stress = curr["max_von_mises_mpa"]
        pct = abs(curr_stress - prev_stress) / abs(prev_stress) * 100 if prev_stress else 0.0
        changes.append(
            {
                "from_element_size_mm": prev["element_size_mm"],
                "to_element_size_mm": curr["element_size_mm"],
                "percent_change": round(pct, 2),
            }
        )

    last_change = changes[-1]["percent_change"]
    converged = last_change <= tolerance_pct
    finest = sorted_points[-1]["element_size_mm"]
    recommendation = (
        f"Converged -- refining to {finest}mm elements changed max stress by "
        f"only {last_change:.1f}%, within the {tolerance_pct:.0f}% tolerance."
        if converged
        else (
            f"Not converged -- max stress is still changing {last_change:.1f}% "
            f"between the two finest element sizes tried ({tolerance_pct:.0f}% "
            f"tolerance). Run again at an element size finer than {finest}mm."
        )
    )

    return {
        "points": sorted_points,
        "changes": changes,
        "converged": converged,
        "recommendation": recommendation,
    }
