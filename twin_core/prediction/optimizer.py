"""Single-parameter wall-thickness optimiser (FORGE-320, target lifecycle
spec App. A "Optimiser", step 10: minimise an objective subject to
constraints).

Scope cut, following the same discipline as every prior step of this epic:

- The ticket's own Jira scope says "over Design IR parameters", but
  FORGE-317's own scoping already found (and this ticket confirmed still
  true) that ``twin_core/design_ir/`` is a CAD-*authoring* op sequence, not
  an engineering-analysis parameter model, and the real arm's geometry was
  never authored through it at all -- there is no live Design IR document
  to sweep. This optimiser instead searches ``wall_thickness_mm``, the one
  parameter FORGE-317's own sensitivity ranking already found dominant for
  both deflection and mass margin, using the same hollow-rectangular-tube
  hand-calcs (``twin_core/prediction/evaluator.py``) rather than inventing
  a second geometry model.
- Mass is the ticket's own stated OBJECTIVE, not a third constraint
  alongside SF/deflection -- the project's separate ``moving_mass_budget``
  Constraint covers the WHOLE assembly, not this one part in isolation, and
  no per-part budget allocation exists to check a share of it against
  (``BudgetAllocation.owner``/``.discipline`` from FORGE-313 records who
  owns an interface quantity, not a numeric per-part mass share). Comparing
  the winning candidate's own mass against the whole-assembly budget is
  left to the caller/Evidence consumer, not baked into the search itself.
- A single continuous parameter with two monotonic constraints (thicker
  wall -> stiffer -> lower deflection; thicker wall -> lower bending stress
  -> higher safety factor) means the minimum-mass feasible point is exactly
  the smallest wall thickness where both constraints first hold --
  monotonic bisection is the right, honest search here, not a general
  nonlinear optimiser (scipy.optimize etc.) that this single-variable,
  single-direction problem doesn't need.
- The safety-factor check uses a plain cantilever max-bending-stress hand-
  calc (``sigma = M c / I``, at the fixed end) -- a real, new tier-0 model
  (no prior ticket computed stress at all, only deflection/mass), not a
  disguised FEA result. "Confirmed by FEA" (the ticket's acceptance
  wording) is achievable only when a real ``calculix.run_fea`` mesh/load
  case is supplied for tier-2 escalation, same caveat FORGE-315's own
  tier-0/tier-2 evaluator already carries -- this module does not attempt
  automatic FEA boundary-condition derivation for a novel part (a real,
  still-unsolved gap, FORGE-278/239/277).

Pure math, no twin/MCP dependency -- mirrors ``twin_core/prediction/
sensitivity.py``'s own split from its orchestration layer,
``api_gateway/twin/optimizer.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from twin_core.prediction.evaluator import (
    hollow_rect_moment_of_inertia_mm4,
    hollow_tube_mass_kg,
    hollow_tube_tip_deflection_mm,
)


class CandidateEvaluation(BaseModel):
    """One wall-thickness value's full tier-0 evaluation."""

    wall_thickness_mm: float
    mass_kg: float
    deflection_mm: float
    deflection_margin_mm: float  # limit - value; positive = within budget
    stress_mpa: float
    safety_factor: float
    sf_margin: float  # safety_factor - sf_limit; positive = satisfies
    feasible: bool


class OptimizationResult(BaseModel):
    status: str  # "optimal" | "infeasible" | "already_feasible_at_min"
    detail: str
    winner: CandidateEvaluation | None = None
    candidates: list[CandidateEvaluation] = Field(default_factory=list)


def cantilever_max_bending_stress_mpa(
    *, length_mm: float, load_n: float, height_mm: float, moment_of_inertia_mm4: float
) -> float:
    """Max bending stress at a cantilever's fixed end: ``sigma = M c / I``,
    ``M = load_n * length_mm`` (bending moment at the wall), ``c =
    height_mm / 2`` (distance from the neutral axis to the outer fibre).
    Same beam idealisation as :func:`~twin_core.prediction.evaluator.
    hollow_tube_tip_deflection_mm` -- a tier-0 hand-calc, not a real FEA
    stress field.
    """
    if length_mm <= 0 or height_mm <= 0:
        raise ValueError("cantilever_max_bending_stress_mpa: length_mm/height_mm must be positive")
    if load_n < 0:
        raise ValueError("cantilever_max_bending_stress_mpa: load_n must be non-negative")
    if moment_of_inertia_mm4 <= 0:
        raise ValueError(
            "cantilever_max_bending_stress_mpa: moment_of_inertia_mm4 must be positive"
        )
    moment_n_mm = load_n * length_mm
    c_mm = height_mm / 2.0
    return moment_n_mm * c_mm / moment_of_inertia_mm4


