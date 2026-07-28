"""Architecture correctness rubric — unquantified-budget gap (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from architecture_rubric import architecture_score, evaluate_architecture  # noqa: E402

_GOAL = "an I2C IMU breakout board"

# What the native phase records: good decomposition, but budgets not quantified.
_UNQUANTIFIED = (
    "System Architecture: decomposes into subsystems — structure, sensing (MPU-6050), "
    "compute (a microcontroller), and power (LDO). The MPU-6050 connects to the MCU over I2C; "
    "the LDO provides 3.3 V from 5 V. Each subsystem has allocated budgets for mass and cost, "
    "ensuring compliance with the requirements."
)
_QUANTIFIED = (
    "System Architecture: subsystems — sensing (MPU-6050), compute (MCU), power (LDO). "
    "I2C bus between sense and compute; 3.3 V rail. Budget allocation: sensing 3 g / 40 mW, "
    "compute 2 g / 30 mW, power 2 g / 20 mW — total 7 g within the 10 g requirement."
)


def test_unquantified_budget_misses_one_check() -> None:
    checks = evaluate_architecture(decision_text=_UNQUANTIFIED, artifact_types=set(), goal=_GOAL)
    assert checks["subsystems_decomposed"]
    assert checks["components_mapped"]
    assert checks["traces_to_requirements"]
    assert checks["interfaces_defined"]
    # the honest gap: "allocated budgets" with no per-subsystem number
    assert not checks["budget_allocated_quantified"]
    assert architecture_score(checks) == round(5 / 6, 3)


def test_quantified_budget_scores_full() -> None:
    checks = evaluate_architecture(decision_text=_QUANTIFIED, artifact_types=set(), goal=_GOAL)
    assert all(checks.values()), checks
    assert architecture_score(checks) == 1.0


def test_bare_naming_scores_low() -> None:
    checks = evaluate_architecture(
        decision_text="The architecture is well organized and meets the design.",
        artifact_types=set(),
        goal=_GOAL,
    )
    assert not checks["subsystems_decomposed"]
    assert not checks["budget_allocated_quantified"]
    assert architecture_score(checks) <= round(3 / 6, 3)
