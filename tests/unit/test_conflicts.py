"""Unit tests for conflict detection across context sources (MET-322).

Cover:

* Field extraction (metadata > content regex; case + whitespace
  normalisation; stop at first newline / pipe).
* `ConflictDetector.detect` groups by `mpn` (default) and emits one
  `Conflict` per disagreeing field per pair, deduplicated.
* Severity comes from the per-field map; defaults to `INFO`.
* `ContextAssembler.assemble` populates `response.conflicts` and
  flips `has_blocking_conflict` when an MPN mismatches.
* Empty / single-fragment / non-grouped fragments produce no conflicts.
"""

from __future__ import annotations

from typing import Any

import pytest

from digital_twin.context import (
    Conflict,
    ConflictDetector,
    ConflictSeverity,
    ContextAssembler,
    ContextAssemblyRequest,
    ContextFragment,
    ContextScope,
    ContextSourceKind,
)
from digital_twin.context.models import estimate_tokens
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from twin_core.api import InMemoryTwinAPI


def _frag(
    source_id: str,
    *,
    metadata: dict[str, Any] | None = None,
    content: str = "",
) -> ContextFragment:
    return ContextFragment(
        content=content,
        source_kind=ContextSourceKind.KNOWLEDGE_HIT,
        source_id=source_id,
        token_count=estimate_tokens(content or " "),
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Detector — direct
# ---------------------------------------------------------------------------


class TestExtractFields:
    def test_metadata_takes_precedence_over_content(self) -> None:
        detector = ConflictDetector()
        frag = _frag(
            "schematic.md",
            metadata={"mpn": "STM32F407", "voltage": "3.3V"},
            content="voltage: 5V\n",  # should lose to metadata
        )
        out = detector._extract_fields(frag)  # noqa: SLF001
        assert out["voltage"] == "3.3V"
        assert out["mpn"] == "STM32F407"

    def test_content_regex_picks_up_kv_lines(self) -> None:
        detector = ConflictDetector()
        # Colon-separated key-value lines — common in datasheet
        # excerpts, ADRs, and the YAML-frontmatter side of markdown.
        # Markdown-table parsing is intentionally a follow-up; the
        # detector's authoritative source is ``metadata`` (set by
        # ingestion), with content regex as a best-effort fallback.
        frag = _frag(
            "datasheet.md",
            content=("MPN: STM32F407\nVoltage: 3.3V\nPackage: LQFP-100\n"),
        )
        out = detector._extract_fields(frag)  # noqa: SLF001
        assert out["mpn"] == "STM32F407"
        assert out["voltage"] == "3.3V"
        assert out["package"] == "LQFP-100"

    def test_unknown_keys_ignored(self) -> None:
        detector = ConflictDetector()
        frag = _frag("doc.md", content="random_field: 42\n")
        out = detector._extract_fields(frag)  # noqa: SLF001
        assert out == {}


class TestDetect:
    def test_no_conflicts_when_only_one_fragment(self) -> None:
        detector = ConflictDetector()
        frags = [_frag("a", metadata={"mpn": "X1", "voltage": "5V"})]
        assert detector.detect(frags) == []

    def test_blocking_severity_for_mpn_mismatch(self) -> None:
        # Two fragments share a grouping (both reference MPN "X1")
        # but disagree on a tracked field.
        # Group by `voltage` instead — set as detector's grouping_field.
        detector = ConflictDetector(grouping_field="voltage")
        frags = [
            _frag("schematic.md", metadata={"voltage": "5V", "mpn": "X1"}),
            _frag("bom.md", metadata={"voltage": "5V", "mpn": "X2"}),
        ]
        conflicts = detector.detect(frags)
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.field == "mpn"
        assert c.severity == ConflictSeverity.BLOCKING
        assert {c.value_a, c.value_b} == {"X1", "X2"}
        assert c.grouping_key == "5V"

    def test_warning_severity_for_voltage_mismatch_within_mpn(self) -> None:
        detector = ConflictDetector()
        frags = [
            _frag("schematic.md", metadata={"mpn": "X1", "voltage": "5V"}),
            _frag("bom.md", metadata={"mpn": "X1", "voltage": "3.3V"}),
        ]
        conflicts = detector.detect(frags)
        assert len(conflicts) == 1
        assert conflicts[0].field == "voltage"
        assert conflicts[0].severity == ConflictSeverity.WARNING
        assert conflicts[0].grouping_key == "X1"

    def test_info_severity_for_package_mismatch(self) -> None:
        detector = ConflictDetector()
        frags = [
            _frag("a.md", metadata={"mpn": "X1", "package": "SOIC-8"}),
            _frag("b.md", metadata={"mpn": "X1", "package": "SO-8"}),
        ]
        conflicts = detector.detect(frags)
        assert conflicts[0].severity == ConflictSeverity.INFO

    def test_no_conflict_when_values_normalise_equal(self) -> None:
        detector = ConflictDetector()
        frags = [
            _frag("a.md", metadata={"mpn": "X1", "voltage": "5V"}),
            _frag("b.md", metadata={"mpn": "X1", "voltage": "  5v  "}),
        ]
        assert detector.detect(frags) == []

    def test_three_sources_emit_three_pairwise_conflicts(self) -> None:
        # MET-322 spec is "one Conflict per pair per field" — three
        # disagreeing fragments share MPN, give three voltage pairs.
        detector = ConflictDetector()
        frags = [
            _frag("a.md", metadata={"mpn": "X1", "voltage": "1.8V"}),
            _frag("b.md", metadata={"mpn": "X1", "voltage": "3.3V"}),
            _frag("c.md", metadata={"mpn": "X1", "voltage": "5V"}),
        ]
        conflicts = detector.detect(frags)
        assert len(conflicts) == 3
        assert all(c.field == "voltage" for c in conflicts)

    def test_dedup_does_not_emit_reverse_pair(self) -> None:
        detector = ConflictDetector()
        frags = [
            _frag("a.md", metadata={"mpn": "X1", "voltage": "1.8V"}),
            _frag("b.md", metadata={"mpn": "X1", "voltage": "3.3V"}),
        ]
        conflicts = detector.detect(frags)
        # No "(b, a)" mirror.
        pairs = {(c.source_a, c.source_b) for c in conflicts}
        assert len(pairs) == 1

    def test_pydantic_round_trip(self) -> None:
        c = Conflict(
            field="mpn",
            value_a="X1",
            value_b="X2",
            source_a="a",
            source_b="b",
            severity=ConflictSeverity.BLOCKING,
        )
        assert Conflict.model_validate_json(c.model_dump_json()).field == "mpn"


# ---------------------------------------------------------------------------
# Assembler integration
# ---------------------------------------------------------------------------


class _StubService:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    async def ingest(self, *args: Any, **kwargs: Any) -> IngestResult:  # pragma: no cover
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        return list(self._hits)

    async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


def _hit(source_path: str, content: str, metadata: dict[str, Any]) -> SearchHit:
    return SearchHit(
        content=content,
        similarity_score=0.9,
        source_path=source_path,
        heading="H",
        chunk_index=0,
        total_chunks=1,
        metadata=metadata,
        knowledge_type=KnowledgeType.DESIGN_DECISION,
        source_work_product_id=None,
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class TestAssemblerConflicts:
    async def test_blocking_mpn_conflict_flips_response_flag(self, twin: InMemoryTwinAPI) -> None:
        # Schematic and BOM disagree on MPN for the same voltage rail.
        hits = [
            _hit(
                "schematic.md",
                "MPN: STM32F407\nvoltage: 3.3V\n",
                {"mpn": "STM32F407", "voltage": "3.3V"},
            ),
            _hit(
                "bom.md",
                "MPN: STM32F411\nvoltage: 3.3V\n",
                {"mpn": "STM32F411", "voltage": "3.3V"},
            ),
        ]
        # Use voltage as grouping key so the two share a pivot.
        detector = ConflictDetector(grouping_field="voltage")
        service = _StubService(hits)
        assembler = ContextAssembler(
            twin=twin,
            knowledge_service=service,  # type: ignore[arg-type]
            conflict_detector=detector,
        )
        response = await assembler.assemble(
            ContextAssemblyRequest(
                agent_id="ee_agent",
                query="?",
                scope=[ContextScope.KNOWLEDGE],
            )
        )
        assert response.conflicts, response.metadata
        assert response.has_blocking_conflict is True
        assert any(c.field == "mpn" for c in response.conflicts)

    async def test_no_conflicts_with_consistent_sources(self, twin: InMemoryTwinAPI) -> None:
        hits = [
            _hit("a.md", "MPN: X1", {"mpn": "X1", "voltage": "5V"}),
            _hit("b.md", "MPN: X1", {"mpn": "X1", "voltage": "5V"}),
        ]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        response = await assembler.assemble(
            ContextAssemblyRequest(
                agent_id="ee_agent",
                query="?",
                scope=[ContextScope.KNOWLEDGE],
            )
        )
        assert response.conflicts == []
        assert response.has_blocking_conflict is False

    async def test_conflict_count_in_metadata(self, twin: InMemoryTwinAPI) -> None:
        hits = [
            _hit("a.md", "x", {"mpn": "X1", "voltage": "1.8V"}),
            _hit("b.md", "x", {"mpn": "X1", "voltage": "3.3V"}),
        ]
        service = _StubService(hits)
        assembler = ContextAssembler(twin=twin, knowledge_service=service)  # type: ignore[arg-type]
        response = await assembler.assemble(
            ContextAssemblyRequest(
                agent_id="ee_agent",
                query="?",
                scope=[ContextScope.KNOWLEDGE],
            )
        )
        assert response.metadata["conflict_count"] == len(response.conflicts)
        assert response.metadata["conflict_count"] >= 1
