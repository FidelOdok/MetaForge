"""Electronics correctness rubric — scores outcome not presence (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from electronics_rubric import electronics_score, evaluate_electronics  # noqa: E402

_RICH_DECISION = (
    "Schematic Topology and Power Budget for MPU-6050 Breakout Board. "
    "Selected an MPU-6050 IMU on I2C at address 0x68, a 3.3 V LDO regulator from "
    "5 V USB, 0.1uF decoupling capacitors, 4.7k pull-up resistors on SDA/SCL, and "
    "a 0.1-inch pin header. Power budget closed at 0.1 W (about 30 mA at 3.3 V)."
)
_GOAL = "I2C IMU breakout board, 3.3V LDO from 5V USB"


def test_rich_prose_without_artifacts_is_honestly_incomplete() -> None:
    # Prose is strong, but no netlist/ERC/BOM artifact was produced.
    checks = evaluate_electronics(decision_text=_RICH_DECISION, artifact_types=set(), goal=_GOAL)
    assert checks["schematic_topology_defined"]
    assert checks["components_selected"]
    assert checks["power_budget_closed"]
    assert checks["interface_pinout_defined"]
    # The honest gaps: no real validation artifact, no BOM deliverable.
    assert not checks["erc_or_netlist_run"]
    assert not checks["bom_present"]
    assert electronics_score(checks) == round(4 / 6, 3)


def test_full_electronics_run_with_artifacts_scores_perfect() -> None:
    checks = evaluate_electronics(
        decision_text=_RICH_DECISION,
        artifact_types={"netlist", "bom"},
        goal=_GOAL,
    )
    assert all(checks.values())
    assert electronics_score(checks) == 1.0


def test_hollow_decision_scores_low() -> None:
    checks = evaluate_electronics(
        decision_text="We considered the electronics and will design them later.",
        artifact_types=set(),
        goal=_GOAL,
    )
    assert electronics_score(checks) == 0.0


def test_gerber_artifact_counts_as_validation() -> None:
    checks = evaluate_electronics(
        decision_text=_RICH_DECISION, artifact_types={"gerber"}, goal=_GOAL
    )
    assert checks["erc_or_netlist_run"]
