#!/usr/bin/env python3
"""Design-loop outcome rubric (FORGE-292, gap G-G6).

Every existing rubric in this directory (``mechanical_rubric.py`` etc.)
grades free-text decision content with substring/regex matches --
``"pass" in text``, ``re.search(r"safety[ -]?factor...", text)``. That is a
real, accurate description of "graders keyword-based" (this ticket's own
"Current state" wording): it can be fooled by any text that merely mentions
the right words, and it cannot check a NUMBER against what the right answer
actually is.

The closed design loop (FORGE-287/288/289/291) has something those other
domains don't: a fully structured, deterministic result --
``status``/``winner.<parameter>_mm``/``winner.mass_kg`` -- with no free text
to grep. So this rubric grades the OUTCOME directly: run the real
orchestration (``api_gateway.twin.optimizer``, the same code
``twin.start_design_loop`` calls), then independently recompute the
expected winner via a direct call to the pure bisection math
(``twin_core.prediction.optimizer.optimize_wall_thickness``) using the same
real geometry/material inputs, and compare the two numerically within a
tolerance. A parameter dropped or mis-threaded anywhere in the
orchestration layer (the exact failure class a keyword grep can never
catch) shows up as a real mismatch here.

``evaluate_design_loop`` is pure and unit-testable with synthetic numbers;
``score_design_loop_run`` calls the real twin + optimizer. Deliberately NOT
stdlib-only, unlike this directory's HTTP-based scenario runner
(``run_scenarios.py``): grading the design loop's own deterministic math
by driving an LLM through the full agentic ``POST /v1/runs`` harness would
add cost and flakiness without adding signal, so this calls the twin API
directly in-process (``twin_core.api.InMemoryTwinAPI``), the same pattern
this session's own live-validation scripts already use against fidel-dev.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


def evaluate_design_loop(
    *,
    status: str,
    winner_parameter_value: float | None,
    expected_parameter_value: float | None,
    tolerance: float = 0.01,
) -> dict[str, bool]:
    """Score a design loop run from real, structured facts (pure). No text
    parsing anywhere -- ``expected_parameter_value`` is the independently
    computed ground truth, not something scraped from a report string."""
    converged = status in ("optimal", "already_feasible_at_min")
    matches_expected = False
    if winner_parameter_value is not None and expected_parameter_value is not None:
        denom = abs(expected_parameter_value) or 1.0
        matches_expected = (
            abs(winner_parameter_value - expected_parameter_value) / denom <= tolerance
        )
    return {
        "converged": converged,
        "winner_present": winner_parameter_value is not None,
        "winner_matches_independent_calc": matches_expected,
    }


def design_loop_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


async def score_design_loop_run(
    *,
    twin: Any,
    work_product_id: str,
    load_n: float,
    deflection_limit_mm: float,
    sf_limit: float = 2.0,
    material: str = "aluminum_6061",
) -> dict[str, Any]:
    """Run the real orchestration and grade it against an independent
    direct call to the pure bisection math -- an outcome check, not a
    presence/keyword one."""
    from api_gateway.twin.metric_evaluator import bounding_box_extents_mm
    from api_gateway.twin.optimizer import make_wall_thickness_optimizer
    from tool_registry.tools.cadquery.materials import (
        resolve_density_kg_m3,
        resolve_elastic_properties,
        resolve_yield_mpa,
    )
    from twin_core.prediction.optimizer import optimize_wall_thickness

    optimize = make_wall_thickness_optimizer(twin)
    # Raises ValueError("no work_product ...") for an unknown id -- same
    # precedent every other optimizer/design-loop caller in this codebase
    # already relies on, so no redundant existence check here.
    actual = await optimize(
        work_product_id=work_product_id,
        load_n=load_n,
        deflection_limit_mm=deflection_limit_mm,
        sf_limit=sf_limit,
        material=material,
    )

    wp = await twin.get_work_product(UUID(work_product_id))
    assert wp is not None  # optimize() above already proved this work product exists
    length_mm, width_mm, height_mm = bounding_box_extents_mm(wp.metadata)
    youngs_modulus_mpa, _poissons_ratio = resolve_elastic_properties(material)
    density_kg_m3 = resolve_density_kg_m3(material)
    yield_mpa = resolve_yield_mpa(material)
    expected = optimize_wall_thickness(
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

    actual_winner = actual.get("winner")
    checks = evaluate_design_loop(
        status=actual["status"],
        winner_parameter_value=(actual_winner["wall_thickness_mm"] if actual_winner else None),
        expected_parameter_value=(expected.winner.wall_thickness_mm if expected.winner else None),
    )
    return {
        "score": design_loop_score(checks),
        "checks": checks,
        "actual_status": actual["status"],
        "actual_winner": actual_winner,
        "expected_status": expected.status,
        "expected_wall_thickness_mm": (
            expected.winner.wall_thickness_mm if expected.winner else None
        ),
    }
