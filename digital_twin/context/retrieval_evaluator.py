"""Retrieval-quality evaluator harness (MET-326).

Loads a labeled query→relevant-source fixture, runs each query through a
live ``KnowledgeService``, and computes precision@k / recall@k / MRR /
NDCG@k aggregates per query and across the eval set. Optional
``MetricsCollector`` integration emits each measurement to the OTel
pipeline so the same numbers light up Grafana dashboards.

Fixture format (matches ``tests/fixtures/knowledge/retrieval_eval.json``)::

    {
      "version": "v1",
      "queries": [
        {
          "id": "Q-LAYERS",
          "agent_id": "mechanical_agent",
          "query": "What are the 4 hardware development layers?",
          "relevant": ["docs/research/hardware-development-layers.md"],
          "relevance_grades": {"docs/...md": 1.0}   # optional, NDCG only
        },
        ...
      ]
    }

The evaluator is intentionally framework-agnostic: it talks to
``KnowledgeService`` (the Protocol), not to LightRAG. Tests can pass a
fake implementation; the integration test passes the real one.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from digital_twin.context.retrieval_metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from digital_twin.knowledge.service import KnowledgeService, SearchHit
from observability.metrics import MetricsCollector
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.context.retrieval_evaluator")

__all__ = [
    "EvalCase",
    "EvalReport",
    "QueryResult",
    "RetrievalEvaluator",
    "load_eval_set",
]


@dataclass
class EvalCase:
    """One labeled query in the eval set."""

    id: str
    agent_id: str
    query: str
    relevant: list[str]
    relevance_grades: dict[str, float] = field(default_factory=dict)

    @property
    def graded(self) -> dict[str, float]:
        """Return the relevance map, defaulting absent entries to 1.0."""
        if self.relevance_grades:
            return dict(self.relevance_grades)
        return {sid: 1.0 for sid in self.relevant}


@dataclass
class QueryResult:
    """Per-query retrieval metrics + the raw retrieved ids."""

    case_id: str
    agent_id: str
    retrieved: list[str]
    precision_at_k: float
    recall_at_k: float
    ndcg_at_k: float
    reciprocal_rank: float


@dataclass
class EvalReport:
    """Aggregate scores across the full eval set."""

    k: int
    query_results: list[QueryResult]
    mean_precision: float
    mean_recall: float
    mean_ndcg: float
    mrr: float
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "queries": len(self.query_results),
            "mean_precision_at_k": self.mean_precision,
            "mean_recall_at_k": self.mean_recall,
            "mean_ndcg_at_k": self.mean_ndcg,
            "mrr": self.mrr,
            "duration_seconds": self.duration_seconds,
            "per_query": [
                {
                    "id": r.case_id,
                    "agent_id": r.agent_id,
                    "precision_at_k": r.precision_at_k,
                    "recall_at_k": r.recall_at_k,
                    "ndcg_at_k": r.ndcg_at_k,
                    "reciprocal_rank": r.reciprocal_rank,
                }
                for r in self.query_results
            ],
        }


def load_eval_set(path: str | Path) -> list[EvalCase]:
    """Parse the JSON fixture into ``EvalCase`` objects."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for row in raw.get("queries", []):
        cases.append(
            EvalCase(
                id=row["id"],
                agent_id=row["agent_id"],
                query=row["query"],
                relevant=list(row.get("relevant", [])),
                relevance_grades=dict(row.get("relevance_grades", {})),
            )
        )
    return cases


def _hit_id(hit: SearchHit) -> str:
    """Stable id used to compare a retrieved hit against the relevant set.

    The eval fixture references documents by ``source_path``; that is the
    only field guaranteed to round-trip through ingest → store → search.
    Fall back to a synthetic id when the path is missing so a malformed
    hit can never be silently treated as a match.
    """
    if hit.source_path:
        return hit.source_path
    return f"chunk://{hit.chunk_index}"


class RetrievalEvaluator:
    """Run an eval set against a live ``KnowledgeService``."""

    def __init__(
        self,
        knowledge_service: KnowledgeService,
        collector: MetricsCollector | None = None,
        k: int = 5,
    ) -> None:
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        self._knowledge_service = knowledge_service
        self._collector = collector
        self._k = k

    async def evaluate(self, cases: Sequence[EvalCase]) -> EvalReport:
        """Score every ``case`` and return aggregates."""
        with tracer.start_as_current_span("retrieval.evaluate") as span:
            span.set_attribute("eval.cases", len(cases))
            span.set_attribute("eval.k", self._k)

            start = time.perf_counter()
            per_query: list[QueryResult] = []
            rankings: list[list[str]] = []
            relevants: list[list[str]] = []

            for case in cases:
                hits = await self._knowledge_service.search(
                    query=case.query,
                    top_k=self._k,
                )
                retrieved_ids = [_hit_id(h) for h in hits]
                rankings.append(retrieved_ids)
                relevants.append(list(case.relevant))

                p = precision_at_k(retrieved_ids, case.relevant, self._k)
                r = recall_at_k(retrieved_ids, case.relevant, self._k)
                n = ndcg_at_k(retrieved_ids, case.graded, self._k)
                rr = _reciprocal_rank(retrieved_ids, case.relevant)

                per_query.append(
                    QueryResult(
                        case_id=case.id,
                        agent_id=case.agent_id,
                        retrieved=retrieved_ids,
                        precision_at_k=p,
                        recall_at_k=r,
                        ndcg_at_k=n,
                        reciprocal_rank=rr,
                    )
                )

                if self._collector is not None:
                    self._collector.record_retrieval_precision(case.agent_id, self._k, p)
                    self._collector.record_retrieval_recall(case.agent_id, self._k, r)
                    self._collector.record_retrieval_ndcg(case.agent_id, self._k, n)

            mrr = mean_reciprocal_rank(rankings, relevants)
            mean_p = _safe_mean(q.precision_at_k for q in per_query)
            mean_r = _safe_mean(q.recall_at_k for q in per_query)
            mean_n = _safe_mean(q.ndcg_at_k for q in per_query)
            duration = time.perf_counter() - start

            if self._collector is not None and per_query:
                # MRR is an aggregate, recorded once per eval run with a
                # synthetic agent_id so dashboards can split per-agent
                # MRR (when the eval set has ≥2 agents) vs. a fleet
                # average.
                self._collector.record_retrieval_mrr("eval_aggregate", mrr)

            report = EvalReport(
                k=self._k,
                query_results=per_query,
                mean_precision=mean_p,
                mean_recall=mean_r,
                mean_ndcg=mean_n,
                mrr=mrr,
                duration_seconds=duration,
            )
            logger.info(
                "retrieval_eval_complete",
                cases=len(cases),
                k=self._k,
                mean_precision=mean_p,
                mean_recall=mean_r,
                mean_ndcg=mean_n,
                mrr=mrr,
                duration_seconds=round(duration, 3),
            )
            span.set_attribute("eval.mean_precision", mean_p)
            span.set_attribute("eval.mean_recall", mean_r)
            span.set_attribute("eval.mrr", mrr)
            return report

    def evaluate_sync(self, cases: Sequence[EvalCase]) -> EvalReport:
        """Convenience wrapper for CLI / scripts that aren't async."""
        return asyncio.run(self.evaluate(cases))


def _reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    relevant_set = set(relevant)
    for rank, sid in enumerate(retrieved, start=1):
        if sid in relevant_set:
            return 1.0 / rank
    return 0.0


def _safe_mean(values: Any) -> float:
    seq = list(values)
    if not seq:
        return 0.0
    return sum(seq) / len(seq)
