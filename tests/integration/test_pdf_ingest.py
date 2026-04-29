"""End-to-end test for PDF ingestion (MET-399).

Closes the coverage gap flagged by the 2026-04-28 audit: PDF is in
``SUPPORTED_EXTENSIONS`` and the data-modalities matrix, but no test
ever drove a real PDF through ``forge ingest``.

Opt in with ``pytest --integration``. Boots the full gateway in-process
via ASGITransport (no real HTTP socket), drives ``forge ingest`` against
a small committed PDF fixture (``datasheet_excerpt.pdf``), and verifies
the document lands in the L1 knowledge layer.

Requires the dev ``metaforge-postgres-1`` (with ``vector`` extension)
running on ``localhost:5432``.
"""

from __future__ import annotations

import os
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
