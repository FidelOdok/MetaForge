"""Mechanical correctness rubric — the pure evaluator (MET-10).

Verifies the rubric distinguishes *real* mechanical design from the hollow
deterministic-handler output (hardcoded quadruped part + hand-calc "V&V").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from mechanical_rubric import evaluate_mechanical, mechanical_score  # noqa: E402

# A genuine, goal-driven mechanical run with real FEA.
GOOD_TEXT = (
    "Cantilever arm in Al6061; CalculiX FEA under the 15 N tip load gives max "
    "von Mises 92 MPa vs yield 276 MPa -> safety factor 3.0. Requirement SF>=2.5 -> PASS."
)
# The current deterministic handler output: hardcoded hip bracket + hand-calc.
HOLLOW_TEXT = (
    "First-order cantilever bending check (hand-calc, not FEA): sigma 60 MPa vs "
    "yield 276 MPa -> safety factor 4.6. Requirement SF>=2 -> PASS. CalculiX to confirm later."
)


def test_genuine_mechanical_scores_full() -> None:
    checks = evaluate_mechanical(
        cad_names=["SensorArm"],
        cad_loadable=True,
        decision_text=GOOD_TEXT,
        goal="an aluminum cantilever sensor arm, SF>=2.5",
    )
    assert all(checks.values()), checks
    assert mechanical_score(checks) == 1.0


def test_hollow_handler_output_is_dinged() -> None:
    # Hardcoded HipBracket for a camera-mount goal + a hand-calc, not FEA.
    checks = evaluate_mechanical(
        cad_names=["HipBracket_FL"],
        cad_loadable=True,
        decision_text=HOLLOW_TEXT,
        goal="an aluminum L-bracket to mount a 200 g camera",
    )
    assert checks["goal_traceable_geometry"] is False  # HipBracket != camera bracket
    assert checks["real_fea_run"] is False  # hand-calc, not FEA
    assert mechanical_score(checks) < 1.0


def test_hip_bracket_is_traceable_for_a_quadruped_goal() -> None:
    checks = evaluate_mechanical(
        cad_names=["HipBracket_FL"],
        cad_loadable=True,
        decision_text=GOOD_TEXT,
        goal="a quadruped robot hip mount bracket",
    )
    assert checks["goal_traceable_geometry"] is True


def test_unloadable_cad_fails_loadable_check() -> None:
    checks = evaluate_mechanical(
        cad_names=["SensorArm"], cad_loadable=False, decision_text=GOOD_TEXT, goal="arm"
    )
    assert checks["cad_model_present"] is True
    assert checks["cad_model_loadable"] is False
