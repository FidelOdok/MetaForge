"""Manufacturing rubric — catches false-readiness (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from manufacturing_rubric import evaluate_manufacturing, manufacturing_score  # noqa: E402

_GOAL = "an I2C IMU breakout board"

# The real case: claims readiness while admitting the exports failed.
_FALSE_READY = (
    "Manufacturing Plan for IMU Breakout Board. Consolidated the BOM and identified "
    "fabrication outputs to prepare for manufacturing, ensuring compliance with design "
    "requirements. During the phase I encountered errors while attempting to export the "
    "BOM and netlist for the PCB, preventing successful execution of these tasks."
)
# Earned readiness: real fab package + process + assembly, no failure.
_EARNED = (
    "Manufacturing readiness for the breakout board: 2-layer PCB fabrication, SMT reflow "
    "assembly, panelization x4. BOM consolidated for procurement. Assembly and AOI "
    "inspection work order attached; bring-up test noted."
)


def test_false_readiness_is_caught() -> None:
    checks = evaluate_manufacturing(decision_text=_FALSE_READY, artifact_types={"bom"}, goal=_GOAL)
    assert checks["mfg_decision_present"] and checks["bom_consolidated"]
    # no real fab artifact, and it IS a false readiness (claims + admits failure)
    assert not checks["mfg_artifact_present"]
    assert not checks["not_false_ready"], "readiness + 'encountered errors' must flag false ready"
    assert manufacturing_score(checks) <= round(3 / 6, 3)


def test_earned_readiness_with_fab_artifact_scores_high() -> None:
    checks = evaluate_manufacturing(
        decision_text=_EARNED,
        artifact_types={"bom", "gerber", "pick_and_place"},
        goal=_GOAL,
    )
    assert all(checks.values()), checks
    assert manufacturing_score(checks) == 1.0


def test_honest_deferral_is_not_false_ready() -> None:
    text = (
        "Manufacturing readiness: BOM consolidated for procurement; 2-layer PCB fab and SMT "
        "assembly planned. Gerber/pick-and-place export deferred to Phase 2 (needs PCB layout); "
        "not asserted as fab-ready."
    )
    checks = evaluate_manufacturing(decision_text=text, artifact_types={"bom"}, goal=_GOAL)
    assert checks["not_false_ready"]  # honest deferral, no failure language
