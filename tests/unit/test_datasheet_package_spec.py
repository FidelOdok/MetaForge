"""Unit tests for datasheet → IC package spec extraction (MET-542).

Deterministic, no PDF/network: a fixture mirrors the TI/JEDEC bracketed-mm
package-outline text (including the glued package code and line-paired
dimension labels that the real LM358 datasheet produces via pypdf).
"""

from __future__ import annotations

from digital_twin.datasheets.package_spec import (
    extract_ic_package_spec,
    find_package_page,
    spec_to_generator_params,
)

# Mirrors the TI LM358 SOIC-8 (D0008A) package-outline page as pypdf extracts it:
# each dimension's label sits on the line above its [mm] value, and the package
# code is glued to the preceding word ("heightD0008A").
LM358_SOIC8 = """www.ti.com
PACKAGE OUTLINE
.228-.244  TYP
[5.80-6.19]
.069 MAX
[1.75]
6X .050
[1.27]
8X .012-.020
[0.31-0.51]
A .189-.197
[4.81-5.00]
B .150-.157
[3.81-3.98]
SOIC - 1.75 mm max heightD0008A
SMALL OUTLINE INTEGRATED CIRCUIT
"""

# A second package on the same datasheet (8-pin PDIP) to test family selection.
LM358_PDIP8 = """www.ti.com
PACKAGE OUTLINE
.400 MAX
[10.16]
6X .100
[2.54]
8X .014-.022
[0.35-0.55]
.355-.400
[9.02-10.16]
.240-.280
[6.10-7.11]
PDIP - 5.08 mm max heightP0008E
"""


class TestExtractIcPackageSpec:
    def test_extracts_soic8_to_datasheet_values(self) -> None:
        spec = extract_ic_package_spec(LM358_SOIC8)
        assert spec is not None
        assert spec["package_type"] == "SOIC"
        assert spec["lead_count"] == 8
        assert spec["pitch"] == 1.27
        assert spec["lead_span"] == 5.995  # mid of 5.80-6.19
        assert spec["body_length"] == 4.905  # mid of 4.81-5.00 (D)
        assert spec["body_width"] == 3.895  # mid of 3.81-3.98 (E1)
        assert spec["height"] == 1.75  # A max
        assert spec["lead_width"] == 0.41  # mid of 0.31-0.51 (b)

    def test_lead_count_from_glued_package_code(self) -> None:
        # "heightD0008A" — no word boundary before the code, must still parse 8.
        spec = extract_ic_package_spec(LM358_SOIC8)
        assert spec is not None and spec["lead_count"] == 8

    def test_max_label_does_not_bleed_into_pitch(self) -> None:
        # Regression: a 48-char window let "MAX" (height line) leak into the
        # pitch's context; line-scoped context keeps height=1.75, pitch=1.27.
        spec = extract_ic_package_spec(LM358_SOIC8)
        assert spec is not None
        assert spec["height"] == 1.75 and spec["pitch"] == 1.27

    def test_non_outline_text_returns_none(self) -> None:
        assert extract_ic_package_spec("Features: low power dual op-amp. VCC 3-32V.") is None


class TestFindPackagePage:
    def test_selects_target_family(self) -> None:
        pages = ["intro text", LM358_PDIP8, "electrical chars", LM358_SOIC8]
        page = find_package_page(pages, target_family="SOIC")
        assert extract_ic_package_spec(page)["package_type"] == "SOIC"

    def test_target_family_dip(self) -> None:
        pages = [LM358_SOIC8, LM358_PDIP8]
        page = find_package_page(pages, target_family="DIP")
        assert extract_ic_package_spec(page)["package_type"] == "DIP"

    def test_no_outline_returns_none(self) -> None:
        assert find_package_page(["just prose", "more prose"]) is None


class TestSpecToGeneratorParams:
    def test_maps_height_to_body_height_and_standoff(self) -> None:
        spec = extract_ic_package_spec(LM358_SOIC8)
        params = spec_to_generator_params(spec)
        assert params["lead_count"] == 8
        assert params["pitch"] == 1.27
        assert params["lead_span"] == 5.995
        # total height (standoff + body_height) reconstructs A.
        assert round(params["standoff"] + params["body_height"], 2) == 1.75
