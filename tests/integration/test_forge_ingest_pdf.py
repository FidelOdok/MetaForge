"""End-to-end CLI PDF directory walker tests (MET-399 L1-C3).

Closes the loop between L1-A3 (server-side pdfplumber wiring in
``LightRAGKnowledgeService.ingest``) and the CLI walker: ``forge ingest``
must (a) discover ``.pdf`` files in a directory walk, (b) dispatch each
one through ``ForgeClient.ingest_document`` with the latin-1 byte
round-trip the gateway expects, and (c) honour ``--dry-run`` by listing
the PDF without making any HTTP calls.

The ``--dry-run`` cases drive the real CLI as a subprocess (mirroring
``tests/unit/test_forge_ingest_errors.py``) so the argparse glue is
exercised end-to-end. The non-dry-run cases drive ``ingest_path``
in-process with a stubbed ``ForgeClient`` — the assertion the spec wants
is "the CLI sent the PDF bytes to the gateway", not whatever the gateway
did with them. That isolation also keeps these tests fast (<5 s total)
and dependency-free (no asyncpg / pgvector / lightrag-hku required).

Reuses the committed ``tests/fixtures/knowledge/datasheet_excerpt.pdf``
fixture so we don't duplicate a 5 KB binary into another tree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cli.forge_cli.ingest import ingest_path

# Repository root = two levels up from this test file
# (tests/integration/test_forge_ingest_pdf.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"

_PDF_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "knowledge" / "datasheet_excerpt.pdf"


def _run_ingest(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m cli.forge_cli.main ingest <args>`` from the repo root."""
    interpreter = str(_PYTHON) if _PYTHON.exists() else sys.executable
    cmd = [interpreter, "-m", "cli.forge_cli.main", "ingest", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )


@pytest.fixture
def pdf_fixture_in_tmp(tmp_path: Path) -> Path:
    """Copy the committed PDF fixture into ``tmp_path`` and return the new path."""
    if not _PDF_FIXTURE.exists():
        pytest.skip(f"PDF fixture missing at {_PDF_FIXTURE}")
    target = tmp_path / "datasheet_excerpt.pdf"
    shutil.copy(_PDF_FIXTURE, target)
    return target


# ---------------------------------------------------------------------------
# --dry-run walker discovery
# ---------------------------------------------------------------------------


