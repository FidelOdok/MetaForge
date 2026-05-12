"""Unit tests for the datasheet PDF parser (MET-430)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from digital_twin.datasheets import (
    PdfDependencyError,
    compute_file_hash,
    extract_pages,
    parse_datasheet_pdf,
)
from twin_core.models import Datasheet

# ---------------------------------------------------------------------------
# compute_file_hash — no dependency on pdfplumber
# ---------------------------------------------------------------------------


class TestComputeFileHash:
    def test_hash_is_deterministic(self) -> None:
        data = b"datasheet bytes"
        assert compute_file_hash(data) == compute_file_hash(data)

    def test_hash_matches_sha256(self) -> None:
        data = b"some-pdf-bytes"
        assert compute_file_hash(data) == hashlib.sha256(data).hexdigest()

    def test_different_inputs_yield_different_hashes(self) -> None:
        assert compute_file_hash(b"a") != compute_file_hash(b"b")

    def test_empty_bytes_hash_is_well_defined(self) -> None:
        # SHA-256 of the empty string is a well-known constant.
        empty_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert compute_file_hash(b"") == empty_sha256


# ---------------------------------------------------------------------------
# extract_pages — depends on pdfplumber. Skip when the dep isn't installed.
# ---------------------------------------------------------------------------


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "knowledge" / "datasheet_excerpt.pdf"

pytestmark_pdf = pytest.mark.skipif(
    not _FIXTURE.exists(),
    reason="datasheet PDF fixture missing",
)


def _pdfplumber_available() -> bool:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


class TestExtractPages:
    @pytest.mark.skipif(not _pdfplumber_available(), reason="pdfplumber not installed")
    @pytestmark_pdf
    def test_extracts_pages_from_fixture(self) -> None:
        pages = extract_pages(_FIXTURE.read_bytes())
        assert len(pages) >= 1
        # At least one page has some text content.
        assert any(page.strip() for page in pages)

    def test_raises_when_pdfplumber_unavailable(self, monkeypatch) -> None:
        """When the dep is absent, ``extract_pages`` raises a clear error.

        Force-blocks the lazy ``import pdfplumber`` so this path is
        exercised even on machines that have the dep installed.
        """
        import builtins
        import sys

        # Drop any cached pdfplumber module so the import re-runs.
        monkeypatch.delitem(sys.modules, "pdfplumber", raising=False)

        original_import = builtins.__import__

        def _block(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "pdfplumber":
                raise ImportError("pdfplumber blocked for test")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block)

        with pytest.raises(PdfDependencyError, match="pdfplumber"):
            extract_pages(b"any-bytes")


# ---------------------------------------------------------------------------
# parse_datasheet_pdf — integration of hash + extract + model build.
# ---------------------------------------------------------------------------


class TestParseDatasheetPdf:
    @pytest.mark.skipif(not _pdfplumber_available(), reason="pdfplumber not installed")
    @pytestmark_pdf
    def test_parse_returns_populated_model(self, tmp_path: Path) -> None:
        # Copy the fixture into a tmp path so the source_path field is
        # not the in-tree fixture (which would be misleading for the
        # citation-bearing field).
        target = tmp_path / "esp32_excerpt.pdf"
        target.write_bytes(_FIXTURE.read_bytes())

        ds = parse_datasheet_pdf(
            target,
            mpn="ESP32-WROOM-32E",
            manufacturer="Espressif",
            revision="rev3",
        )
        assert isinstance(ds, Datasheet)
        assert ds.mpn == "ESP32-WROOM-32E"
        assert ds.manufacturer == "Espressif"
        assert ds.revision == "rev3"
        assert ds.file_hash == compute_file_hash(target.read_bytes())
        assert ds.page_count >= 1
        assert str(target) in ds.source_path
