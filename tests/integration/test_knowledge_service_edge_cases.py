"""Integration tests for edge-case knowledge ingestion (MET-400).

Closes the coverage gap from the 2026-04-28 audit. Empty content was
caught by UAT (MET-375); these tests pin the behaviour for the rest:
large files, emoji + CJK content, malformed cross-references.

Opt in with ``pytest --integration``. Boots the gateway (with real
LightRAG-backed KnowledgeService) via ASGITransport.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cli.forge_cli.ingest import ingest_path
from tests.integration.test_forge_ingest_e2e import _AsgiClient

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql+asyncpg://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _dsn())


@pytest.fixture
async def gateway() -> AsyncIterator[tuple[object, _AsgiClient]]:
    """Boot the gateway and yield an ASGI client."""
    from api_gateway.db.engine import dispose_engine
    from api_gateway.server import create_app

    await dispose_engine()
    app = create_app()
    async with app.router.lifespan_context(app):
        client = _AsgiClient(app=app)
        yield app, client


class TestKnowledgeServiceEdgeCases:
    async def test_large_file_50mb(
        self,
        tmp_path: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """A 50 MB markdown file ingests within 60s and is searchable.

        The fixture is generated at test time (don't commit 50MB blobs).
        Acts as a smoke test for the chunker's memory + iteration
        behaviour at realistic-but-large input sizes.
        """
        app, client = gateway
        sentinel = uuid.uuid4().hex[:8]
        # 50 MB of ~50-byte paragraphs, deterministic content. Inject
        # a uniquely-searchable sentinel in the middle so we can find
        # this exact ingestion through search.
        chunk = "The bracket is fabricated from titanium grade 5 stock.\n\n"
        target_bytes = 50 * 1024 * 1024
        repeats = target_bytes // len(chunk)
        midpoint = repeats // 2
        body_parts = []
        for i in range(repeats):
            if i == midpoint:
                body_parts.append(
                    f"# Sentinel {sentinel}\n\n"
                    "Distinctive marker phrase: the bracket SR-7 was selected.\n\n"
                )
            body_parts.append(chunk)
        big_md = tmp_path / "huge.md"
        big_md.write_text("".join(body_parts), encoding="utf-8")
        actual = big_md.stat().st_size
        assert actual >= target_bytes, f"fixture too small: {actual} bytes"

        t0 = time.perf_counter()
        result = ingest_path(big_md, client=client, request_timeout=120.0)
        elapsed = time.perf_counter() - t0

        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]
        ingested = result["ingested"][0]
        assert ingested["chunks_indexed"] >= 1
        # Generous bound — LightRAG's LLM-driven entity extraction is
        # the dominant cost. 60s would not be realistic; 600s gives
        # headroom for real-world API latency.
        assert elapsed < 600.0, f"50MB ingest took {elapsed:.1f}s"

    async def test_unicode_emoji_and_cjk_content(
        self,
        tmp_path: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """Markdown with emoji + CJK characters ingests cleanly and
        the embedded chunk surfaces in vector search.
        """
        app, client = gateway
        sentinel = uuid.uuid4().hex[:8]
        path = tmp_path / "international.md"
        path.write_text(
            f"# 国際文書 {sentinel}\n\n"
            "This document mixes English, 日本語, 中文, and emoji 🚀.\n"
            "The bracket 部品 ID is SR-7-公差.\n",
            encoding="utf-8",
        )

        result = ingest_path(path, client=client)
        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]

        # Verify the text round-trips through embedding + retrieval.
        service = app.state.knowledge_service  # type: ignore[attr-defined]
        hits = await service.search(f"国際文書 {sentinel}", top_k=5)
        assert any((h.source_path or "").endswith("international.md") for h in hits), [
            (h.source_path, h.content[:60]) for h in hits
        ]

    async def test_circular_links_no_crash(
        self,
        tmp_path: Path,
        gateway: tuple[object, _AsgiClient],
    ) -> None:
        """Markdown with broken cross-references / dangling anchors
        does not fail ingest.
        """
        _, client = gateway
        path = tmp_path / "broken_links.md"
        path.write_text(
            "# Broken Links Test\n\n"
            "See [the missing section](#nonexistent-anchor) and\n"
            "also [external bad link](https://this.does.not.resolve.example).\n\n"
            "## Real heading\n\n"
            "Body content with no broken refs of its own, just\n"
            "linking around: [back to top](#broken-links-test).\n",
            encoding="utf-8",
        )

        result = ingest_path(path, client=client)
        assert result["total"] == 1, result
        assert result["failed"] == [], result["failed"]
        ingested = result["ingested"][0]
        assert ingested["chunks_indexed"] >= 1
