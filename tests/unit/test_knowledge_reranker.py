"""Unit tests for the L1 hybrid-search reranker (MET-335).

These tests must run completely offline — the real
``BAAI/bge-reranker-base`` model is ~440 MB. We patch
``digital_twin.knowledge.reranker.CrossEncoder`` with a stub and assert:

* ``Reranker`` constructor does not load the model.
* The first ``rerank()`` call instantiates the cross-encoder exactly
  once and re-orders the hits by descending cross-encoder score.
* ``LightRAGKnowledgeService.search(rerank=False)`` never imports nor
  instantiates the reranker module — disabled deployments pay zero cost.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from digital_twin.knowledge.reranker import Reranker
from digital_twin.knowledge.service import SearchHit


def _make_hit(content: str, similarity: float, source_path: str) -> SearchHit:
    """Build a minimal ``SearchHit`` for reranker fixtures."""
    return SearchHit(
        content=content,
        similarity_score=similarity,
        source_path=source_path,
        heading=None,
        chunk_index=0,
        total_chunks=1,
    )


def _fixture_hits() -> list[SearchHit]:
    """Five fake hits.

    Cosine ordering (similarity_score, descending):
        h_noise (0.92) > h_dup (0.85) > h_relevant (0.74)
        > h_off1 (0.62) > h_off2 (0.55)

    But ``h_relevant`` is the chunk that should win on cross-encoder
    score — it directly answers the query. The reranker stub below
    encodes this preference so the test verifies promotion of a lower
    cosine-score chunk over higher-cosine but noisier near-duplicates.
    """
    return [
        _make_hit(
            "thermal management is loosely related but mostly noise",
            0.92,
            "noise.md",
        ),
        _make_hit(
            "thermal management is loosely related (near-duplicate)",
            0.85,
            "dup.md",
        ),
        _make_hit(
            "the dedicated thermal management strategy is a copper "
            "heat-spreader plus a 40mm fan rated 12 CFM",
            0.74,
            "relevant.md",
        ),
        _make_hit(
            "off-topic discussion of supplier lead times",
            0.62,
            "off1.md",
        ),
        _make_hit(
            "off-topic discussion of EMC certification",
            0.55,
            "off2.md",
        ),
    ]


# ---------------------------------------------------------------------------
# Reranker class — lazy loading and ordering
# ---------------------------------------------------------------------------


class TestRerankerLazyLoad:
    def test_constructor_does_not_load_model(self) -> None:
        """Constructing a ``Reranker`` must not pay the 440 MB model cost.

        We patch ``CrossEncoder`` to a MagicMock that records calls; the
        constructor must not invoke it.
        """
        with patch("digital_twin.knowledge.reranker.CrossEncoder") as mock_ce:
            r = Reranker()
            assert mock_ce.call_count == 0
            assert r.is_loaded is False
            assert r.model_name == "BAAI/bge-reranker-base"

    @pytest.mark.asyncio
    async def test_first_rerank_loads_model_exactly_once(self) -> None:
        """The first ``rerank()`` call instantiates the model; the second does not."""
        with patch("digital_twin.knowledge.reranker.CrossEncoder") as mock_ce:
            instance = MagicMock()
            instance.predict.return_value = [0.1, 0.9]
            mock_ce.return_value = instance

            r = Reranker()
            hits = [
                _make_hit("a", 0.5, "a.md"),
                _make_hit("b", 0.4, "b.md"),
            ]
            await r.rerank("query", hits)
            assert mock_ce.call_count == 1
            assert r.is_loaded is True

            # Second call must not re-instantiate the model.
            instance.predict.return_value = [0.9, 0.1]
            await r.rerank("query", hits)
            assert mock_ce.call_count == 1


# ---------------------------------------------------------------------------
# Reranker class — ordering semantics
# ---------------------------------------------------------------------------


class TestRerankerOrdering:
    @pytest.mark.asyncio
    async def test_rerank_promotes_relevant_chunk_over_higher_cosine_noise(
        self,
    ) -> None:
        """Engineered cross-encoder scores promote the relevant chunk
        even though it has the third-highest cosine score.
        """
        hits = _fixture_hits()
        # Source order: [noise, dup, relevant, off1, off2]
        # Cross-encoder scores below promote ``relevant`` to the top.
        ce_scores = [0.10, 0.20, 0.95, 0.05, 0.02]

        with patch("digital_twin.knowledge.reranker.CrossEncoder") as mock_ce:
            instance = MagicMock()
            instance.predict.return_value = ce_scores
            mock_ce.return_value = instance

            r = Reranker()
            reranked = await r.rerank("how is thermal managed?", hits)

        # Relevant chunk wins despite its lower cosine score.
        assert reranked[0].source_path == "relevant.md"
        # Order is strictly descending by cross-encoder score.
        expected_order = ["relevant.md", "dup.md", "noise.md", "off1.md", "off2.md"]
        assert [h.source_path for h in reranked] == expected_order
        # The cross-encoder was passed (query, content) pairs in input order.
        called_pairs = instance.predict.call_args.args[0]
        assert called_pairs[0][0] == "how is thermal managed?"
        assert called_pairs[0][1] == hits[0].content

    @pytest.mark.asyncio
    async def test_rerank_empty_list_does_not_load_model(self) -> None:
        with patch("digital_twin.knowledge.reranker.CrossEncoder") as mock_ce:
            r = Reranker()
            assert await r.rerank("q", []) == []
            assert mock_ce.call_count == 0

    @pytest.mark.asyncio
    async def test_rerank_single_hit_does_not_load_model(self) -> None:
        with patch("digital_twin.knowledge.reranker.CrossEncoder") as mock_ce:
            r = Reranker()
            single = [_make_hit("only", 0.7, "only.md")]
            out = await r.rerank("q", single)
            assert [h.source_path for h in out] == ["only.md"]
            assert mock_ce.call_count == 0


# ---------------------------------------------------------------------------
# LightRAGKnowledgeService.search(rerank=...) — disabled path is cost-free
# ---------------------------------------------------------------------------


class _StubChunksVdb:
    """Tiny stand-in for LightRAG's ``chunks_vdb`` storage.

    Returns whatever fixture chunks the test injects; keeps the
    ``KnowledgeService.search`` path off real LightRAG / Postgres.
    """

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def query(self, query: str, top_k: int) -> list[dict[str, Any]]:
        self.calls.append({"query": query, "top_k": top_k})
        return self._chunks


def _raw_chunk(content: str, source_path: str, score: float, idx: int) -> dict[str, Any]:
    """Build a raw chunk dict shaped like LightRAG's NanoVectorDB output.

    ``file_path`` carries the JSON metadata blob produced by
    ``_encode_meta`` in the LightRAG service. We re-build it here so the
    service's ``_chunk_to_hit`` decoder accepts the row.
    """
    import json

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
        "distance": 1.0 - score,  # NanoVectorDB returns distance
    }


class TestLightRAGRerankIntegration:
    @pytest.mark.asyncio
    async def test_search_rerank_false_does_not_import_reranker(self) -> None:
        """``rerank=False`` must not import ``digital_twin.knowledge.reranker``.

        The reranker module pulls ``sentence_transformers`` lazily; we
        prove the disabled path never reaches that import by checking
        ``sys.modules`` before and after a search call.
        """
        # Drop the module if it's already cached from a previous test —
        # we want to observe whether *this* call imports it.
        sys.modules.pop("digital_twin.knowledge.reranker", None)

        from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService

        svc = LightRAGKnowledgeService(working_dir="/tmp/uat-rerank-disabled")
        # Skip the real initialize() — fake the state it would have set.
        svc._initialized = True
        fake_rag = MagicMock()
        fake_rag.chunks_vdb = _StubChunksVdb(
            [
                _raw_chunk("alpha", "a.md", 0.9, 0),
                _raw_chunk("beta", "b.md", 0.8, 1),
            ]
        )
        svc._rag = fake_rag

        hits = await svc.search("anything", top_k=2, rerank=False)
        assert len(hits) == 2
        assert "digital_twin.knowledge.reranker" not in sys.modules

    @pytest.mark.asyncio
    async def test_search_rerank_true_runs_reranker_and_truncates(self) -> None:
        """``rerank=True`` retrieves ``top_k * 3`` candidates, reranks,
        and truncates to ``top_k``.
        """
        from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService

        # Five raw chunks; cosine-best is "noise", reranker-best is "relevant".
        raw = [
            _raw_chunk("noise text", "noise.md", 0.92, 0),
            _raw_chunk("near-duplicate noise", "dup.md", 0.85, 1),
            _raw_chunk("the relevant answer", "relevant.md", 0.74, 2),
            _raw_chunk("off-topic 1", "off1.md", 0.62, 3),
            _raw_chunk("off-topic 2", "off2.md", 0.55, 4),
        ]

        svc = LightRAGKnowledgeService(working_dir="/tmp/uat-rerank-enabled")
        svc._initialized = True
        stub = _StubChunksVdb(raw)
        fake_rag = MagicMock()
        fake_rag.chunks_vdb = stub
        svc._rag = fake_rag

        with patch("digital_twin.knowledge.reranker.CrossEncoder") as mock_ce:
            instance = MagicMock()
            # After the service sorts by similarity_score descending,
            # the order entering the reranker will be:
            #   [noise, dup, relevant, off1, off2]
            instance.predict.return_value = [0.10, 0.20, 0.95, 0.05, 0.02]
            mock_ce.return_value = instance

            hits = await svc.search("query", top_k=2, rerank=True)

        # top_k * 3 = 6, the stub had 5 chunks so all 5 were fetched.
        assert stub.calls[0]["top_k"] >= 6
        # Reranked top is "relevant"; truncated to top_k=2.
        assert len(hits) == 2
        assert hits[0].source_path == "relevant.md"
        assert hits[1].source_path == "dup.md"
