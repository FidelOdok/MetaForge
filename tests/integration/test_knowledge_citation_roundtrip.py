"""Integration test for citation round-trip through the knowledge chain (MET-389).

Asserts that ``LightRAGKnowledgeService`` preserves the citation fields
(``source_path``, ``heading``, ``chunk_index``, ``total_chunks``) and the
caller-supplied ``metadata`` byte-for-byte through the full lifecycle:
ingest -> chunk -> store -> retrieve -> return.

The citation chain crosses several boundaries:

* :func:`digital_twin.knowledge.lightrag_service._chunk_by_heading`
  splits markdown by H1..H6 boundaries and stamps each chunk with its
  parent heading.
* :func:`digital_twin.knowledge.lightrag_service._encode_meta` packs the
  citation fields into the ``file_path`` JSON blob LightRAG echoes back.
* :meth:`LightRAGKnowledgeService._chunk_to_hit` is the inverse: it
  decodes the blob and projects it onto a :class:`SearchHit`.

If any link in that chain drops a field, downstream UI / RAG prompts
lose the ability to render an attributable answer — that's the bug
this suite is built to detect.

Mirrors the L1-A1 / L1-F3 skip-clean Postgres pattern:
``pytest.importorskip("asyncpg")`` plus a 2 s connectivity probe so
the suite SKIPs cleanly on CI (where ``[dev]`` doesn't ship the
integration backend) instead of either ERROR'ing or sitting on a
6-minute LightRAG retry loop.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

# Skip the whole module when asyncpg isn't installed (CI ``[dev]``
# environment) — the import below would otherwise fail at collection.
pytest.importorskip("asyncpg")

from digital_twin.knowledge import create_knowledge_service  # noqa: E402
from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService  # noqa: E402
from digital_twin.knowledge.types import KnowledgeType  # noqa: E402

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"

# Stable UUID for any project-scoped assertions so failures are easier
# to read in logs.
PROJECT_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


async def _pg_reachable(dsn: str) -> bool:
    """Cheap connectivity probe so we SKIP cleanly when Postgres isn't up.

    Mirrors ``test_knowledge_project_isolation.py`` — 2 s timeout, fail
    closed. Avoids sitting on LightRAG's ~6 minute internal retry loop
    on a stone-dead container.
    """
    import asyncio

    import asyncpg  # type: ignore[import-untyped]

    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
    except (OSError, TimeoutError, asyncpg.PostgresError):
        return False
    except Exception:
        return False
    try:
        await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    finally:
        await conn.close()
    return True


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[LightRAGKnowledgeService]:
    """One-per-test LightRAG service, namespaced to avoid collisions.

    Skips cleanly if Postgres+pgvector isn't reachable (per the L1-A1
    "skip cleanly when integration backend unavailable" requirement).
    """
    dsn = _dsn()
    if not await _pg_reachable(dsn):
        pytest.skip(f"Postgres+pgvector not reachable at {dsn} — integration backend unavailable")

    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-{suffix}"),
        postgres_dsn=dsn,
        namespace_prefix=f"lightrag_cite_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc  # type: ignore[misc]
    finally:
        await svc.close()  # type: ignore[attr-defined]


class TestCitationRoundTrip:
    """MET-389: citation fields survive ingest -> chunk -> store -> retrieve."""

    async def test_h2_heading_round_trips_through_search(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """An H2 heading from section 2 round-trips onto the hit's ``heading``.

        The fixture has three H2 sections, each with two paragraphs of
        distinct content. We search for a unique-token phrase that
        lives in section 2's second paragraph — the hit must cite
        section 2's H2, carry the same source_path we ingested with,
        and expose a chunk_index / total_chunks consistent with the
        chunker's output.
        """
        sentinel = uuid.uuid4().hex[:8]
        unique_phrase = f"citation-probe-section-2-paragraph-2-{sentinel}"
        markdown = f"""# Datasheet Excerpt

## Power Supply

The device accepts a wide input range from 2.5 V to 5.5 V on the VIN pin.
Internal LDO drops this to a stable 1.2 V digital core rail.

The boost converter handles transients up to 3 A peak with a 1 MHz
switching frequency, suitable for portable battery-backed designs.

## Operating Conditions

