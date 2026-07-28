"""Requirements correctness rubric — verifiability gap (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from requirements_rubric import evaluate_requirements, requirements_score  # noqa: E402

_GOAL = "an I2C IMU breakout board"

# What the native phase records: quantified constraints, but no verification.
_QUANTIFIED_NO_VERIFY = (
    "Requirements for the I2C IMU breakout board: expose an MPU-6050 on a 0.1-inch header, "
    "provide 3.3 V regulation from 5 V USB, include decoupling. Constraints: mass limit 10 g, "
    "board 25 x 20 mm, power budget 0.1 W. Operating range -40 to 85 °C."
)
_WITH_VERIFY = _QUANTIFIED_NO_VERIFY + (
    " Verification: mass measured on a scale (acceptance <= 10 g); dimensions inspected against "
    "the 25 x 20 mm envelope; power measured at 3.3 V (pass/fail <= 0.1 W)."
)


def test_quantified_but_unverifiable_requirements_miss_one_check() -> None:
    checks = evaluate_requirements(
        decision_text=_QUANTIFIED_NO_VERIFY, artifact_types=set(), goal=_GOAL
    )
    assert checks["quantified_constraints"]
    assert checks["functional_reqs_stated"]
    assert checks["interfaces_specified"]
    assert checks["operating_environment"]
    # the honest gap: no verification / acceptance criteria
    assert not checks["verification_criteria"]
    assert requirements_score(checks) == round(5 / 6, 3)


def test_verifiable_requirements_score_full() -> None:
    checks = evaluate_requirements(decision_text=_WITH_VERIFY, artifact_types=set(), goal=_GOAL)
    assert all(checks.values()), checks
    assert requirements_score(checks) == 1.0


def test_generic_prose_scores_low() -> None:
    checks = evaluate_requirements(
        decision_text="The requirements ensure functionality and address power and size.",
        artifact_types=set(),
        goal=_GOAL,
    )
    # a requirement word + a functional verb exist, but nothing quantified/verified
    assert not checks["quantified_constraints"]
    assert not checks["verification_criteria"]
    assert requirements_score(checks) <= round(3 / 6, 3)
