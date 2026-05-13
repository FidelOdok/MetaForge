"""Unit tests for PDF table extraction (MET-444)."""

from __future__ import annotations

from pathlib import Path

import pytest

from digital_twin.datasheets.tables import (
    Table,
    _clean_rows,
    _infer_columns,
)


def _pdfplumber_available() -> bool:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "knowledge" / "datasheet_excerpt.pdf"


# ---------------------------------------------------------------------------
# Pure helpers (no PDF needed)
# ---------------------------------------------------------------------------


class TestCleanRows:
    def test_strips_whitespace(self) -> None:
        out = _clean_rows([["  a  ", " b "]])
        assert out == [["a", "b"]]

    def test_drops_fully_empty_rows(self) -> None:
        out = _clean_rows([["a"], ["", ""], ["b"]])
        assert out == [["a"], ["b"]]

    def test_normalises_none_to_empty_string(self) -> None:
        out = _clean_rows([["a", None, "c"]])
        assert out == [["a", "", "c"]]

    def test_empty_input_returns_empty(self) -> None:
        assert _clean_rows([]) == []


class TestInferColumns:
    def test_header_row_when_short_text_cells(self) -> None:
        rows = [["Parameter", "Min", "Max"], ["Vdd", "1.8", "3.6"]]
        assert _infer_columns(rows) == ["Parameter", "Min", "Max"]

    def test_returns_none_when_header_has_empty_cell(self) -> None:
        rows = [["A", "", "C"], ["1", "2", "3"]]
        assert _infer_columns(rows) is None

    def test_returns_none_when_header_is_numeric_only(self) -> None:
        rows = [["100", "200", "300"], ["a", "b", "c"]]
        assert _infer_columns(rows) is None

    def test_returns_none_when_header_too_long(self) -> None:
        long_cell = "x" * 40
        rows = [[long_cell, long_cell, long_cell], ["a", "b", "c"]]
        assert _infer_columns(rows) is None

    def test_returns_none_for_empty_input(self) -> None:
        assert _infer_columns([]) is None


class TestTableDataclass:
    def test_is_empty_for_no_rows(self) -> None:
        assert Table(page=1, rows=[]).is_empty is True

    def test_is_empty_for_all_empty_rows(self) -> None:
        assert Table(page=1, rows=[[], []]).is_empty is True

    def test_not_empty_when_one_row_has_cells(self) -> None:
        assert Table(page=1, rows=[["a"], []]).is_empty is False


# ---------------------------------------------------------------------------
# Real PDF extraction (skipped when pdfplumber is absent)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _pdfplumber_available() or not _FIXTURE.exists(),
    reason="pdfplumber not installed or fixture missing",
)
class TestExtractTablesAgainstFixture:
    def test_returns_list(self) -> None:
        from digital_twin.datasheets.tables import extract_tables

        tables = extract_tables(_FIXTURE.read_bytes())
        # Permissive — the in-repo fixture is a small excerpt and may
        # or may not contain detectable tables. The contract is "list,
        # never error" — that's what we assert.
        assert isinstance(tables, list)
        for t in tables:
            assert isinstance(t, Table)
            assert t.page >= 1
            assert isinstance(t.rows, list)


class TestExtractTablesMissingDependency:
    def test_raises_when_pdfplumber_unavailable(self, monkeypatch) -> None:
        """When the dep is absent, ``extract_tables`` raises a clear error."""
        import builtins
        import sys

        from digital_twin.datasheets.parser import PdfDependencyError
        from digital_twin.datasheets.tables import extract_tables

        monkeypatch.delitem(sys.modules, "pdfplumber", raising=False)
        original_import = builtins.__import__

        def _block(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "pdfplumber":
                raise ImportError("pdfplumber blocked for test")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block)

        with pytest.raises(PdfDependencyError, match="pdfplumber"):
            extract_tables(b"any-bytes")


# ---------------------------------------------------------------------------
# parse_datasheet_pdf wiring
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _pdfplumber_available() or not _FIXTURE.exists(),
    reason="pdfplumber not installed or fixture missing",
)
class TestParserWiring:
    def test_parse_stashes_tables_in_metadata(self, tmp_path: Path) -> None:
        """``parse_datasheet_pdf`` populates ``datasheet.metadata['tables']``
        only when the extractor found at least one table.
        """
        from digital_twin.datasheets import parse_datasheet_pdf

        target = tmp_path / "ds.pdf"
        target.write_bytes(_FIXTURE.read_bytes())

        ds = parse_datasheet_pdf(
            target,
            mpn="X",
            manufacturer="Y",
            revision="rev1",
        )
        # Permissive — fixture-dependent. If tables are present they
        # round-trip as dicts; if absent the key is missing.
        if "tables" in ds.metadata:
            assert isinstance(ds.metadata["tables"], list)
            for t in ds.metadata["tables"]:
                assert "page" in t and "rows" in t
            assert ds.metadata["table_count"] == len(ds.metadata["tables"])
        else:
            assert "table_count" not in ds.metadata

    def test_parse_with_extract_tables_false_skips_metadata(self, tmp_path: Path) -> None:
        from digital_twin.datasheets import parse_datasheet_pdf

        target = tmp_path / "ds.pdf"
        target.write_bytes(_FIXTURE.read_bytes())

        ds = parse_datasheet_pdf(
            target,
            mpn="X",
            manufacturer="Y",
            revision="rev1",
            extract_tables=False,
        )
        assert "tables" not in ds.metadata
        assert "table_count" not in ds.metadata
