"""Unit tests for the CSV row-level chunker (MET-340).

HP-INGEST-04 expects each CSV row to become its own searchable chunk
so engineers can hit a BOM by MPN. These tests pin the row→chunk
contract:

* one chunk per data row (header excluded)
* per-row ``row_index`` lands in chunk metadata
* row content includes the MPN (i.e. it's the *whole* row, not just
  the first column)
* the rendered content is in ``col=val; col=val`` form
* the header column list ships in chunk metadata for context display
* whitespace-only rows are skipped (no garbage chunks from trailing
  blank lines)
"""

from __future__ import annotations

from pathlib import Path

from digital_twin.knowledge.chunker import CsvRowChunk, chunk_csv

_BOM_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "bom.csv"


def _load_bom() -> str:
    """Load the committed 5-row BOM fixture used by HP-INGEST-04."""
    return _BOM_FIXTURE.read_text(encoding="utf-8")


def test_chunk_csv_produces_one_chunk_per_data_row() -> None:
    """5-row data CSV (1 header + 5 data) -> 5 chunks."""
    chunks = chunk_csv(_load_bom())
    assert len(chunks) == 5
    assert all(isinstance(chunk, CsvRowChunk) for chunk in chunks)


def test_chunk_csv_metadata_carries_row_index() -> None:
    """chunk[2]'s row_index is 2 (zero-based, header excluded)."""
    chunks = chunk_csv(_load_bom())
    assert chunks[2].row_index == 2
    # Sanity: indices are dense and start at 0.
    assert [chunk.row_index for chunk in chunks] == [0, 1, 2, 3, 4]


def test_chunk_csv_content_contains_row_mpn() -> None:
    """chunk[2]'s content contains the MPN of data row 2 (TPS62840DLCR)."""
    chunks = chunk_csv(_load_bom())
    assert "TPS62840DLCR" in chunks[2].content
    # First and last rows too — no off-by-one in the slicing.
    assert "STM32H723VGT6" in chunks[0].content
    assert "RP2040" in chunks[4].content


def test_chunk_csv_content_format_is_key_value() -> None:
    """Content is rendered as ``col=val; col=val; ...``."""
    chunks = chunk_csv(_load_bom())
    first = chunks[0].content
    # Each header column appears as ``col=`` exactly once.
    for col in ("mpn", "manufacturer", "package", "price"):
        assert f"{col}=" in first
    # Pairs are joined with ``"; "`` and there are header_count - 1 separators.
    assert first.count("; ") == 3
    # Spot-check the canonical first row literally.
    assert first == (
        "mpn=STM32H723VGT6; manufacturer=STMicroelectronics; package=LQFP100; price=8.50"
    )


def test_chunk_csv_metadata_carries_header() -> None:
    """Every chunk carries the column list for downstream context."""
    chunks = chunk_csv(_load_bom())
    expected_header = ["mpn", "manufacturer", "package", "price"]
    for chunk in chunks:
        assert chunk.header == expected_header
        # ``columns`` round-trips the row data as a structured mapping.
        assert set(chunk.columns.keys()) == set(expected_header)
    # Pinpoint a known cell.
    assert chunks[1].columns["mpn"] == "BME280"
    assert chunks[3].columns["package"] == "SOIC-18"


def test_chunk_csv_handles_empty_or_whitespace_rows() -> None:
    """Empty / whitespace-only rows are dropped, not turned into chunks."""
    csv_text = (
        "mpn,manufacturer,package,price\n"
        "STM32H723VGT6,STMicroelectronics,LQFP100,8.50\n"
        ",,,\n"  # all-empty row
        "   ,   ,   ,   \n"  # whitespace-only row
        "RP2040,Raspberry Pi,QFN-56,1.00\n"
        "\n"  # trailing blank line — DictReader emits nothing for this
    )
    chunks = chunk_csv(csv_text)
    assert len(chunks) == 2
    assert chunks[0].columns["mpn"] == "STM32H723VGT6"
    assert chunks[1].columns["mpn"] == "RP2040"
    # row_index stays dense — skipped rows do not advance the counter.
    assert [chunk.row_index for chunk in chunks] == [0, 1]


def test_chunk_csv_empty_input_returns_empty_list() -> None:
    """Empty / whitespace-only input -> []. Defensive corner case."""
    assert chunk_csv("") == []
    assert chunk_csv("   \n  \n") == []


def test_chunk_csv_header_only_returns_empty_list() -> None:
    """A CSV that's nothing but a header has no data rows -> []."""
    assert chunk_csv("mpn,manufacturer,package,price\n") == []
