"""photo.measure: code measures what a reader marked (FORGE-925, R3.6).

Synthetic images with known answers: a part of known pixel size (straight and turned),
holes at a known pitch, a row of pins.
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from tool_registry.tools.photo.adapter import PhotoServer
from tool_registry.tools.photo.measure import measure
from tool_registry.tools.photo.scale import resolve
from tool_registry.tools.web.image_fetch import FetchedImage, ImageStore


def _canvas(w: int = 400, h: int = 300) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (w, h), 220)
    return image, ImageDraw.Draw(image)


def _grey(image: Image.Image) -> np.ndarray[Any, Any]:
    return np.asarray(image, dtype=np.float64)


def _rect(
    cx: float, cy: float, length: float, width: float, angle_deg: float
) -> list[tuple[float, float]]:
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -uy, ux
    hl, hw = length / 2, width / 2
    return [
        (cx + sx * hl * ux + sy * hw * vx, cy + sx * hl * uy + sy * hw * vy)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


@pytest.mark.parametrize("angle", [0.0, 15.0])
def test_the_body_is_measured_along_its_own_axes(angle: float) -> None:
    image, draw = _canvas()
    draw.polygon(_rect(200, 150, 200, 100, angle), fill=30)
    out = measure(_grey(image), [{"kind": "body", "box": [70, 70, 260, 160]}])
    (body,) = out["regions"]
    # Tight on a straight part; a turned one loses a pixel or two to its jagged edges.
    tol = 1.0 if angle == 0 else 2.5
    assert body["length_px"] == pytest.approx(200, abs=tol)
    assert body["width_px"] == pytest.approx(100, abs=tol)
    assert body["angle_deg"] == pytest.approx(angle, abs=1.0)
    assert len(body["corners"]) == 4 and body["confidence"] > 0.8
    assert out["notes"] == []


def test_a_turned_square_is_found_turned() -> None:
    """A square's spread is the same in every direction, so its turn comes from its edges
    (the RP2040 on LCSC's grid paper read as its upright bounding box before)."""
    image, draw = _canvas()
    draw.polygon(_rect(200, 150, 140, 140, 30.0), fill=30)
    (body,) = measure(_grey(image), [{"kind": "body", "box": [100, 50, 200, 200]}])["regions"]
    assert body["length_px"] == pytest.approx(140, abs=2.5)
    assert body["width_px"] == pytest.approx(140, abs=2.5)
    assert abs(body["angle_deg"]) in (pytest.approx(30, abs=1.5), pytest.approx(60, abs=1.5))


def test_thin_lines_touching_the_part_are_not_part_of_it() -> None:
    image, draw = _canvas()
    for x in range(0, 400, 20):  # grid paper
        draw.line([(x, 0), (x, 299)], fill=90, width=1)
    draw.rectangle([120, 100, 279, 199], fill=30)  # 160 x 100
    (body,) = measure(_grey(image), [{"kind": "body", "box": [90, 80, 220, 140]}])["regions"]
    assert body["length_px"] == pytest.approx(160, abs=1.5)
    assert body["width_px"] == pytest.approx(100, abs=1.5)


def test_a_reader_who_disagrees_lowers_confidence_and_is_noted() -> None:
    image, draw = _canvas()
    draw.rectangle([100, 100, 299, 199], fill=30)
    agreed = measure(
        _grey(image), [{"kind": "body", "box": [80, 80, 240, 140], "reader_length_px": 201}]
    )
    out = measure(
        _grey(image), [{"kind": "body", "box": [80, 80, 240, 140], "reader_length_px": 240}]
    )
    assert out["regions"][0]["confidence"] < agreed["regions"][0]["confidence"]
    assert "reader said 240.0 px" in out["notes"][0]


def test_holes_give_centres_diameters_and_pitch() -> None:
    image, draw = _canvas()
    for x in (60, 85, 110, 135, 160):
        draw.ellipse([x - 8, 142, x + 8, 158], fill=20)
    out = measure(
        _grey(image),
        [
            {
                "kind": "holes",
                "points": [[61, 151], [84, 149], [111, 150], [134, 151], [159, 150]],
                "radius_px": 8,
            }
        ],
    )
    (holes,) = out["regions"]
    assert holes["pitch_px"] == pytest.approx(25, abs=0.3)
    assert holes["span_px"] == pytest.approx(100, abs=0.5) and holes["count"] == 4
    assert all(d == pytest.approx(17, abs=1.5) for d in holes["diameters_px"])


def test_a_row_of_pins_gives_its_span_and_pitch() -> None:
    image, draw = _canvas()
    for i in range(11):  # 11 pins, 10 pitches of 20 px
        x = 50 + 20 * i
        draw.rectangle([x - 4, 100, x + 4, 120], fill=25)
    out = measure(_grey(image), [{"kind": "row", "box": [40, 98, 220, 24], "count": 10}])
    (row,) = out["regions"]
    assert row["features"] == 11 and row["count"] == 10
    assert row["pitch_px"] == pytest.approx(20, abs=0.2) and row["span_px"] == pytest.approx(
        200, abs=1
    )


def test_a_region_that_cannot_be_measured_says_why() -> None:
    image, _draw = _canvas()
    out = measure(
        _grey(image),
        [{"kind": "body", "box": [500, 500, 10, 10]}, {"kind": "blob", "box": [0, 0, 5, 5]}],
    )
    assert "outside the image" in out["regions"][0]["error"]
    assert "kind is body, holes or row" in out["regions"][1]["error"]


async def test_measure_then_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A board with a 2.54 mm header along it: measure in pixels, then size in mm."""
    image, draw = _canvas(500, 300)
    draw.rectangle([30, 80, 469, 235], fill=40)  # the board, 440 x 156 px
    for i in range(11):  # a header along the length: 10 pitches over 400 px
        x = 50 + 40 * i
        draw.ellipse([x - 6, 94, x + 6, 106], fill=230)
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    monkeypatch.setenv("METAFORGE_IMAGE_ROOT", str(tmp_path))
    ImageStore(tmp_path).put(
        data, FetchedImage(f"image:{sha}", sha, "image/png", 500, 300, "https://x", "", len(data))
    )
    out = await PhotoServer().photo_measure(
        {
            "image_ref": f"image:{sha}",
            "view": "top",
            "regions": [
                {"kind": "body", "box": [10, 60, 480, 196], "use": "body"},
                {
                    "kind": "holes",
                    "points": [[50 + 40 * i, 100] for i in range(11)],
                    "radius_px": 6,
                    "use": "pitch",
                    "axis": "length",
                },
            ],
        }
    )
    body = out["regions"][0]
    assert body["length_px"] == pytest.approx(440, abs=1)
    scale = out["scale_input"]
    assert scale["body_px"]["length"]["px"] == body["length_px"]
    scale["references"] = [
        {
            "kind": "header_2.54",
            "axis": "length",
            "span_px": out["regions"][1]["span_px"],
            "count": 10,
        }
    ]
    sized = resolve(scale)
    # The header's 10 pitches (25.4 mm) span 400 px, so the 440 px board is 27.94 mm.
    assert sized["sizes"]["length"]["value"] == pytest.approx(27.94, rel=0.005)
    assert sized["pitches"][0]["snapped"] and sized["pitches"][0]["value_mm"] == 2.54
