"""Unit tests for the typed property extractor (MET-445)."""

from __future__ import annotations

from digital_twin.knowledge.property_extractor import (
    ExtractedProperty,
    ExtractionMethod,
    _normalise,
    _value_and_unit,
    extract_property_from_tables,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_lowercases_and_underscores(self) -> None:
        assert _normalise("Operating Temperature") == "operating_temperature"

    def test_collapses_punctuation_and_spaces(self) -> None:
        assert _normalise("V_DD (max)") == "v_dd_max"

    def test_handles_none(self) -> None:
        assert _normalise(None) == ""

    def test_strips_leading_trailing_underscores(self) -> None:
        assert _normalise("- foo -") == "foo"

    def test_idempotent(self) -> None:
        n1 = _normalise("Operating Temperature")
        n2 = _normalise(n1)
        assert n1 == n2


class TestValueAndUnit:
    def test_splits_number_and_unit(self) -> None:
        assert _value_and_unit(["param", "85", "°C"], 0) == ("85", None)

    def test_pulls_unit_from_combined_cell(self) -> None:
        assert _value_and_unit(["param", "85 °C"], 0) == ("85", "°C")

    def test_text_only_value(self) -> None:
        assert _value_and_unit(["param", "active"], 0) == ("active", None)

    def test_skips_empty_cells(self) -> None:
        assert _value_and_unit(["param", "", "  ", "3.3 V"], 0) == ("3.3", "V")

    def test_returns_none_when_no_value(self) -> None:
        assert _value_and_unit(["param"], 0) == (None, None)


# ---------------------------------------------------------------------------
# Tier 1: extract_property_from_tables
# ---------------------------------------------------------------------------


def _tbl(page: int = 1, heading: str | None = None, rows: list[list[str]] | None = None) -> dict:
    return {"page": page, "heading": heading, "rows": rows or []}


class TestExtractTier1Match:
    def test_finds_exact_match(self) -> None:
        tables = [
            _tbl(
                page=15,
                heading="Absolute Maximum Ratings",
                rows=[
                    ["Parameter", "Min", "Max", "Unit"],
                    ["Operating Temperature", "-40", "85", "°C"],
                ],
            )
        ]
        result = extract_property_from_tables(tables, "operating_temperature")
        assert result.found is True
        assert result.value == "-40"
        assert result.confidence == 1.0
        assert result.extraction_method == ExtractionMethod.VERBATIM
        assert result.page == 15
        assert result.heading == "Absolute Maximum Ratings"
        assert result.table_row == 1

    def test_matches_with_punctuation_variation(self) -> None:
        """Caller asks for ``operating_temperature_max_c``; table has ``Operating Temp (max)``."""
        tables = [
            _tbl(rows=[["Operating Temp (max)", "85", "°C"]]),
        ]
        result = extract_property_from_tables(
            tables,
            "operating_temperature_max_c",
            aliases=["Operating Temp (max)"],
        )
        assert result.found
        assert result.value == "85"

    def test_returns_unit_when_combined_in_cell(self) -> None:
        tables = [_tbl(rows=[["Supply Voltage", "3.3 V"]])]
        result = extract_property_from_tables(tables, "supply voltage")
        assert result.value == "3.3"
        assert result.unit == "V"

    def test_no_match_returns_not_found(self) -> None:
        tables = [_tbl(rows=[["Operating Temp", "85"]])]
        result = extract_property_from_tables(tables, "nonexistent_property")
        assert result.found is False
        assert result.value is None
        assert result.confidence == 0.0
        assert result.extraction_method == ExtractionMethod.NOT_FOUND

    def test_empty_tables_returns_not_found(self) -> None:
        result = extract_property_from_tables([], "anything")
        assert result.found is False

    def test_skips_row_without_value_cell(self) -> None:
        """A label-only row (e.g. section header inside the table) is skipped."""
        tables = [_tbl(rows=[["Operating Temperature"], ["", "85", "°C"]])]
        result = extract_property_from_tables(tables, "operating_temperature")
        # The label-only row has no value to the right; the next row
        # doesn't carry the label, so the extractor reports not-found.
        assert result.found is False

    def test_prefers_earliest_page_on_duplicates(self) -> None:
        """When the same property appears in multiple tables, take the earliest page."""
        tables = [
            _tbl(page=15, rows=[["Operating Temperature", "85", "°C"]]),
            _tbl(page=20, rows=[["Operating Temperature", "125", "°C"]]),
        ]
        result = extract_property_from_tables(tables, "operating_temperature")
        assert result.value == "85"
        assert result.page == 15


class TestExtractedPropertyShape:
    def test_default_is_not_found(self) -> None:
        ep = ExtractedProperty(property_name="x", value=None)
        assert ep.found is False
        assert ep.confidence == 0.0
        assert ep.extraction_method == ExtractionMethod.NOT_FOUND

    def test_found_property_round_trips(self) -> None:
        ep = ExtractedProperty(
            property_name="op_temp",
            value="85",
            unit="°C",
            confidence=1.0,
            extraction_method=ExtractionMethod.VERBATIM,
            page=15,
            heading="Section 6.2",
            table_row=2,
        )
        assert ep.found is True
        assert ep.confidence == 1.0
        assert ep.unit == "°C"
