"""Unit tests for ``digital_twin.context.retrieval_evaluator`` (MET-326).

Uses an in-memory ``KnowledgeService`` stub so the harness logic can be
verified without a Postgres / LightRAG dependency. The integration test
suite exercises the real path end-to-end.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from digital_twin.context.retrieval_evaluator import (
    EvalCase,
    RetrievalEvaluator,
    load_eval_set,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType


class _FakeKnowledge:
    """Returns canned ``SearchHit`` lists keyed by exact query string."""

    def __init__(self, table: dict[str, list[SearchHit]]) -> None:
        self._table = table
        self.search_calls: list[tuple[str, int]] = []

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        self.search_calls.append((query, top_k))
        return list(self._table.get(query, []))[:top_k]

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path=source_path)

    async def delete_by_source(self, source_path: str) -> int:
        return 0

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok"}


def _hit(path: str, score: float = 0.9, chunk: int = 0) -> SearchHit:
    return SearchHit(
        content=f"chunk {chunk} of {path}",
        similarity_score=score,
        source_path=path,
        heading=None,
        chunk_index=chunk,
        total_chunks=1,
    )


@pytest.mark.asyncio
async def test_evaluator_scores_perfect_retrieval() -> None:
    cases = [
        EvalCase(id="Q1", agent_id="a1", query="q1", relevant=["doc-a.md"]),
        EvalCase(id="Q2", agent_id="a1", query="q2", relevant=["doc-b.md"]),
    ]
    fake = _FakeKnowledge(
        {
            "q1": [_hit("doc-a.md")],
            "q2": [_hit("doc-b.md")],
        }
    )
    evaluator = RetrievalEvaluator(fake, k=5)  # type: ignore[arg-type]
    report = await evaluator.evaluate(cases)
    # Only 1 hit returned per query; precision_at_k clamps to len(top_k)
    # so the single hit gives precision = 1.0.
    assert report.mean_precision == 1.0
    assert report.mean_recall == 1.0
    assert report.mrr == 1.0
    assert report.mean_ndcg == 1.0
    assert {qr.case_id for qr in report.query_results} == {"Q1", "Q2"}


@pytest.mark.asyncio
async def test_evaluator_handles_misses() -> None:
    cases = [
        EvalCase(id="Q1", agent_id="a1", query="q1", relevant=["target.md"]),
    ]
    fake = _FakeKnowledge({"q1": [_hit("noise.md")]})
    evaluator = RetrievalEvaluator(fake, k=5)  # type: ignore[arg-type]
    report = await evaluator.evaluate(cases)
    assert report.mean_precision == 0.0
    assert report.mean_recall == 0.0
    assert report.mrr == 0.0
    assert report.mean_ndcg == 0.0


@pytest.mark.asyncio
async def test_evaluator_records_metrics_when_collector_present() -> None:
    class _Collector:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def record_retrieval_precision(self, agent_id: str, k: int, value: float) -> None:
            self.calls.append(("precision", agent_id, str(k), str(value)))

        def record_retrieval_recall(self, agent_id: str, k: int, value: float) -> None:
            self.calls.append(("recall", agent_id, str(k), str(value)))

        def record_retrieval_ndcg(self, agent_id: str, k: int, value: float) -> None:
            self.calls.append(("ndcg", agent_id, str(k), str(value)))

        def record_retrieval_mrr(self, agent_id: str, value: float) -> None:
            self.calls.append(("mrr", agent_id, str(value)))

    cases = [EvalCase(id="Q1", agent_id="mech", query="q1", relevant=["a.md"])]
    fake = _FakeKnowledge({"q1": [_hit("a.md")]})
    collector = _Collector()
    evaluator = RetrievalEvaluator(fake, collector=collector, k=3)  # type: ignore[arg-type]
    await evaluator.evaluate(cases)
    kinds = [c[0] for c in collector.calls]
    assert "precision" in kinds
    assert "recall" in kinds
    assert "ndcg" in kinds
    assert "mrr" in kinds


@pytest.mark.asyncio
async def test_evaluator_passes_top_k_to_search() -> None:
    fake = _FakeKnowledge({"q": [_hit("x.md")]})
    evaluator = RetrievalEvaluator(fake, k=7)  # type: ignore[arg-type]
    await evaluator.evaluate([EvalCase(id="Q", agent_id="a", query="q", relevant=["x.md"])])
    assert fake.search_calls == [("q", 7)]


def test_load_eval_set_parses_fixture(tmp_path: Any) -> None:
    f = tmp_path / "eval.json"
    f.write_text(
        '{"version":"v1","queries":[{"id":"X","agent_id":"agent","query":"q",'
        '"relevant":["a.md","b.md"],"relevance_grades":{"a.md":1.0,"b.md":0.5}}]}'
    )
    cases = load_eval_set(f)
    assert len(cases) == 1
    case = cases[0]
    assert case.id == "X"
    assert case.relevant == ["a.md", "b.md"]
    assert case.graded == {"a.md": 1.0, "b.md": 0.5}


def test_eval_case_graded_defaults_to_one_when_grades_absent() -> None:
    case = EvalCase(id="Q", agent_id="a", query="q", relevant=["x.md", "y.md"])
    assert case.graded == {"x.md": 1.0, "y.md": 1.0}


def test_evaluator_rejects_invalid_k() -> None:
    fake = _FakeKnowledge({})
    with pytest.raises(ValueError):
        RetrievalEvaluator(fake, k=0)  # type: ignore[arg-type]
