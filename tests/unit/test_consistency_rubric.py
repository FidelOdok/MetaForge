"""Cross-phase consistency (digital-thread) rubric (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from consistency_rubric import consistency_score, evaluate_consistency  # noqa: E402

_GOAL = "an MPU-6050 I2C IMU breakout board, 3.3 V from 5 V USB, 25 x 20 mm, mass 10 g"

_COHERENT = {
    "requirements": "Requirements: MPU-6050 on I2C, mass 10 g, 25 x 20 mm, 0.1 W, 3.3 V rail.",
    "architecture": "Architecture: sensing (MPU-6050), power (LDO 3.3 V). Budget 8 g.",
    "electronics": "Schematic: MPU-6050 on I2C at 0x68, LDO 3.3 V. Power 90 mW.",
    "firmware": "Firmware: I2C driver for MPU-6050, pin map SDA/SCL.",
    "vv": "V&V: envelope 24 x 18 mm within 25 x 20 mm. PASS.",
    "manufacturing": "Manufacturing: BOM consolidated; 2-layer PCB; SMT assembly.",
}
_ARTIFACTS = {"cad_model", "bom", "pinmap", "firmware_source", "test_plan", "manufacturing_file"}


def test_coherent_thread_scores_high() -> None:
    checks = evaluate_consistency(phase_texts=_COHERENT, artifact_types=_ARTIFACTS, goal=_GOAL)
    assert all(checks.values()), checks
    assert consistency_score(checks) == 1.0


def test_device_mismatch_breaks_the_thread() -> None:
    # firmware drives a DIFFERENT sensor than the BOM lists -> device thread breaks.
    broken = dict(_COHERENT)
    broken["firmware"] = "Firmware: I2C driver for a BME280, pin map SDA/SCL."
    broken["electronics"] = "Schematic: BME280 on I2C, LDO 3.3 V."
    broken["architecture"] = "Architecture: sensing subsystem, power (LDO 3.3 V)."
    broken["requirements"] = "Requirements: a sensor on I2C, mass 10 g, 3.3 V rail."
    checks = evaluate_consistency(phase_texts=broken, artifact_types=_ARTIFACTS, goal=_GOAL)
    # the goal names MPU-6050 but no phase carries it -> device thread not intact
    assert not checks["device_thread_intact"]


def test_missing_artifact_breaks_artifact_thread() -> None:
    checks = evaluate_consistency(
        phase_texts=_COHERENT, artifact_types={"cad_model", "bom"}, goal=_GOAL
    )
    assert not checks["artifact_thread_complete"]
    assert not checks["manufacturing_references_bom"] or "bom" in {"cad_model", "bom"}


def test_interface_thread_requires_both_phases() -> None:
    one_sided = dict(_COHERENT)
    one_sided["firmware"] = "Firmware: reads the sensor and blinks an LED."  # no bus named
    checks = evaluate_consistency(phase_texts=one_sided, artifact_types=_ARTIFACTS, goal=_GOAL)
    assert not checks["interface_thread_intact"]
