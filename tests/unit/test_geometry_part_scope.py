"""A size limit that names a part is measured on that part (FORGE-748).

The Chair industrial-design gate reported 13 violations, most of them
``seat_width_min >= 400.1 mm`` against each 40 mm leg and the backrest, and
``seat_depth_min >= 380 mm`` against the seat panel's 18 mm thickness. The
seat itself was never the problem. Parts below are the Chair's, from that gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from api_gateway.runs.geometry_constraints import check_geometry_constraints


def _c(metric: str, limit: float, op: str = ">=") -> SimpleNamespace:
    return SimpleNamespace(
        name=metric,
        metric=metric,
        operator=op,
        limit=limit,
        unit="mm",
        severity=SimpleNamespace(value="error"),
        metadata={},
        acceptance_criteria="",
        message="",
    )


def _box(x: float, y: float, z: float) -> dict[str, Any]:
    return {"bbox_mm": {"min": [0, 0, 0], "max": [x, y, z]}}


SEAT = ("Chair Seat", _box(480, 420, 18))
BACK = ("Chair Backrest", _box(480, 397.05, 87.19))
LEGS = [
    (f"Chair {n} Leg {s}", _box(450, 40, 40)) for n in ("Front", "Rear") for s in ("Left", "Right")
]
ASSEMBLY = (
    "Chair Assembly",
    {
        **_box(850, 500, 472.19),
        "parts": [
            {
                "name": "Chair Seat",
                "position_bbox_mm": {"min": [0, 0, 400], "max": [480, 420, 418]},
            },
            {
                "name": "Chair Backrest",
                "position_bbox_mm": {"min": [0, 400, 418], "max": [480, 487, 850]},
            },
        ],
    },
)
CHAIR = [SEAT, BACK, *LEGS, ASSEMBLY]


def test_a_seat_limit_is_measured_on_the_seat_only() -> None:
    out = check_geometry_constraints([_c("seat_width_min", 400.1)], CHAIR)
    assert out.violations == []
    assert out.evaluated == 1


def test_a_panels_depth_is_not_its_thickness() -> None:
    out = check_geometry_constraints(
        [_c("seat_depth_min", 380), _c("seat_depth_max", 440.9, op="<=")], CHAIR
    )
    assert out.violations == []
    assert out.evaluated == 2


def test_a_real_violation_on_the_named_part_still_fails() -> None:
    narrow = [("Chair Seat", _box(480, 360, 18)), *CHAIR[1:]]
    out = check_geometry_constraints([_c("seat_depth_min", 380)], narrow)
    assert len(out.violations) == 1 and "Chair Seat" in out.violations[0]


def test_a_product_limit_is_measured_on_the_assembly() -> None:
    out = check_geometry_constraints([_c("product_envelope_width", 500, op="<=")], CHAIR)
    assert out.violations == [] and out.evaluated == 1
    tight = check_geometry_constraints([_c("product_envelope_width", 450, op="<=")], CHAIR)
    assert len(tight.violations) == 1 and "Chair Assembly" in tight.violations[0]


def test_a_limit_on_a_part_with_no_model_is_not_evaluated_rather_than_misapplied() -> None:
    out = check_geometry_constraints([_c("armrest_height_min", 200)], CHAIR)
    assert out.violations == [] and out.evaluated == 0
    assert "armrest" in out.not_evaluated[0]


def test_an_unnamed_size_still_applies_to_every_model() -> None:
    out = check_geometry_constraints([_c("envelope_length", 900, op="<=")], [SEAT, *LEGS])
    assert out.violations == [] and out.evaluated == 5