def _evaluate(
    wall_thickness_mm: float,
    *,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
    density_kg_m3: float,
    yield_mpa: float,
    deflection_limit_mm: float,
    sf_limit: float,
) -> CandidateEvaluation:
    deflection_mm = hollow_tube_tip_deflection_mm(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        wall_thickness_mm=wall_thickness_mm,
        load_n=load_n,
        youngs_modulus_mpa=youngs_modulus_mpa,
    )
    mass_kg = hollow_tube_mass_kg(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        wall_thickness_mm=wall_thickness_mm,
        density_kg_m3=density_kg_m3,
    )
    moment_of_inertia_mm4 = hollow_rect_moment_of_inertia_mm4(
        width_mm, height_mm, wall_thickness_mm
    )
    stress_mpa = cantilever_max_bending_stress_mpa(
        length_mm=length_mm,
        load_n=load_n,
        height_mm=height_mm,
        moment_of_inertia_mm4=moment_of_inertia_mm4,
    )
    safety_factor = yield_mpa / stress_mpa if stress_mpa > 0 else float("inf")
    deflection_margin_mm = deflection_limit_mm - deflection_mm
    sf_margin = safety_factor - sf_limit
    return CandidateEvaluation(
        wall_thickness_mm=wall_thickness_mm,
        mass_kg=mass_kg,
        deflection_mm=deflection_mm,
        deflection_margin_mm=deflection_margin_mm,
        stress_mpa=stress_mpa,
        safety_factor=safety_factor,
        sf_margin=sf_margin,
        feasible=deflection_margin_mm >= 0 and sf_margin >= 0,
    )