Industrial grade parts are rated for ambient temperatures from -40 C
to +85 C with a junction-to-ambient thermal resistance of 38 C/W.

The {unique_phrase} marker lives in this paragraph: storage temperature
is -65 C to +150 C, JEDEC moisture sensitivity level MSL3 per J-STD-020.

## Electrical Characteristics

Quiescent current is 80 microamps in active mode, dropping to 1.2 microamps
in shutdown. Power supply rejection ratio is 75 dB at 1 kHz.

Output voltage accuracy is 1 percent across the full temperature and
load range, sufficient for ratiometric ADC reference applications.
"""
        source_path = f"cite://test/{sentinel}.md"

        ingest_result = await service.ingest(
            content=markdown,
            source_path=source_path,
            knowledge_type=KnowledgeType.COMPONENT,
            project_id=PROJECT_ID,
        )
        assert ingest_result.chunks_indexed >= 1, ingest_result
        assert ingest_result.source_path == source_path

        # Allow LightRAG a moment to flush async writes.
        time.sleep(0.5)

        hits = await service.search(unique_phrase, top_k=5, project_id=PROJECT_ID)
        assert len(hits) >= 1, "expected at least one hit for the unique phrase"

        top = hits[0]
        # Citation fields must round-trip exactly.
        assert top.source_path == source_path, (
            f"source_path drift: ingested {source_path!r}, got {top.source_path!r}"
        )
        # The H2 for section 2 is "Operating Conditions" — that's where
        # the unique phrase lives. The chunker stamps each chunk with
        # its own heading (no H1 prefix), so the heading should be
        # exactly "Operating Conditions" (or contain it, if a future
        # chunker change introduces a path).
        assert top.heading is not None, f"heading missing from top hit: {top!r}"
        assert "Operating Conditions" in top.heading, (
            f"expected H2 'Operating Conditions' in heading, got {top.heading!r}"
        )
        # chunk_index must be a non-negative int and consistent with
        # total_chunks (which must match the chunker's output count).
        assert isinstance(top.chunk_index, int), top.chunk_index
        assert top.chunk_index >= 0, top.chunk_index
        assert isinstance(top.total_chunks, int), top.total_chunks
        assert top.total_chunks == ingest_result.chunks_indexed, (
            f"total_chunks ({top.total_chunks}) does not match the "
            f"chunker's output count ({ingest_result.chunks_indexed})"
        )
        assert top.chunk_index < top.total_chunks, (
            f"chunk_index ({top.chunk_index}) must be < total_chunks ({top.total_chunks})"
        )

    async def test_h1_h2_heading_path_concatenation(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """H1 -> H2 nested structure: heading captures the H2 in scope.

        The current chunker (``_chunk_by_heading``) treats every
        heading line as a chunk boundary and stamps each chunk with the
        heading text from its own boundary line — it does NOT
        concatenate the parent H1 into the H2 chunk's heading. This
        test pins that behaviour: the returned heading is the H2
        alone. If a future change introduces a ``H1 / H2`` path
        string, swap the assertion below to allow either form.
        """
        sentinel = uuid.uuid4().hex[:8]
        unique_phrase = f"nested-heading-probe-{sentinel}"
        markdown = f"""# STM32H743 Reference Manual

Top-level intro paragraph that lives under the H1 only.

## Memory Map

The device exposes 1 MB of flash and 564 KB of SRAM organised across
multiple banks for code/data separation.

## Peripheral Catalog

