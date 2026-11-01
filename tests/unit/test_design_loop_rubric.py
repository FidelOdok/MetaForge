"""Design-loop outcome rubric (FORGE-292, gap G-G6) — the pure evaluator.

Verifies the rubric grades a real, structured outcome (status + a numeric
value checked against an independently computed ground truth) rather than
grepping free text for keywords, the "graders keyword-based" gap the other
rubrics in this directory (mechanical_rubric.py etc.) still have.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from design_loop_rubric import (  # noqa: E402
    design_loop_score,
    evaluate_design_loop,
    score_design_loop_run,
)


def test_converged_winner_matching_expected_scores_full() -> None:
    checks = evaluate_design_loop(
        status="optimal",
        winner_parameter_value=10.927734375,
        expected_parameter_value=10.927734375,
    )
    assert all(checks.values()), checks
    assert design_loop_score(checks) == 1.0


def test_winner_diverging_from_independent_calc_is_dinged() -> None:
    """The exact failure class a keyword grep can never catch: the loop
    converged and produced SOME winner, but it's the wrong number -- e.g. a
    dropped sf_limit or a mis-threaded material argument somewhere in the
    orchestration layer."""
    checks = evaluate_design_loop(
        status="optimal",
        winner_parameter_value=5.0,
        expected_parameter_value=10.927734375,
    )
    assert checks["converged"] is True
    assert checks["winner_present"] is True
    assert checks["winner_matches_independent_calc"] is False
    assert design_loop_score(checks) < 1.0


def test_within_tolerance_still_passes() -> None:
    checks = evaluate_design_loop(
        status="optimal",
        winner_parameter_value=10.93,
        expected_parameter_value=10.927734375,
        tolerance=0.01,
    )
    assert checks["winner_matches_independent_calc"] is True


def test_infeasible_run_has_no_winner_and_does_not_converge() -> None:
    checks = evaluate_design_loop(
        status="infeasible",
        winner_parameter_value=None,
        expected_parameter_value=None,
    )
    assert checks["converged"] is False
    assert checks["winner_present"] is False
    assert design_loop_score(checks) == 0.0


def test_already_feasible_at_min_counts_as_converged() -> None:
    checks = evaluate_design_loop(
        status="already_feasible_at_min",
        winner_parameter_value=0.5,
        expected_parameter_value=0.5,
    )
    assert checks["converged"] is True


def test_score_of_empty_checks_is_zero() -> None:
    assert design_loop_score({}) == 0.0


class TestScoreDesignLoopRun:
    """The impure half -- runs the real orchestration against a real work
    product and grades it against an independent direct calculation."""

    async def test_grades_a_real_run_against_real_geometry(self) -> None:
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        twin = InMemoryTwinAPI.create()
        wp = await twin.create_work_product(
            WorkProduct(
                name="upper_arm",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
                metadata={
                    "geometry_features": {
                        "properties": {
                            "bounding_box": {
                                "min_x": -180,
                                "max_x": 180,
                                "min_y": -20,
                                "max_y": 20,
                                "min_z": -30,
                                "max_z": 30,
                            }
                        }
                    }
                },
            )
        )
        result = await score_design_loop_run(
            twin=twin,
            work_product_id=str(wp.id),
            load_n=100.0,
            deflection_limit_mm=0.5,
        )
        assert result["score"] == 1.0
        assert result["checks"]["winner_matches_independent_calc"] is True
        assert result["actual_status"] == result["expected_status"] == "optimal"

    async def test_unknown_work_product_reports_an_error_not_a_crash(self) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        with pytest.raises(ValueError, match="no work_product"):
            await score_design_loop_run(
                twin=twin,
                work_product_id="11111111-1111-1111-1111-111111111111",
                load_n=20.0,
                deflection_limit_mm=0.5,
            )