def optimize_wall_thickness(
    *,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
    density_kg_m3: float,
    yield_mpa: float,
    deflection_limit_mm: float,
    sf_limit: float = 2.0,
    wall_min_mm: float = 0.5,
    wall_max_mm: float | None = None,
    tolerance_mm: float = 0.01,
    max_iterations: int = 60,
) -> OptimizationResult:
    """Find the minimum ``wall_thickness_mm`` (=> minimum mass, since mass
    is monotonically increasing in wall thickness for fixed outer
    dimensions) that satisfies both ``deflection_mm <= deflection_limit_mm``
    and ``safety_factor >= sf_limit``.

    Both constraints are themselves monotonically non-decreasing in wall
    thickness (a thicker wall raises the section's moment of inertia,
    which strictly lowers both deflection and bending stress for a fixed
    outer envelope) -- so bisection on feasibility is exact, not a
    heuristic, for this specific single-parameter problem.
    """
    if wall_max_mm is None:
        # Leave a real cavity (thin-wall tube), not a degenerate near-solid
        # section -- half the smaller cross-section extent, minus a margin.
        wall_max_mm = min(width_mm, height_mm) / 2.0 - 0.5
    if wall_min_mm <= 0:
        raise ValueError("optimize_wall_thickness: wall_min_mm must be positive")
    if wall_max_mm <= wall_min_mm:
        raise ValueError(
            f"optimize_wall_thickness: wall_max_mm ({wall_max_mm:.4g}) must exceed "
            f"wall_min_mm ({wall_min_mm:.4g}) -- check width_mm/height_mm leave room for a cavity"
        )

    def eval_at(wall_thickness_mm: float) -> CandidateEvaluation:
        return _evaluate(
            wall_thickness_mm,
            length_mm=length_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            load_n=load_n,
            youngs_modulus_mpa=youngs_modulus_mpa,
            density_kg_m3=density_kg_m3,
            yield_mpa=yield_mpa,
            deflection_limit_mm=deflection_limit_mm,
            sf_limit=sf_limit,
        )

    lo_eval = eval_at(wall_min_mm)
    hi_eval = eval_at(wall_max_mm)
    candidates = [lo_eval, hi_eval]

    if lo_eval.feasible:
        return OptimizationResult(
            status="already_feasible_at_min",
            detail=(
                f"the minimum allowed wall thickness ({wall_min_mm:.4g}mm) already satisfies "
                f"both constraints (deflection_margin={lo_eval.deflection_margin_mm:.4g}mm, "
                f"sf_margin={lo_eval.sf_margin:.4g}) -- no thinner, lighter option was searched"
            ),
            winner=lo_eval,
            candidates=candidates,
        )
    if not hi_eval.feasible:
        return OptimizationResult(
            status="infeasible",
            detail=(
                f"even the maximum allowed wall thickness ({wall_max_mm:.4g}mm) fails: "
                f"deflection_margin={hi_eval.deflection_margin_mm:.4g}mm, "
                f"sf_margin={hi_eval.sf_margin:.4g} -- no wall thickness in "
                f"[{wall_min_mm:.4g}, {wall_max_mm:.4g}]mm satisfies both constraints "
                "with this material and load"
            ),
            winner=None,
            candidates=candidates,
        )

    lo, hi = wall_min_mm, wall_max_mm
    for _ in range(max_iterations):
        if hi - lo <= tolerance_mm:
            break
        mid = (lo + hi) / 2.0
        mid_eval = eval_at(mid)
        candidates.append(mid_eval)
        if mid_eval.feasible:
            hi = mid
        else:
            lo = mid

    winner = eval_at(hi)
    candidates.append(winner)
    return OptimizationResult(
        status="optimal",
        detail=(
            "minimum feasible wall thickness found via bisection: "
            f"{winner.wall_thickness_mm:.4g}mm (mass {winner.mass_kg:.4g}kg, "
            f"deflection_margin={winner.deflection_margin_mm:.4g}mm, "
            f"sf_margin={winner.sf_margin:.4g})"
        ),
        winner=winner,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Second parameter (FORGE-288, gap G-G2): height_mm, wall_thickness_mm fixed.
# ---------------------------------------------------------------------------
#
# Genuinely generalizes twin.start_design_loop (FORGE-287) beyond the single
# wall_thickness_mm parameter it shipped with -- proof that the design
# loop's own candidate-ingestion logic is now parameter-agnostic, not a
# renamed copy of the same search. Same real physics
# (twin_core/prediction/evaluator.py's hollow-tube hand-calcs), same
# monotonic-bisection soundness: for a FIXED wall_thickness_mm, increasing
# height_mm strictly increases the section's moment of inertia (cubic in
# height) -- strictly lowering deflection and bending stress -- while ALSO
# strictly increasing cross-sectional area (outer width*height grows faster
# than the inner cavity's width*height, since the outer width exceeds the
# inner one for any wall_thickness_mm > 0). So, exactly as with
# wall_thickness_mm, the minimum-mass feasible point is the smallest height
# where both constraints first hold.
#
# A parallel CandidateEvaluation/OptimizationResult pair, not a reused one:
# CandidateEvaluation's own ``wall_thickness_mm`` field would be dishonestly
# repurposed to mean "height" if shared -- a field name should name what it
# holds.


class TubeHeightCandidateEvaluation(BaseModel):
    """One height_mm value's full tier-0 evaluation, wall_thickness_mm held
    fixed."""

    height_mm: float
    mass_kg: float
    deflection_mm: float
    deflection_margin_mm: float
    stress_mpa: float
    safety_factor: float
    sf_margin: float
    feasible: bool


class TubeHeightOptimizationResult(BaseModel):
    status: str  # "optimal" | "infeasible" | "already_feasible_at_min"
    detail: str
    winner: TubeHeightCandidateEvaluation | None = None
    candidates: list[TubeHeightCandidateEvaluation] = Field(default_factory=list)


def _evaluate_height(
    height_mm: float,
    *,
    length_mm: float,
    width_mm: float,
    wall_thickness_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
    density_kg_m3: float,
    yield_mpa: float,
    deflection_limit_mm: float,
    sf_limit: float,
) -> TubeHeightCandidateEvaluation:
    deflection_mm = hollow_tube_tip_deflection_mm(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        wall_thickness_mm=wall_thickness_mm,
        load_n=load_n,
        youngs_modulus_mpa=youngs_modulus_mpa,
    )
    mass_kg = hollow_tube_mass_kg(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        wall_thickness_mm=wall_thickness_mm,
        density_kg_m3=density_kg_m3,
    )
    moment_of_inertia_mm4 = hollow_rect_moment_of_inertia_mm4(
        width_mm, height_mm, wall_thickness_mm
    )
    stress_mpa = cantilever_max_bending_stress_mpa(
        length_mm=length_mm,
        load_n=load_n,
        height_mm=height_mm,
        moment_of_inertia_mm4=moment_of_inertia_mm4,
    )
    safety_factor = yield_mpa / stress_mpa if stress_mpa > 0 else float("inf")
    deflection_margin_mm = deflection_limit_mm - deflection_mm
    sf_margin = safety_factor - sf_limit
    return TubeHeightCandidateEvaluation(
        height_mm=height_mm,
        mass_kg=mass_kg,
        deflection_mm=deflection_mm,
        deflection_margin_mm=deflection_margin_mm,
        stress_mpa=stress_mpa,
        safety_factor=safety_factor,
        sf_margin=sf_margin,
        feasible=deflection_margin_mm >= 0 and sf_margin >= 0,
    )


def optimize_tube_height(
    *,
    length_mm: float,
    width_mm: float,
    wall_thickness_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
    density_kg_m3: float,
    yield_mpa: float,
    deflection_limit_mm: float,
    sf_limit: float = 2.0,
    height_min_mm: float = 1.0,
    height_max_mm: float | None = None,
    tolerance_mm: float = 0.01,
    max_iterations: int = 60,
) -> TubeHeightOptimizationResult:
    """Find the minimum ``height_mm`` (=> minimum mass, for fixed
    ``wall_thickness_mm``) that satisfies both ``deflection_mm <=
    deflection_limit_mm`` and ``safety_factor >= sf_limit``. Mirrors
    :func:`optimize_wall_thickness`'s own bisection structure exactly --
    see this module's docstring for why bisection is exact here too."""
    if height_max_mm is None:
        height_max_mm = width_mm * 4.0
    if height_min_mm <= 2 * wall_thickness_mm:
        raise ValueError(
            "optimize_tube_height: height_min_mm must leave a real cavity above "
            f"2*wall_thickness_mm ({2 * wall_thickness_mm:.4g}mm)"
        )
    if height_max_mm <= height_min_mm:
        raise ValueError(
            f"optimize_tube_height: height_max_mm ({height_max_mm:.4g}mm) must exceed "
            f"height_min_mm ({height_min_mm:.4g}mm)"
        )

    def eval_at(height_mm: float) -> TubeHeightCandidateEvaluation:
        return _evaluate_height(
            height_mm,
            length_mm=length_mm,
            width_mm=width_mm,
            wall_thickness_mm=wall_thickness_mm,
            load_n=load_n,
            youngs_modulus_mpa=youngs_modulus_mpa,
            density_kg_m3=density_kg_m3,
            yield_mpa=yield_mpa,
            deflection_limit_mm=deflection_limit_mm,
            sf_limit=sf_limit,
        )

    lo_eval = eval_at(height_min_mm)
    hi_eval = eval_at(height_max_mm)
    candidates = [lo_eval, hi_eval]

    if lo_eval.feasible:
        return TubeHeightOptimizationResult(
            status="already_feasible_at_min",
            detail=(
                f"the minimum allowed height ({height_min_mm:.4g}mm) already satisfies "
                f"both constraints (deflection_margin={lo_eval.deflection_margin_mm:.4g}mm, "
                f"sf_margin={lo_eval.sf_margin:.4g}) -- no shorter, lighter option was searched"
            ),
            winner=lo_eval,
            candidates=candidates,
        )
    if not hi_eval.feasible:
        return TubeHeightOptimizationResult(
            status="infeasible",
            detail=(
                f"even the maximum allowed height ({height_max_mm:.4g}mm) fails: "
                f"deflection_margin={hi_eval.deflection_margin_mm:.4g}mm, "
                f"sf_margin={hi_eval.sf_margin:.4g} -- no height in "
                f"[{height_min_mm:.4g}, {height_max_mm:.4g}]mm satisfies both constraints "
                "with this material and load"
            ),
            winner=None,
            candidates=candidates,
        )

    lo, hi = height_min_mm, height_max_mm
    for _ in range(max_iterations):
        if hi - lo <= tolerance_mm:
            break
        mid = (lo + hi) / 2.0
        mid_eval = eval_at(mid)
        candidates.append(mid_eval)
        if mid_eval.feasible:
            hi = mid
        else:
            lo = mid

    winner = eval_at(hi)
    candidates.append(winner)
    return TubeHeightOptimizationResult(
        status="optimal",
        detail=(
            "minimum feasible height found via bisection: "
            f"{winner.height_mm:.4g}mm (mass {winner.mass_kg:.4g}kg, "
            f"deflection_margin={winner.deflection_margin_mm:.4g}mm, "
            f"sf_margin={winner.sf_margin:.4g})"
        ),
        winner=winner,
        candidates=candidates,
    )