class TestDryRunDiscovery:
    def test_dry_run_walker_discovers_pdf_in_directory(
        self,
        tmp_path: Path,
        pdf_fixture_in_tmp: Path,  # noqa: ARG002 — copies PDF into tmp_path
    ) -> None:
        """Directory walk with --dry-run lists the PDF and exits 0."""
        proc = _run_ingest(str(tmp_path), "--dry-run")

        assert proc.returncode == 0, (
            f"expected exit 0; got {proc.returncode}\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
        assert "datasheet_excerpt.pdf" in proc.stdout, proc.stdout
        # Dry-run must NOT have made any network calls — no HTTP errors
        # should leak. (We don't bind to a port; httpx isn't even imported
        # on this path.)
        assert "Traceback (most recent call last):" not in proc.stderr, proc.stderr

    def test_dry_run_walker_skips_unsupported(
        self,
        tmp_path: Path,
        pdf_fixture_in_tmp: Path,  # noqa: ARG002 — copies PDF into tmp_path
    ) -> None:
        """Walker reports .pdf and .md, silently filters .jpg."""
        (tmp_path / "notes.md").write_text("# Notes\n\nbody\n", encoding="utf-8")
        (tmp_path / "image.jpg").write_bytes(b"\xff\xd8\xff JPG payload")

        proc = _run_ingest(str(tmp_path), "--dry-run")

        assert proc.returncode == 0, proc.stderr
        assert "datasheet_excerpt.pdf" in proc.stdout, proc.stdout
        assert "notes.md" in proc.stdout, proc.stdout
        # Unsupported extension must be silently filtered — no mention
        # in stdout (would-be-ingested list) or stderr (warnings).
        assert "image.jpg" not in proc.stdout, proc.stdout
        assert "image.jpg" not in proc.stderr, proc.stderr
        assert "Traceback (most recent call last):" not in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# Non-dry-run dispatch path (mocked ForgeClient.ingest_document)
# ---------------------------------------------------------------------------


def _make_stub_client(response: dict[str, Any]) -> MagicMock:
    """Build a MagicMock stand-in for ``ForgeClient`` whose ``ingest_document``
    returns ``response``. Returns the mock so callers can assert on calls.
    """
    stub = MagicMock()
    stub.ingest_document = MagicMock(return_value=response)
    return stub


class TestPdfDispatchedViaIngestPath:
    def test_pdf_dispatched_via_ingest_path_unit(self) -> None:
        """Single-PDF ``ingest_path`` round-trips PDF bytes through the client.

        The CLI reads PDFs as latin-1 (``_read_file_content``) so the
        payload is JSON-safe; re-encoding back to latin-1 must restore
        the original bytes — verified by checking the ``%PDF-`` magic
        survives.
        """
        if not _PDF_FIXTURE.exists():
            pytest.skip(f"PDF fixture missing at {_PDF_FIXTURE}")

        stub = _make_stub_client(
            {"chunksIndexed": 7, "entryIds": ["abc"]},
        )

        result = ingest_path(_PDF_FIXTURE, client=stub)

        # One file, one HTTP call, no failures.
        assert stub.ingest_document.call_count == 1, stub.ingest_document.call_args_list
        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]
        assert result["skipped"] == [], result["skipped"]

        # The chunk count from the gateway round-trips to the result row.
        ingested = result["ingested"][0]
        assert ingested["chunks_indexed"] == 7, ingested
        assert ingested["entry_ids"] == ["abc"], ingested

        # The latin-1 round-trip preserves the PDF magic bytes.
        call_kwargs = stub.ingest_document.call_args.kwargs
        content = call_kwargs["content"]
        assert isinstance(content, str), type(content)
        # Re-encode and confirm we get the original PDF magic.
        reencoded = content.encode("latin-1")
        assert reencoded.startswith(b"%PDF-"), reencoded[:16]
        # And the source path threaded through unchanged.
        assert call_kwargs["source_path"].endswith("datasheet_excerpt.pdf"), call_kwargs[
            "source_path"
        ]

    def test_directory_walk_dispatches_each_pdf(self, tmp_path: Path) -> None:
        """A directory of two PDFs produces two ``ingest_document`` calls.

        Each call carries a latin-1 PDF payload — the dispatch path must
        not silently skip a PDF or merge them into one request.
        """
        if not _PDF_FIXTURE.exists():
            pytest.skip(f"PDF fixture missing at {_PDF_FIXTURE}")

        # Two copies under different names so the walker yields two paths.
        first = tmp_path / "datasheet_one.pdf"
        second = tmp_path / "datasheet_two.pdf"
        shutil.copy(_PDF_FIXTURE, first)
        shutil.copy(_PDF_FIXTURE, second)

        stub = _make_stub_client({"chunksIndexed": 4, "entryIds": ["x"]})

        result = ingest_path(tmp_path, client=stub)

        assert result["total"] == 2, result
        assert result["failed"] == [], result["failed"]
        assert result["skipped"] == [], result["skipped"]

        # Two HTTP calls, both with PDF magic in the latin-1 payload.
        assert stub.ingest_document.call_count == 2, stub.ingest_document.call_args_list
        seen_paths: list[str] = []
        for call in stub.ingest_document.call_args_list:
            kwargs = call.kwargs
            seen_paths.append(kwargs["source_path"])
            content = kwargs["content"]
            assert isinstance(content, str), type(content)
            assert content.encode("latin-1").startswith(b"%PDF-"), (
                f"call for {kwargs['source_path']} did not carry a PDF payload"
            )

        # Both PDFs were dispatched (set comparison — walker order is
        # alphabetical via ``sorted`` in ``_discover_files``).
        assert {Path(p).name for p in seen_paths} == {
            "datasheet_one.pdf",
            "datasheet_two.pdf",
        }

        # Each ingested row carries the gateway's chunk count.
        for row in result["ingested"]:
            assert row["chunks_indexed"] == 4, row


# ---------------------------------------------------------------------------
# Smoke check: dry-run JSON shape is parseable
# ---------------------------------------------------------------------------


class TestDryRunJsonShape:
    def test_dry_run_emits_parseable_json(
        self,
        tmp_path: Path,
        pdf_fixture_in_tmp: Path,  # noqa: ARG002 — copies PDF into tmp_path
    ) -> None:
        """``--format json`` on a dry-run produces a result envelope that
        downstream tooling (UAT runner, dashboards) can parse without
        regex-scraping stdout. Soft check that locks the contract.

        ``--format`` is a top-level arg so it must come before the
        subcommand name.
        """
        interpreter = str(_PYTHON) if _PYTHON.exists() else sys.executable
        proc = subprocess.run(
            [
                interpreter,
                "-m",
                "cli.forge_cli.main",
                "--format",
                "json",
                "ingest",
                str(tmp_path),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        envelope = json.loads(proc.stdout)
        assert envelope["total"] == 1, envelope
        assert envelope["dry_run"] is True, envelope
        assert envelope["ingested"][0]["path"].endswith("datasheet_excerpt.pdf"), envelope
