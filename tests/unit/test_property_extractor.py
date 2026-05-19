"""Unit tests for the typed property extractor (MET-445, MET-433)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from digital_twin.knowledge.property_extractor import (
    ExtractedProperty,
    ExtractionMethod,
    _normalise,
    _value_and_unit,
    extract_properties_for_mpn,
    extract_property_from_tables,
)


class _StubTwin:
    """Minimal Twin double — only ``get_current_datasheet`` is exercised."""

    def __init__(self, datasheet: Any | None = None) -> None:
        self._datasheet = datasheet
        self.calls: list[str] = []

    async def get_current_datasheet(self, mpn: str) -> Any | None:
        self.calls.append(mpn)
        return self._datasheet


class _StubDatasheet:
    """Pydantic-shaped stand-in matching ``twin_core.models.Datasheet``."""

    def __init__(
        self,
        *,
        revision: str = "rev1",
        published_at: datetime | None = None,
        source_path: str = "",
        source_url: str | None = None,
        tables: list[dict[str, Any]] | None = None,
    ) -> None:
        self.revision = revision
        self.published_at = published_at
        self.source_path = source_path
        self.source_url = source_url
        self.metadata: dict[str, Any] = {"tables": tables or []}

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


# ---------------------------------------------------------------------------
# extract_properties_for_mpn — MET-433 datasheet-aware helper
# ---------------------------------------------------------------------------


class TestExtractPropertiesForMpn:
    async def test_returns_mpn_not_found_when_twin_has_no_datasheet(self) -> None:
        """No datasheet for the MPN → ``mpn_found=False`` and empty items.

        The empty-items branch is what lets callers distinguish "we
        haven't ingested this part yet" from "we've ingested it but
        the property wasn't in any table" — both are valid states.
        """
        twin = _StubTwin(datasheet=None)
        result = await extract_properties_for_mpn(
            twin, "ESP32-WROOM-32", ["operating_voltage"]
        )
        assert twin.calls == ["ESP32-WROOM-32"]
        assert result.mpn_found is False
        assert result.items == []
        assert result.datasheet_revision is None
        assert result.datasheet_source_path is None

    async def test_returns_per_property_results_in_input_order(self) -> None:
        """Items must be one-per-input-property in input order — callers
        zip back to their request list, not match by name.
        """
        tables = [
            {
                "page": 5,
                "heading": "Electrical Characteristics",
                "rows": [
                    ["Operating Voltage", "3.3 V"],
                    ["Operating Temperature", "-40 to 85 °C"],
                ],
            }
        ]
        twin = _StubTwin(datasheet=_StubDatasheet(revision="3.4", tables=tables))
        result = await extract_properties_for_mpn(
            twin, "ESP32-WROOM-32", ["operating_voltage", "missing_thing"]
        )
        assert result.mpn_found is True
        assert result.datasheet_revision == "3.4"
        assert len(result.items) == 2
        # Order preserved.
        assert result.items[0].property_name == "operating_voltage"
        assert result.items[0].value == "3.3"
        assert result.items[0].unit == "V"
        assert result.items[1].property_name == "missing_thing"
        assert result.items[1].value is None
        assert result.items[1].extraction_method == ExtractionMethod.NOT_FOUND

    async def test_aliases_passed_through_to_matcher(self) -> None:
        """Aliases per-property widen Tier-1 matches without affecting
        scoring. Without ``VCC`` aliased, supply_voltage wouldn't hit.
        """
        tables = [
            {
                "page": 1,
                "rows": [["VCC", "5.0 V"]],
            }
        ]
        twin = _StubTwin(datasheet=_StubDatasheet(tables=tables))
        result = await extract_properties_for_mpn(
            twin,
            "X",
            ["supply_voltage"],
            aliases={"supply_voltage": ["VCC", "VDD"]},
        )
        assert result.items[0].value == "5.0"
        assert result.items[0].unit == "V"

    async def test_uses_source_url_when_present_else_source_path(self) -> None:
        """``datasheet_source_path`` prefers ``source_url`` (canonical
        manufacturer URL) over the local ``source_path`` — the citation
        chain should be stable across mirror/cache layouts.
        """
        twin = _StubTwin(
            datasheet=_StubDatasheet(
                source_path="/tmp/cached.pdf",
                source_url="https://manufacturer.example/ds.pdf",
                tables=[],
            )
        )
        result = await extract_properties_for_mpn(twin, "X", ["any"])
        assert result.datasheet_source_path == "https://manufacturer.example/ds.pdf"

    async def test_falls_back_to_source_path_when_url_missing(self) -> None:
        twin = _StubTwin(
            datasheet=_StubDatasheet(
                source_path="/srv/datasheets/x.pdf",
                source_url=None,
                tables=[],
            )
        )
        result = await extract_properties_for_mpn(twin, "X", ["any"])
        assert result.datasheet_source_path == "/srv/datasheets/x.pdf"

    async def test_empty_metadata_dict_is_tolerated(self) -> None:
        """A Datasheet with no ``tables`` key (older ingests) returns
        NOT_FOUND for every property rather than crashing.
        """
        twin = _StubTwin(datasheet=_StubDatasheet(tables=None))
        result = await extract_properties_for_mpn(twin, "X", ["anything"])
        assert result.mpn_found is True
        assert len(result.items) == 1
        assert result.items[0].extraction_method == ExtractionMethod.NOT_FOUND

    async def test_published_at_round_trips_unchanged(self) -> None:
        ts = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        twin = _StubTwin(datasheet=_StubDatasheet(published_at=ts, tables=[]))
        result = await extract_properties_for_mpn(twin, "X", ["any"])
        assert result.datasheet_published_at == ts

    async def test_empty_properties_list_yields_empty_items(self) -> None:
        """Requesting zero properties is well-defined: items is empty
        but ``mpn_found`` still reflects whether the datasheet was located.
        """
        twin = _StubTwin(datasheet=_StubDatasheet(tables=[]))
        result = await extract_properties_for_mpn(twin, "X", [])
        assert result.mpn_found is True
        assert result.items == []

    async def test_property_name_preserved_in_each_item(self) -> None:
        """When an alias matches, the returned item keeps the *requested*
        property_name (not the alias that matched the table cell) so the
        wire response maps back to the caller's request.
        """
        tables = [{"page": 1, "rows": [["VCC", "3.3 V"]]}]
        twin = _StubTwin(datasheet=_StubDatasheet(tables=tables))
        result = await extract_properties_for_mpn(
            twin,
            "X",
            ["supply_voltage"],
            aliases={"supply_voltage": ["VCC"]},
        )
        assert result.items[0].property_name == "supply_voltage"
