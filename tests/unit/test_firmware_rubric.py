"""Firmware correctness rubric — scores outcome not presence (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from firmware_rubric import evaluate_firmware, firmware_score  # noqa: E402

_GOAL = "I2C IMU breakout board"

# What the native firmware phase actually records: generic architecture prose.
_GENERIC = (
    "Firmware and Control Architecture for Breakout Board. RTOS configuration, "
    "control loop design, and pin mappings necessary to interface the IMU."
)
# A real firmware design: concrete driver + registers + bring-up.
_CONCRETE = (
    "Firmware for the MPU-6050 breakout. I2C driver: init the bus at 400 kHz, "
    "read WHO_AM_I at 0x75, configure the sample rate to 100 Hz via register 0x19, "
    "then poll the accel/gyro registers. Bring-up: verify WHO_AM_I self-test."
)


def test_generic_prose_without_artifacts_scores_low() -> None:
    checks = evaluate_firmware(decision_text=_GENERIC, artifact_types=set(), goal=_GOAL)
    assert checks["firmware_decision_present"]  # RTOS / control loop
    # generic prose names no concrete bus driver, registers, artifacts, or bring-up
    assert not checks["interface_driver_defined"]
    assert not checks["register_or_sampling_detail"]
    assert not checks["pinmap_present"]
    assert not checks["firmware_source_present"]
    assert not checks["bringup_or_test_noted"]
    assert firmware_score(checks) == round(1 / 6, 3)


def test_concrete_firmware_with_artifacts_scores_high() -> None:
    checks = evaluate_firmware(
        decision_text=_CONCRETE,
        artifact_types={"pinmap", "firmware_source"},
        goal=_GOAL,
    )
    assert all(checks.values())
    assert firmware_score(checks) == 1.0


def test_artifacts_alone_do_not_grant_prose_checks() -> None:
    # A pinmap + scaffold exist, but the decision text is empty.
    checks = evaluate_firmware(
        decision_text="", artifact_types={"pinmap", "firmware_source"}, goal=_GOAL
    )
    assert checks["pinmap_present"] and checks["firmware_source_present"]
    assert not checks["interface_driver_defined"]
    assert firmware_score(checks) == round(2 / 6, 3)
