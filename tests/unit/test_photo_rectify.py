"""photo.rectify: an angled view corrected from the corners and two references (R14.4)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from tool_registry.tools.photo.rectify import RectifyError, rectify

L, W = 25.4, 17.78  # the board, mm; origin top-left, y down (as the image)

#: A strong perspective: the far edge 40% narrower, the board turned 12 degrees.
TRUE = np.array([[18.0, 4.0, 120.0], [-2.5, 9.0, 80.0], [0.0, 0.018, 1.0]])


def _px(x_mm: float, y_mm: float) -> list[float]:
    x, y, w = TRUE @ np.array([x_mm, y_mm, 1.0])
    return [float(x / w), float(y / w)]


def _args(**extra: Any) -> dict[str, Any]:
    return {
        # Given out of order: the tool orders them.
        "corners_px": [_px(L, W), _px(0, 0), _px(0, W), _px(L, 0)],
        "references": [
            # a 9-pin header span along the bottom edge: 8 x 2.54 mm
            {"from_px": _px(2.54, 16.51), "to_px": _px(22.86, 16.51), "length_mm": 20.32},
            # two holes along the left edge, 12.7 mm apart
            {"from_px": _px(2.54, 2.54), "to_px": _px(2.54, 15.24), "length_mm": 12.7},
        ],
        "features_px": [
            {"name": "H2", "at": _px(22.86, 2.54)},
            {"name": "J1 pin 1", "at": _px(2.54, 16.51)},
        ],
        **extra,
    }


def test_the_true_size_and_positions_come_back_from_an_angled_view() -> None:
    tl, tr = np.array(_px(0, 0)), np.array(_px(L, 0))
    bl, br = np.array(_px(0, W)), np.array(_px(L, W))
    # The view really is distorted: the two long edges differ by more than 10%.
    assert abs(np.linalg.norm(tr - tl) / np.linalg.norm(br - bl) - 1) > 0.1
    out = rectify(_args())
    assert not out["approximate"] and out["notes"] == []
    assert out["size_mm"]["length"] == pytest.approx(L, abs=0.01)
    assert out["size_mm"]["width"] == pytest.approx(W, abs=0.01)
    h2, pin1 = out["features"]
    # Lower-left origin, as BOARD takes positions.
    assert (h2["x_mm"], h2["y_mm"]) == (
        pytest.approx(22.86, abs=0.02),
        pytest.approx(15.24, abs=0.02),
    )
    assert (pin1["x_mm"], pin1["y_mm"]) == (
        pytest.approx(2.54, abs=0.02),
        pytest.approx(1.27, abs=0.02),
    )


def test_one_reference_is_approximate_and_says_why() -> None:
    args = _args()
    args["references"] = args["references"][:1]
    out = rectify(args)
    assert out["approximate"] and all(f["approximate"] for f in out["features"])
    assert out["sizes"]["length"]["value_mm"] == pytest.approx(L, abs=0.01)
    assert out["sizes"]["width"]["approximate"] is True
    assert "two scale references on different sides" in out["notes"][-1]


def test_a_diagonal_reference_is_not_used() -> None:
    args = _args()
    args["references"].append(
        {"from_px": _px(0, 0), "to_px": _px(L, W), "length_mm": float(np.hypot(L, W))}
    )
    out = rectify(args)
    assert len(out["references_used"]) == 2 and "diagonally" in out["notes"][0]


@pytest.mark.parametrize(
    ("corners", "why"),
    [
        ([[0, 0], [10, 0], [10, 10]], "four corners"),
        ([[0, 0], [10, 0], [20, 0], [30, 0]], "convex|degenerate"),
    ],
)
def test_bad_corners_are_refused(corners: list[list[float]], why: str) -> None:
    with pytest.raises(RectifyError, match=why):
        rectify({"corners_px": corners})
