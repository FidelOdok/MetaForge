"""V&V correctness rubric — catches the false-pass verdict (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from vv_rubric import evaluate_vv, vv_score  # noqa: E402

_GOAL = "an I2C IMU breakout board"

# The dangerous real case: claims compliance while admitting the checks failed.
_FALSE_PASS = (
    "Validation of Design Against Requirements. The simulations demonstrated compliance "
    "with the established engineering requirements such as stress limits, thermal "
    "performance, and electrical circuit integrity. However, the attempt to run the "
    "mechanical simulations (FEA and thermal) and electrical validations (ERC and DRC) "
    "encountered failures."
)
# An earned pass: real numbers, traces to a requirement, no failure-to-run.
_EARNED = (
    "V&V verdict PASS. CalculiX FEA gives sigma_max = 92 MPa vs the 276 MPa yield "
    "requirement, safety factor 3.0 (target >= 2). ERC: no violations."
)


def test_false_pass_is_caught() -> None:
    checks = evaluate_vv(decision_text=_FALSE_PASS, artifact_types=set(), goal=_GOAL)
    assert checks["verdict_recorded"]  # it does claim compliance
    assert checks["check_type_named"]  # FEA/ERC/DRC named
    # but the verdict is not earned: no artifact, no numbers, and it is a FALSE pass
    assert not checks["real_check_artifact"]
    assert not checks["quantified_result"]
    assert not checks["not_false_pass"], "compliance + 'encountered failures' must flag false pass"
    assert vv_score(checks) <= round(2 / 6, 3)


def test_earned_pass_with_artifact_scores_high() -> None:
    checks = evaluate_vv(
        decision_text=_EARNED,
        artifact_types={"simulation_result"},
        goal=_GOAL,
    )
    assert all(checks.values()), checks
    assert vv_score(checks) == 1.0


def test_honest_fail_is_not_a_false_pass() -> None:
    text = "V&V verdict FAIL: bending stress 310 MPa exceeds the 276 MPa yield limit."
    checks = evaluate_vv(decision_text=text, artifact_types=set(), goal=_GOAL)
    assert checks["not_false_pass"]  # an honest fail is not a false pass
    assert checks["verdict_recorded"] and checks["quantified_result"]
