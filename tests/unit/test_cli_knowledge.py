"""Unit tests for ``forge knowledge`` CLI subcommands (MET-443)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.forge_cli.knowledge import (
    _format_summary,
    _superseded_revision,
    register_subparser,
)


def _pdfplumber_available() -> bool:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "knowledge" / "datasheet_excerpt.pdf"


# ---------------------------------------------------------------------------
# Pure helpers (no PDF / Twin needed)
# ---------------------------------------------------------------------------


class _StubDatasheet:
    """Minimal Datasheet shape for helper tests."""

    def __init__(self, id_, mpn, manufacturer, revision, page_count, file_hash, ingested_at):
        self.id = id_
        self.mpn = mpn
        self.manufacturer = manufacturer
        self.revision = revision
        self.page_count = page_count
        self.file_hash = file_hash
        self.ingested_at = ingested_at


class TestSupersededRevision:
    def test_returns_none_for_single_history(self) -> None:
        from datetime import UTC, datetime

        ds = _StubDatasheet(
            "id1", "MPN", "X", "rev1", 1, "h" * 20, datetime.now(UTC)
        )
        assert _superseded_revision(ds, [ds]) is None

    def test_returns_prior_revision_label(self) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        older = _StubDatasheet("id1", "MPN", "X", "rev1", 1, "a" * 20, now - timedelta(days=1))
        newer = _StubDatasheet("id2", "MPN", "X", "rev2", 1, "b" * 20, now)
        assert _superseded_revision(newer, [older, newer]) == "rev1"

    def test_empty_history_returns_none(self) -> None:
        from datetime import UTC, datetime

        ds = _StubDatasheet("id1", "MPN", "X", "rev1", 1, "h" * 20, datetime.now(UTC))
        assert _superseded_revision(ds, []) is None


class TestFormatSummary:
    def test_fresh_ingest_shape(self) -> None:
        from datetime import UTC, datetime

        ds = _StubDatasheet(
            "id1", "ESP32-WROOM-32E", "Espressif", "rev3", 42, "abcdef0123456789", datetime.now(UTC)
        )
        out = _format_summary(ds, idempotent=False, superseded_revision=None)
        assert out["mpn"] == "ESP32-WROOM-32E"
        assert out["manufacturer"] == "Espressif"
        assert out["revision"] == "rev3"
        assert out["page_count"] == 42
        assert out["status"] == "ingested"
        assert out["supersedes"] is None
        # File hash is truncated for display.
        assert out["file_hash"].endswith("…")
        assert len(out["file_hash"]) <= 14

    def test_idempotent_shape(self) -> None:
        from datetime import UTC, datetime

        ds = _StubDatasheet(
            "id1", "MPN", "X", "rev1", 1, "x" * 20, datetime.now(UTC)
        )
        out = _format_summary(ds, idempotent=True, superseded_revision=None)
        assert out["status"] == "already-ingested"

    def test_supersedes_shape(self) -> None:
        from datetime import UTC, datetime

        ds = _StubDatasheet(
            "id2", "MPN", "X", "rev2", 1, "y" * 20, datetime.now(UTC)
        )
        out = _format_summary(ds, idempotent=False, superseded_revision="rev1")
        assert out["supersedes"] == "rev1"


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


class TestSubparserRegistration:
    def test_knowledge_ingest_datasheet_parses(self) -> None:
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        register_subparser(subparsers)

        args = parser.parse_args(
            [
                "knowledge",
                "ingest-datasheet",
                "some.pdf",
                "--mpn",
                "ESP32-WROOM-32E",
                "--manufacturer",
                "Espressif",
                "--revision",
                "rev3",
            ]
        )
        assert args.command == "knowledge"
        assert args.knowledge_command == "ingest-datasheet"
        assert args.path == "some.pdf"
        assert args.mpn == "ESP32-WROOM-32E"
        assert args.manufacturer == "Espressif"
        assert args.revision == "rev3"
        assert args.source_url is None


# ---------------------------------------------------------------------------
# Handler dispatch (file-not-found exits cleanly)
# ---------------------------------------------------------------------------


class TestHandlerExits:
    def test_missing_file_exits_1(self) -> None:
        from cli.forge_cli.knowledge import _run_ingest_datasheet

        class _Args:
            path = "/does/not/exist.pdf"
            mpn = "X"
            manufacturer = "Y"
            revision = "rev1"
            source_url = None

        with pytest.raises(SystemExit) as exc:
            _run_ingest_datasheet(_Args())
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# End-to-end (only when pdfplumber is installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _pdfplumber_available() or not _FIXTURE.exists(),
    reason="pdfplumber missing or fixture absent",
)
class TestE2eIngest:
    def test_first_ingest_against_fixture(self, tmp_path: Path, monkeypatch) -> None:
        """Run the full flow against an in-memory Twin."""
        # Pin to in-memory backend so the test doesn't need Neo4j.
        monkeypatch.delenv("NEO4J_URI", raising=False)
        monkeypatch.delenv("METAFORGE_GRAPH_BACKEND", raising=False)

        target = tmp_path / "esp32.pdf"
        target.write_bytes(_FIXTURE.read_bytes())

        import argparse

        from cli.forge_cli.knowledge import _run_ingest_datasheet

        args = argparse.Namespace(
            path=str(target),
            mpn="ESP32-WROOM-32E",
            manufacturer="Espressif",
            revision="rev1",
            source_url=None,
        )

        result = _run_ingest_datasheet(args)
        assert result is not None
        assert result["status"] == "ingested"
        assert result["mpn"] == "ESP32-WROOM-32E"
        assert result["supersedes"] is None
