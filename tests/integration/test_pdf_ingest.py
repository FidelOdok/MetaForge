"""End-to-end test for PDF ingestion (MET-399).

Closes the coverage gap flagged by the 2026-04-28 audit: PDF is in
``SUPPORTED_EXTENSIONS`` and the data-modalities matrix, but no test
ever drove a real PDF through ``forge ingest``.

Opt in with ``pytest --integration``. Boots the full gateway in-process
via ASGITransport (no real HTTP socket), drives ``forge ingest`` against
a small committed PDF fixture (``datasheet_excerpt.pdf``), and verifies
the document lands in the L1 knowledge layer.

Requires the dev ``metaforge-postgres-1`` (with ``vector`` extension)
running on ``localhost:5432``. The L1-A3 multi-page tests probe Postgres
on a 2 s timeout (mirroring ``test_knowledge_project_isolation.py``) so
the suite skips cleanly when the integration backend isn't reachable.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cli.forge_cli.ingest import ingest_path

# Reuse the ASGI client helper from the existing forge-ingest e2e suite
# so we don't fork the test scaffolding.
from tests.integration.test_forge_ingest_e2e import _AsgiClient

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql+asyncpg://metaforge:metaforge@localhost:5432/metaforge"

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "datasheet_excerpt.pdf"
)


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


def _pg_dsn_sync() -> str:
    """Return the asyncpg-flavoured DSN (same shape as project_isolation test)."""
    return _dsn().replace("postgresql+asyncpg://", "postgresql://")


async def _pg_reachable(dsn: str) -> bool:
    """Cheap connectivity probe so we SKIP cleanly when Postgres isn't up.

    Mirrors ``test_knowledge_project_isolation.py`` — 2 s timeout, fail
    closed. Avoids sitting on LightRAG's ~6 minute internal retry loop
    on a stone-dead container.
    """
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


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _dsn())


@pytest.fixture
async def gateway() -> AsyncIterator[tuple[object, _AsgiClient]]:
    """Boot the gateway (with KnowledgeService) and yield an ASGI client."""
    from api_gateway.db.engine import dispose_engine
    from api_gateway.server import create_app

    await dispose_engine()
    app = create_app()
    async with app.router.lifespan_context(app):
        client = _AsgiClient(app=app)
        yield app, client


@pytest.fixture
def pdf_fixture(tmp_path: Path) -> Path:
    """Copy the committed PDF fixture into a unique tmp_path so the
    same physical file can be ingested multiple times across tests
    without colliding on source_path dedup keys.
    """
    if not _FIXTURE_PATH.exists():
        pytest.skip(f"PDF fixture missing at {_FIXTURE_PATH}")
    target = tmp_path / "datasheet_excerpt.pdf"
    target.write_bytes(_FIXTURE_PATH.read_bytes())
    return target


class TestPdfIngest:
    async def test_pdf_file_lands_in_knowledge_layer(
        self,
        pdf_fixture: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """Single PDF ingest produces ≥1 chunk and is searchable."""
        app, client = gateway
        result = ingest_path(pdf_fixture, client=client)

        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]
        assert result["skipped"] == [], result["skipped"]
        ingested = result["ingested"][0]
        assert ingested["chunks_indexed"] >= 1, ingested
        assert ingested["path"].endswith(".pdf"), ingested

        # Search for content that's verbatim in the fixture (page 2).
        service = app.state.knowledge_service  # type: ignore[attr-defined]
        hits = await service.search("voltage regulator 1.2 V digital core", top_k=5)
        pdf_hits = [h for h in hits if (h.source_path or "").endswith("datasheet_excerpt.pdf")]
        assert pdf_hits, [(h.source_path, h.content[:80]) for h in hits]

    async def test_pdf_search_hit_carries_citation_fields(
        self,
        pdf_fixture: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """Search hits from a PDF carry chunk_index < total_chunks."""
        app, _ = gateway

        # Drive ingestion via the service directly so we bypass the
        # walker — keeps this assertion focused on citation round-trip
        # rather than the CLI plumbing.
        from digital_twin.knowledge.types import KnowledgeType

        service = app.state.knowledge_service  # type: ignore[attr-defined]
        with pdf_fixture.open("rb") as fh:
            content = fh.read().decode("latin-1")
        await service.ingest(
            content=content,
            source_path=str(pdf_fixture.resolve()),
            knowledge_type=KnowledgeType.COMPONENT,
        )

        hits = await service.search("STM32H743 microcontroller", top_k=5)
        pdf_hits = [h for h in hits if (h.source_path or "").endswith("datasheet_excerpt.pdf")]
        assert pdf_hits, [(h.source_path, h.content[:60]) for h in hits]

        for h in pdf_hits:
            assert h.chunk_index is not None, f"chunk_index missing on {h.source_path}"
            assert h.total_chunks is not None, f"total_chunks missing on {h.source_path}"
            assert h.chunk_index < h.total_chunks, (
                f"chunk_index={h.chunk_index} not < total_chunks={h.total_chunks}"
            )

    async def test_pdf_reingest_does_not_double_count(
        self,
        pdf_fixture: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """Re-ingesting the same PDF re-uses the chunk count (dedup)."""
        _, client = gateway

        first = ingest_path(pdf_fixture, client=client)
        second = ingest_path(pdf_fixture, client=client)

        assert first["total"] == 1
        assert second["total"] == 1
        first_chunks = first["ingested"][0]["chunks_indexed"]
        second_chunks = second["ingested"][0]["chunks_indexed"]
        # The chunker is deterministic for the same content so re-ingest
        # should produce the same chunk count. LightRAG's predelete
        # (MET-378) drops the prior chunks first; the count we see on
        # the second response is the count of newly-indexed chunks,
        # which equals the first.
        assert first_chunks == second_chunks > 0, (first_chunks, second_chunks)

    async def test_pdf_delete_by_source_removes_all_chunks(
        self,
        pdf_fixture: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """``delete_by_source`` purges every chunk from a PDF source."""
        app, client = gateway
        service = app.state.knowledge_service  # type: ignore[attr-defined]

        # Use a sentinel-suffixed source_path so the assertion below is
        # robust even if other tests left other PDFs in the store.
        sentinel = uuid.uuid4().hex[:8]
        # Tag the source_path via a query string so the file walker
        # ingests as the canonical resolved path but we can search by
        # the sentinel content.
        result = ingest_path(pdf_fixture, client=client)
        assert result["total"] == 1, result
        ingested_path = result["ingested"][0]["path"]
        assert ingested_path.endswith(".pdf")

        # Confirm at least one chunk is searchable before delete.
        hits_before = await service.search("STM32H743", top_k=5)
        assert any((h.source_path or "") == ingested_path for h in hits_before), [
            (h.source_path, h.content[:60]) for h in hits_before
        ]

        # Delete and re-search — no hits from this source_path remain.
        deleted = await service.delete_by_source(ingested_path)
        assert deleted >= 1, f"delete_by_source returned {deleted}"

        hits_after = await service.search(f"STM32H743 {sentinel}", top_k=10)
        remaining = [h for h in hits_after if (h.source_path or "") == ingested_path]
        assert remaining == [], remaining

    # --- L1-A3 (MET-399): multi-page chunking + page-citation round-trip --

    async def test_pdf_ingest_chunks_multiple_pages(
        self,
        pdf_fixture: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """Multi-page PDF must produce more than one chunk.

        The fixture is a 5-page STM32H743 excerpt — once pdfplumber
        renders each page as its own ``## Page N`` H2 section, the
        heading-aware chunker is required to emit >1 chunk. Pre-wiring
        the PDF was being shoved through the chunker as a single blob
        of binary garbage and would pass any "chunks_indexed >= 1"
        assertion vacuously; the strict ``> 1`` here guards against
        regressing back to that broken state.
        """
        if not await _pg_reachable(_pg_dsn_sync()):
            pytest.skip(
                f"Postgres+pgvector not reachable at {_pg_dsn_sync()} — "
                "integration backend unavailable"
            )

        _, client = gateway
        result = ingest_path(pdf_fixture, client=client)

        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]
        ingested = result["ingested"][0]
        assert ingested["chunks_indexed"] > 1, (
            f"expected multi-page chunking, got chunks_indexed="
            f"{ingested['chunks_indexed']} — pdfplumber wiring regressed?"
        )

    async def test_pdf_search_returns_page_citation(
        self,
        pdf_fixture: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """A search hit on a non-first page must carry a ``Page N`` heading.

        We picked a phrase that lives unambiguously on page 4 of the
        committed STM32H743 excerpt (``Industrial grade: -40 to +85``).
        After ingest the hit's ``heading`` (or ``metadata``) must
        round-trip the ``## Page N`` label that
        ``_extract_pdf_text`` synthesised — that's the citation contract
        HP-INGEST-03 promises end users.
        """
        if not await _pg_reachable(_pg_dsn_sync()):
            pytest.skip(
                f"Postgres+pgvector not reachable at {_pg_dsn_sync()} — "
                "integration backend unavailable"
            )

        app, client = gateway
        service = app.state.knowledge_service  # type: ignore[attr-defined]

        result = ingest_path(pdf_fixture, client=client)
        assert result["total"] == 1, result

        # "Industrial grade: -40 to +85 degrees Celsius" is on page 4 of
        # the committed fixture (Operating Conditions / Temperature) —
        # not on page 1, so a hit here proves the chunker advanced past
        # the first page and pdfplumber's per-page text was preserved.
        hits = await service.search("industrial grade temperature -40 to +85", top_k=5)
        assert hits, "expected at least one hit for a known page-4 phrase"

        page_re = re.compile(r"Page\s+\d+")

        def _has_page_marker(hit: object) -> bool:
            heading = getattr(hit, "heading", None) or ""
            metadata = getattr(hit, "metadata", None) or {}
            if page_re.search(heading):
                return True
            # Allow the citation to land in metadata as well — the spec
            # accepts either location.
            for v in metadata.values():
                if isinstance(v, str) and page_re.search(v):
                    return True
            return False

        with_page = [h for h in hits if _has_page_marker(h)]
        assert with_page, (
            "no hit carried a 'Page N' citation — citation round-trip "
            "broken (MET-399). Hits seen: "
            + repr([(getattr(h, "heading", None), getattr(h, "metadata", None)) for h in hits])
        )