The {unique_phrase} marker lives under H2 #2: USART, SPI, I2C, CAN-FD,
SDMMC, and a 16-bit parallel camera interface are all available on
LQFP100 packages.
"""
        source_path = f"cite://nested/{sentinel}.md"

        await service.ingest(
            content=markdown,
            source_path=source_path,
            knowledge_type=KnowledgeType.COMPONENT,
            project_id=PROJECT_ID,
        )
        time.sleep(0.5)

        hits = await service.search(unique_phrase, top_k=5, project_id=PROJECT_ID)
        assert len(hits) >= 1, "expected hit for the H2 #2 phrase"
        top = hits[0]

        assert top.source_path == source_path
        assert top.heading is not None, f"heading missing: {top!r}"
        # Pinned behaviour: the chunker stamps the H2 alone, not a
        # concatenated "H1 / H2" path. Accept either form so a future
        # chunker that DOES build a path string will not break the
        # contract — but at minimum the H2 text must be present.
        assert "Peripheral Catalog" in top.heading, (
            f"expected H2 'Peripheral Catalog' in heading (alone or as "
            f"part of an H1/H2 path), got {top.heading!r}"
        )

    async def test_chunk_index_matches_textual_position(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """A 3-paragraph fixture: chunk_index for the last paragraph is in-range.

        Without H2 boundaries the heading-aware chunker produces a
        single chunk for the whole body (or hard-splits only when
        ``max_chunk_chars`` is exceeded — which our tiny fixture is
        well below). The exact chunk_index for the search hit
        therefore depends on chunker boundary choices; we only assert
        the soft contract:

        * ``chunk_index >= 0``
        * ``chunk_index < total_chunks``
        """
        sentinel = uuid.uuid4().hex[:8]
        unique_phrase = f"third-paragraph-probe-{sentinel}"
        markdown = f"""Paragraph one introduces the part: a tiny dual-channel
op-amp in a SOT-23-5 package, ideal for portable instrumentation.

Paragraph two covers the supply rails: 1.8 V to 5.5 V single-supply
operation with rail-to-rail input and output swing.

Paragraph three holds the {unique_phrase} marker: input bias current
is 1 picoamp typical, suitable for high-impedance sensor front-ends.
"""
        source_path = f"cite://prose/{sentinel}.md"

        ingest_result = await service.ingest(
            content=markdown,
            source_path=source_path,
            knowledge_type=KnowledgeType.COMPONENT,
            project_id=PROJECT_ID,
        )
        assert ingest_result.chunks_indexed >= 1
        time.sleep(0.5)

        hits = await service.search(unique_phrase, top_k=5, project_id=PROJECT_ID)
        assert len(hits) >= 1, "expected at least one hit for the prose phrase"
        top = hits[0]

        assert top.source_path == source_path
        assert isinstance(top.chunk_index, int), top.chunk_index
        assert top.chunk_index >= 0, top.chunk_index
        assert isinstance(top.total_chunks, int), top.total_chunks
        assert top.chunk_index < top.total_chunks, (
            f"chunk_index ({top.chunk_index}) must be < total_chunks ({top.total_chunks})"
        )

    async def test_metadata_round_trip_alongside_citation(
        self, service: LightRAGKnowledgeService
    ) -> None:
        """User-supplied metadata round-trips alongside the citation fields.

        Engineers tag ingested docs with vendor / doc_id / project
        identifiers; those keys must reach the search hit's
        ``metadata`` dict so RAG prompts and dashboards can render
        provenance without a second lookup.
        """
        sentinel = uuid.uuid4().hex[:8]
        unique_phrase = f"metadata-roundtrip-probe-{sentinel}"
        markdown = f"""# Vendor Note

## Compliance Statement

This component is RoHS and REACH compliant per the {unique_phrase}
attestation issued 2026-Q1 by TestCo.
"""
        source_path = f"cite://meta/{sentinel}.md"

        await service.ingest(
            content=markdown,
            source_path=source_path,
            knowledge_type=KnowledgeType.COMPONENT,
            metadata={"vendor": "TestCo", "doc_id": "doc-42"},
            project_id=PROJECT_ID,
        )
        time.sleep(0.5)

        hits = await service.search(unique_phrase, top_k=5, project_id=PROJECT_ID)
        assert len(hits) >= 1, "expected hit for the metadata-tagged phrase"
        top = hits[0]

        # Citation fields are present.
        assert top.source_path == source_path
        assert top.heading is not None and "Compliance Statement" in top.heading, (
            f"expected H2 'Compliance Statement' in heading, got {top.heading!r}"
        )
        assert isinstance(top.chunk_index, int) and top.chunk_index >= 0
        assert isinstance(top.total_chunks, int) and top.total_chunks >= 1

        # User metadata round-trips on the same hit.
        assert top.metadata.get("vendor") == "TestCo", (
            f"vendor metadata dropped on round-trip: {top.metadata!r}"
        )
        assert top.metadata.get("doc_id") == "doc-42", (
            f"doc_id metadata dropped on round-trip: {top.metadata!r}"
        )
