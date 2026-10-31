"""A load position is not a part size (FORGE-716).

Live on the sandbox: a chair run's requirements set ``moment_arm_length <= 0.5 m``
(where the sitter's load acts). The geometry check matched "length", compared
0.5 m with every committed part's largest extent, and held the design gate on
"Chair Back measures 600 mm" and "Chair Assembly measures 1400 mm". No correct
chair could pass, so every retry would have stalled the same way.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.runs.geometry_constraints import (
    check_geometry_constraints,
    classify,
    is_load_position,
)


def _constraint(metric: str, limit: float | None, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        name=kw.pop("name", metric),
        metric=metric,
        limit=limit,
        operator=kw.pop("operator", "<="),
        unit=kw.pop("unit", "mm"),
        severity=SimpleNamespace(value=kw.pop("severity", "error")),
        metadata={},
        **kw,
    )


CHAIR_BACK = {"bbox_mm": {"min": [0, 0, 0], "max": [450, 40, 600]}, "material": "oak"}
CHAIR_ASSEMBLY = {"bbox_mm": {"min": [0, 0, 0], "max": [500, 520, 1400]}, "material": "oak"}


def test_the_live_moment_arm_no_longer_fails_the_chair() -> None:
    moment_arm = _constraint("moment_arm_length", 0.5, name="moment_arm", unit="m")
    out = check_geometry_constraints(
        [moment_arm], [("Chair Back", CHAIR_BACK), ("Chair Assembly", CHAIR_ASSEMBLY)]
    )
    assert out.violations == []
    assert out.evaluated == 0
    # Reported, not silently passed: a reviewer sees it was not checked here.
    assert any("moment_arm" in n and "not a part size" in n for n in out.not_evaluated)


@pytest.mark.parametrize(
    "metric",
    [
        "moment_arm_length",
        "lever_length",
        "load_offset",
        "load-position-height",
        "hole_spacing_length",
        "edge_distance",
        "seat_clearance_height",
        "actuator_stroke_length",
    ],
)
def test_load_positions_and_spacings_are_not_part_sizes(metric: str) -> None:
    assert is_load_position(metric)
    assert classify(metric) is None


@pytest.mark.parametrize(
    ("metric", "kind"),
    [
        ("envelope_length", "envelope"),
        ("seat_height", "envelope"),
        ("max_width", "envelope"),
        ("stock_thickness", "thickness"),
        ("frame_material", "material"),
    ],
)
def test_real_size_limits_are_still_checked(metric: str, kind: str) -> None:
    assert not is_load_position(metric)
    assert classify(metric) == kind


def test_a_real_envelope_limit_still_fails_an_oversized_part() -> None:
    out = check_geometry_constraints(
        [_constraint("envelope_length", 1000)], [("Chair Assembly", CHAIR_ASSEMBLY)]
    )
    assert out.violations == [
        "envelope_length: Chair Assembly measures 1400 mm, requirement is <= 1000 mm"
    ]


def test_an_info_level_load_position_stays_silent() -> None:
    out = check_geometry_constraints(
        [_constraint("moment_arm_length", 0.5, unit="m", severity="info")],
        [("Chair Back", CHAIR_BACK)],
    )
    assert out.violations == [] and out.not_evaluated == []
