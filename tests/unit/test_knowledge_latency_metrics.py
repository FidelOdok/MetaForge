"""Unit tests for knowledge-search latency instrumentation (MET-401 / L1-A7).

HP-RETR-08 wants ``p95 < 200 ms`` on a 1k-doc corpus. These tests pin
the gating signal — ``metaforge_knowledge_search_duration_seconds`` —
so a regression that silently drops the histogram observation surfaces
in CI before it shows up on a Grafana panel.

Three pieces are asserted:

1. ``LightRAGKnowledgeService.search`` records the histogram exactly
   once per call with a positive float duration.
2. The same ``search`` call sets a ``knowledge_search.duration_ms`` /
   ``knowledge.duration_ms`` attribute on the active OTel span — the
   Tempo trace carries per-trace context for slow queries even when
   the histogram is the SLO authority.
3. ``MetricsRegistry.KNOWLEDGE_SEARCH_DURATION`` is registered with
   ``type="histogram"`` and bucket boundaries that span the SLO target
   (200 ms must fall inside a bucket so ``histogram_quantile`` is
   accurate at p95).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService
from observability.metrics import MetricsRegistry

# ---------------------------------------------------------------------------
# Stub chunks_vdb — same shape as ``test_knowledge_reranker.py`` so we
# never need real LightRAG, sentence-transformers, or asyncpg.
# ---------------------------------------------------------------------------


class _StubChunksVdb:
    """Tiny stand-in for LightRAG's ``chunks_vdb`` storage."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    async def query(self, query: str, top_k: int) -> list[dict[str, Any]]:
        return self._chunks


def _raw_chunk(content: str, source_path: str, score: float, idx: int) -> dict[str, Any]:
    """Build a raw chunk dict shaped like LightRAG's NanoVectorDB output."""
    meta = {
        "ver": "v1",
        "src": source_path,
        "ci": idx,
        "tc": 1,
        "h": None,
        "kt": "design_decision",
        "wp": None,
        "x": {"project_id": "default"},
    }
    return {
        "id": f"chunk-{idx}",
        "content": content,
        "file_path": json.dumps(meta, separators=(",", ":"), sort_keys=True),
        "distance": 1.0 - score,
    }


def _make_service(
    metrics_collector: Any | None = None,
) -> LightRAGKnowledgeService:
    """Build a service whose ``search`` runs entirely in-process."""
    svc = LightRAGKnowledgeService(
        working_dir="/tmp/uat-l1-a7-latency",
        metrics_collector=metrics_collector,
    )
    svc._initialized = True
    fake_rag = MagicMock()
    fake_rag.chunks_vdb = _StubChunksVdb(
        [
            _raw_chunk("alpha", "a.md", 0.9, 0),
            _raw_chunk("beta", "b.md", 0.8, 1),
        ]
    )
    svc._rag = fake_rag
    return svc


# ---------------------------------------------------------------------------
# 1. Histogram is observed on each search
# ---------------------------------------------------------------------------


class TestSearchIncrementsHistogram:
    @pytest.mark.asyncio
    async def test_search_increments_histogram(self) -> None:
        """``search`` records exactly one histogram sample per call."""
        collector = MagicMock()
        svc = _make_service(metrics_collector=collector)

        await svc.search("anything", top_k=2)

        collector.record_knowledge_search_duration.assert_called_once()
        args, _kwargs = collector.record_knowledge_search_duration.call_args
        # Signature is (top_k, duration_seconds).
        assert args[0] == 2
        duration = args[1]
        assert isinstance(duration, float)
        assert duration > 0.0
        # Sanity: a stub that does no I/O finishes well under 5 s.
        assert duration < 5.0

    @pytest.mark.asyncio
    async def test_search_records_once_per_call(self) -> None:
        """Two ``search`` calls produce two histogram observations."""
        collector = MagicMock()
        svc = _make_service(metrics_collector=collector)

        await svc.search("query-a", top_k=2)
        await svc.search("query-b", top_k=2)

        assert collector.record_knowledge_search_duration.call_count == 2

    @pytest.mark.asyncio
    async def test_search_works_without_collector(self) -> None:
        """No collector wired → search is still callable (no-op metrics)."""
        svc = _make_service(metrics_collector=None)
        hits = await svc.search("anything", top_k=2)
        assert len(hits) == 2  # path is unchanged when metrics are off


# ---------------------------------------------------------------------------
# 2. Span attribute carries the duration
# ---------------------------------------------------------------------------


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Attach an in-memory span exporter to the active TracerProvider.

    Mirrors ``tests/unit/test_mcp_otel_instrumentation.py`` — OTel
    forbids replacing the global TracerProvider after first use, so we
    only install a fresh SDK provider when the active one is the NoOp
    default.
    """
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)  # type: ignore[attr-defined]
    try:
        yield exporter
    finally:
        exporter.clear()


class TestSearchSpanDurationAttribute:
    @pytest.mark.asyncio
    async def test_search_records_duration_span_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """The ``lightrag.search`` span carries a duration attribute."""
        svc = _make_service(metrics_collector=None)
        await svc.search("anything", top_k=2)

        spans = [s for s in span_exporter.get_finished_spans() if s.name == "lightrag.search"]
        assert spans, (
            "expected a 'lightrag.search' span; got "
            f"{[s.name for s in span_exporter.get_finished_spans()]}"
        )
        attrs = dict(spans[-1].attributes or {})
        # Either spelling is acceptable per the spec ("duration_ms or
        # equivalent"); we set both for forward compatibility.
        assert "knowledge_search.duration_ms" in attrs or "knowledge.duration_ms" in attrs
        duration_ms = attrs.get("knowledge_search.duration_ms") or attrs.get(
            "knowledge.duration_ms"
        )
        assert isinstance(duration_ms, (int, float))
        assert duration_ms > 0.0


# ---------------------------------------------------------------------------
# 3. Histogram is registered with sane bucket boundaries
# ---------------------------------------------------------------------------


class TestKnowledgeSearchHistogramRegistration:
    def test_histogram_metric_definition_exists(self) -> None:
        """``MetricsRegistry`` declares the new histogram."""
        defn = MetricsRegistry.KNOWLEDGE_SEARCH_DURATION
        # Platform convention: every metric carries the ``metaforge_``
        # prefix; the spec's bare ``knowledge_search_duration_seconds``
        # is preserved as a substring inside the canonical name.
        assert defn.name == "metaforge_knowledge_search_duration_seconds"
        assert "knowledge_search_duration_seconds" in defn.name
        assert defn.type == "histogram"
        assert defn.unit == "s"
        # Bucket boundaries must span the SLO target so
        # ``histogram_quantile(0.95, …) > 0.2`` is meaningful.
        assert defn.buckets is not None
        assert 0.2 in defn.buckets, f"200 ms boundary must be a bucket edge, got {defn.buckets!r}"
        # Coarse sanity: at least one sub-10 ms bucket and one >= 1 s
        # bucket so both fast and slow queries land inside a bucket.
        assert any(b <= 0.01 for b in defn.buckets)
        assert any(b >= 1.0 for b in defn.buckets)

    def test_histogram_is_in_all_metrics_registry(self) -> None:
        """The new histogram is reachable through ``all_metrics()``."""
        names = {m.name for m in MetricsRegistry.all_metrics()}
        assert "metaforge_knowledge_search_duration_seconds" in names

    def test_histogram_is_in_knowledge_metrics_group(self) -> None:
        """A dedicated grouped accessor exists for the L1-A7 metric."""
        names = {m.name for m in MetricsRegistry.knowledge_metrics()}
        assert "metaforge_knowledge_search_duration_seconds" in names
