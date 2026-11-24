"""scale.resolve: pixels into sourced millimetres, or what is missing (FORGE-926, R4).

The cases follow the spec's evidence: Adafruit's LSM6DS33 breakout (4480), true outline
25.40 x 17.78 mm from its Eagle board file, read from an angled product photo.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.photo.adapter import PhotoServer
from tool_registry.tools.photo.scale import ScaleError, package_candidates, resolve

# An angled photo: the header's 10 pitches (25.4 mm) span the board's 400 px length.
PHOTO: dict[str, Any] = {
    "image_ref": "image:" + "a" * 64,
    "view": "angled",
    "body_px": {"length": {"px": 400.0}, "width": {"px": 236.0}},
    "references": [{"kind": "header_2.54", "axis": "length", "span_px": 400.0, "count": 10}],
}
SIMILAR = {
    "ref": "adafruit-4485",
    "length_mm": 25.4,
    "width_mm": 17.8,
    "source_ref": "Adafruit 4485 board file",
}


def test_header_holes_alone_set_the_length_and_flag_the_width() -> None:
    out = resolve(PHOTO)
    length, width = out["sizes"]["length"], out["sizes"]["width"]
    assert length["value"] == pytest.approx(25.4) and length["method"] == "photo"
    assert length["source_kind"] == "estimate" and "2.54" in length["basis"]
    # The width reuses the length's scale and, in an angled view, reads short.
    assert width["value"] == pytest.approx(14.986, abs=0.01) and width["reused_scale"]
    assert width["confidence"] < length["confidence"]
    assert any("probably too small" in w for w in out["warnings"])
    assert out["status"] == "partial" and any("a height" in n for n in out["needs"])


def test_a_similar_part_sets_both_sides_and_the_photo_cross_checks() -> None:
    out = resolve({**PHOTO, "similar_part": SIMILAR})
    assert out["sizes"]["length"]["method"] == "similar_part"
    assert out["sizes"]["width"]["value"] == pytest.approx(17.8)
    (check,) = out["sizes"]["length"]["checks"]
    assert check["method"] == "photo" and check["agrees"]


def test_a_printed_size_needs_its_quote_and_an_invented_one_is_dropped() -> None:
    text = "Adafruit LSM6DS33 breakout. Dimensions: 25.4mm x 17.8mm x 4.6mm"
    out = resolve(
        {
            **PHOTO,
            "metadata_text": text,
            "metadata_dims": [
                {"field": "length", "value_mm": 25.4, "quote": "25.4mm x 17.8mm"},
                {"field": "width", "value_mm": 17.8, "quote": "25.4mm x 17.8mm"},
                {"field": "height", "value_mm": 99, "quote": "height 99 mm"},
                {"field": "height", "value_mm": 9.9, "quote": "4.6mm"},
            ],
        }
    )
    assert out["sizes"]["length"]["method"] == "printed"
    assert out["sizes"]["width"]["value"] == 17.8
    whys = [r["why"] for r in out["rejected_quotes"]]
    assert whys == ["the quote is not in the input text", "the quote does not hold that number"]
    assert "height" not in out["sizes"]


def test_a_disagreeing_source_warns_and_lowers_confidence() -> None:
    wrong = {**SIMILAR, "width_mm": 22.0}
    plain = resolve({**PHOTO, "similar_part": SIMILAR})["sizes"]["length"]["confidence"]
    out = resolve({**PHOTO, "similar_part": {**wrong, "length_mm": 31.0}})
    assert any("length: photo gives 25.40 mm" in w for w in out["warnings"])
    assert out["sizes"]["length"]["confidence"] < plain


def test_package_sizes_come_from_the_kicad_name_only() -> None:
    soic = package_candidates("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    assert (soic["length"][0].value, soic["width"][0].value) == (4.9, 3.9)
    chip = package_candidates("Resistor_SMD:R_0603_1608Metric")
    assert (chip["length"][0].value, chip["width"][0].value) == (1.6, 0.8)
    assert package_candidates("Package_QFP:LQFP-48") == {}


def test_pitches_snap_only_when_close() -> None:
    out = resolve(
        {
            **PHOTO,
            "pitches_px": [
                {"axis": "length", "px": 31.0, "count": 1},  # 1.97 mm: snaps to 2.0
                {"axis": "length", "px": 28.0, "count": 1},  # 1.78 mm: 11 % from 2.0, snaps
                {"axis": "length", "px": 47.24, "count": 1},  # 3.0 mm: 18 % from 2.54, no snap
                {"axis": "height", "px": 10.0},
            ],
        }
    )
    a, b, c, d = out["pitches"]
    assert a["snapped"] and a["value_mm"] == 2.0
    assert b["snapped"] and b["value_mm"] == 2.0
    assert not c["snapped"] and c["value_mm"] == pytest.approx(3.0, abs=0.001)
    assert d["status"].startswith("unscaled")


def test_no_source_means_needs_never_a_guess() -> None:
    out = resolve({"view": "top", "body_px": {"length": {"px": 400.0}}})
    assert out["status"] == "needs" and out["sizes"] == {}
    assert "printed size" in out["needs"][0]


def test_an_unknown_scale_object_is_not_used_but_a_cited_one_is() -> None:
    ruler = {
        **PHOTO,
        "references": [{"kind": "ruler_10mm", "axis": "length", "span_px": 160, "count": 1}],
    }
    assert resolve(ruler)["sizes"] == {}
    cited = resolve(
        {
            **ruler,
            "scale_objects": [
                {"kind": "ruler_10mm", "size_mm": 10, "source_ref": "steel rule in the photo"}
            ],
        }
    )
    assert cited["sizes"]["length"]["value"] == pytest.approx(25.0)
    with pytest.raises(ScaleError, match="needs kind, size_mm and source_ref"):
        resolve({**ruler, "scale_objects": [{"kind": "ruler_10mm", "size_mm": 10}]})


async def test_the_tool_answers_and_is_registered() -> None:
    server = PhotoServer()
    assert server.tool_ids == ["scale.resolve"]
    out = await server.scale_resolve(PHOTO)
    assert out["sizes"]["length"]["value"] == pytest.approx(25.4)
