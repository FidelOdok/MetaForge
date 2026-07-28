"""Goal-traceability helper — catches template-overfit (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from rubric_common import goal_part_tokens, goal_traceable  # noqa: E402


def test_extracts_part_numbers_only() -> None:
    toks = goal_part_tokens("A BME280 sensor and a microSD card, 40 x 30 mm, 3.3 V, I2C")
    assert "bme280" in toks
    # dimensions, rails, and single-digit buses are not part tokens
    assert not any(t in toks for t in ("4030", "33", "i2c", "microsd"))


def test_traceable_when_artifact_names_the_part() -> None:
    goal = "A BME280 environmental logger over I2C"
    assert goal_traceable("Selected the BME-280 sensor at 0x76", goal)  # hyphen-insensitive
    assert goal_traceable("Selected the BME280 sensor", goal)


def test_not_traceable_when_handler_emits_wrong_default_part() -> None:
    # The overfit failure: a BME280 goal but the artifacts still name MPU-6050.
    goal = "A BME280 environmental logger over I2C"
    assert not goal_traceable("Selected an MPU-6050 IMU at 0x68", goal)


def test_vacuously_traceable_for_generic_goal() -> None:
    # No specific part named -> nothing to trace against.
    assert goal_traceable("anything at all", "a small I2C IMU breakout board")
