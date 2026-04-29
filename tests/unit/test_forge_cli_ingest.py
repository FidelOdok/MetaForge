"""Unit tests for ``forge ingest`` (MET-336).

Cover:

* File walking — single file, directory recursive vs flat, supported
  extensions, error on unsupported.
* ``knowledge_type`` inference from path segments and the override
  precedence (explicit --type > inferred > default).
* Dry-run skips the HTTP path entirely.
* Empty files are skipped, not failed.
* HTTP failures are recorded in ``failed`` without aborting the rest
  of the batch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli.forge_cli.ingest import (
    DEFAULT_KNOWLEDGE_TYPE,
    SUPPORTED_EXTENSIONS,
    _discover_files,
    _infer_knowledge_type,
    ingest_path,
)


class _StubClient:
    """Records ``ingest_document`` calls and responds with a stub."""

    def __init__(self, fail_paths: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_paths = fail_paths or set()
        self.next_chunks = 3

    def ingest_document(
        self,
        content: str,
        source_path: str,
        knowledge_type: str,
        source_work_product_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "content_length": len(content),
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "source_work_product_id": source_work_product_id,
                "metadata": metadata,
                "timeout": timeout,
            }
        )
        if source_path in self.fail_paths:
            raise RuntimeError(f"simulated HTTP failure for {source_path}")
        return {
            "entryIds": ["00000000-0000-0000-0000-000000000001"],
            "chunksIndexed": self.next_chunks,
            "sourcePath": source_path,
        }


# ---------------------------------------------------------------------------
# File walking + extension filtering
# ---------------------------------------------------------------------------


class TestDiscoverFiles:
    def test_single_supported_file(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.md"
        path.write_text("# Hello", encoding="utf-8")
        assert _discover_files(path, recursive=True) == [path]

    def test_single_unsupported_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "design.dwg"
        path.write_text("ignore me", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported"):
            _discover_files(path, recursive=True)

    def test_directory_recursive(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.md").write_text("b", encoding="utf-8")
        (sub / "c.txt").write_text("c", encoding="utf-8")
        (sub / "skip.png").write_bytes(b"\x89PNG")
        files = _discover_files(tmp_path, recursive=True)
        assert {p.name for p in files} == {"a.md", "b.md", "c.txt"}

    def test_directory_non_recursive(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.md").write_text("b", encoding="utf-8")
        files = _discover_files(tmp_path, recursive=False)
        assert [p.name for p in files] == ["a.md"]

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _discover_files(tmp_path / "ghost", recursive=True)

    def test_supported_extensions_set(self) -> None:
        assert {".md", ".pdf", ".txt", ".markdown"} <= SUPPORTED_EXTENSIONS

    def test_pdf_content_reaches_handler_unmangled(self, tmp_path: Path) -> None:
        """PDF bytes survive the walker → handler hop intact (MET-399).

        Catches a regression where a future refactor of
        ``_read_file_content`` could swallow PDF content (UTF-8 decode
        on bytes that aren't UTF-8) — the old code path returned an
        empty string for binary files.
        """
        pdf = tmp_path / "datasheet.pdf"
        # Minimal valid-enough PDF header + a payload byte that's not
        # valid UTF-8 (0xff). The walker must surface every byte.
        pdf.write_bytes(b"%PDF-1.4\n%\xff binary-payload\n%%EOF\n")

        stub = _StubClient()
        result = ingest_path(pdf, client=stub)
        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]
        assert len(stub.calls) == 1
        # Roundtrip latin-1 to recover the original bytes.
        recovered = stub.calls[0]["content_length"]
        # 35 bytes in the original PDF should arrive at the handler.
        assert recovered == len(pdf.read_bytes()), (
            f"PDF byte count drifted: handler saw {recovered}, "
            f"file is {len(pdf.read_bytes())} bytes"
        )


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------


class TestInferKnowledgeType:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("docs/decisions/material-selection.md", "design_decision"),
            ("docs/adr/0042-bracket.md", "design_decision"),
            ("docs/constraints/load-cases.md", "constraint"),
            ("docs/failures/insert-pullout.md", "failure"),
            ("docs/components/STM32.md", "component"),
            ("docs/datasheets/op-amp.pdf", "component"),
            ("README.md", DEFAULT_KNOWLEDGE_TYPE),
            ("notes/random.md", DEFAULT_KNOWLEDGE_TYPE),
        ],
    )
    def test_path_inference(self, path: str, expected: str) -> None:
        assert _infer_knowledge_type(Path(path)) == expected


# ---------------------------------------------------------------------------
# ingest_path orchestration
# ---------------------------------------------------------------------------


class TestIngestPath:
    def test_single_file_calls_client_and_records_chunks(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions" / "bracket.md"
        path.parent.mkdir()
        path.write_text("# Decision\nContent goes here.", encoding="utf-8")
        client = _StubClient()

        result = ingest_path(path, client=client)  # type: ignore[arg-type]
        assert result["total"] == 1
        assert len(result["ingested"]) == 1
        assert result["failed"] == []
        assert result["skipped"] == []
        # Inference picks up "decisions" → design_decision
        assert client.calls[0]["knowledge_type"] == "design_decision"

    def test_directory_walk_ingests_each_file(self, tmp_path: Path) -> None:
        (tmp_path / "constraints").mkdir()
        (tmp_path / "constraints" / "a.md").write_text("c1", encoding="utf-8")
        (tmp_path / "decisions").mkdir()
        (tmp_path / "decisions" / "d.md").write_text("d1", encoding="utf-8")
        (tmp_path / "skip.png").write_bytes(b"\x89PNG")  # ignored
        client = _StubClient()

        result = ingest_path(tmp_path, client=client)  # type: ignore[arg-type]
        assert result["total"] == 2
        assert {c["knowledge_type"] for c in client.calls} == {"constraint", "design_decision"}

    def test_dry_run_skips_http(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("anything", encoding="utf-8")
        client = _StubClient()
        result = ingest_path(tmp_path, client=client, dry_run=True)  # type: ignore[arg-type]
        assert result["dry_run"] is True
        assert client.calls == []
        assert all(item["dry_run"] for item in result["ingested"])

    def test_explicit_type_overrides_inference(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions" / "x.md"
        path.parent.mkdir()
        path.write_text("body", encoding="utf-8")
        client = _StubClient()
        ingest_path(path, client=client, knowledge_type="session")  # type: ignore[arg-type]
        assert client.calls[0]["knowledge_type"] == "session"

    def test_empty_file_is_skipped_not_failed(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_text("   \n   ", encoding="utf-8")
        client = _StubClient()
        result = ingest_path(empty, client=client)  # type: ignore[arg-type]
        assert client.calls == []
        assert result["skipped"] == [{"path": str(empty.resolve()), "reason": "empty file"}]

    def test_http_failure_recorded_per_file(self, tmp_path: Path) -> None:
        good = tmp_path / "good.md"
        bad = tmp_path / "bad.md"
        good.write_text("a", encoding="utf-8")
        bad.write_text("b", encoding="utf-8")
        client = _StubClient(fail_paths={str(bad.resolve())})
        result = ingest_path(tmp_path, client=client)  # type: ignore[arg-type]
        assert len(result["ingested"]) == 1
        assert len(result["failed"]) == 1
        assert "simulated HTTP failure" in result["failed"][0]["error"]

    def test_metadata_and_workproduct_pass_through(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.md"
        path.write_text("body", encoding="utf-8")
        client = _StubClient()
        wp = "11111111-1111-1111-1111-111111111111"
        ingest_path(  # type: ignore[arg-type]
            path,
            client=client,
            knowledge_type="component",
            source_work_product_id=wp,
            metadata={"reviewer": "ee"},
        )
        call = client.calls[0]
        assert call["source_work_product_id"] == wp
        assert call["metadata"] == {"reviewer": "ee"}
