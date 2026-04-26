"""End-to-end retrieval-quality eval (MET-326).

Ingests the labeled planner-repo fixtures into a real LightRAG service
and asserts that a tuned set of queries clears a sanity floor on
precision@5 / recall@5. Opt in with ``pytest --integration``.

Requires:
* The dev ``metaforge-postgres-1`` container running on
  ``localhost:5432`` with the ``vector`` extension.
* The local ``MetaForge-Planner`` checkout at
  ``/mnt/c/Users/odokf/Documents/MetaForge-Planner`` (the canonical
  source of the eval corpus).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from digital_twin.context.retrieval_evaluator import RetrievalEvaluator, load_eval_set
from digital_twin.knowledge import create_knowledge_service
from digital_twin.knowledge.types import KnowledgeType

pytestmark = pytest.mark.integration


_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"
_PLANNER_ROOT = Path("/mnt/c/Users/odokf/Documents/MetaForge-Planner")
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "retrieval_eval.json"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest.fixture
async def evaluated_service(tmp_path: Path) -> AsyncIterator[object]:
    """Spin up a LightRAG service, ingest the eval corpus, and tear down."""
    suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=str(tmp_path / f"lightrag-eval-{suffix}"),
        postgres_dsn=_dsn(),
        namespace_prefix=f"lightrag_eval_{suffix}",
    )
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        cases = load_eval_set(_FIXTURE)
        ingested: set[str] = set()
        for case in cases:
            for rel in case.relevant:
                if rel in ingested:
                    continue
                source = _PLANNER_ROOT / rel
                if not source.exists():
                    pytest.skip(f"planner fixture missing: {rel}")
                content = source.read_text(encoding="utf-8")
                await svc.ingest(  # type: ignore[attr-defined]
                    content=content,
                    source_path=rel,
                    knowledge_type=KnowledgeType.SPEC,
                )
                ingested.add(rel)
        yield svc
    finally:
        await svc.close()  # type: ignore[attr-defined]


async def test_planner_corpus_clears_precision_floor(evaluated_service: object) -> None:
    """Sanity-floor: tuned queries hit ``precision@5 >= 0.2`` overall.

    Floor reflects single-relevant-document queries: with k=5 and one
    truly relevant doc, the theoretical max precision@5 is 0.2. A
    healthy retriever should attain that on at least one query and
    average upward when multi-relevant queries are added.
    """
    cases = load_eval_set(_FIXTURE)
    evaluator = RetrievalEvaluator(evaluated_service, k=5)  # type: ignore[arg-type]
    report = await evaluator.evaluate(cases)
    assert report.duration_seconds < 30.0, f"eval too slow: {report.duration_seconds:.2f}s"
    # Recall is the more meaningful floor for a small fixture; precision
    # is bounded by 1/k for single-doc queries. Require both to clear
    # their respective floors.
    assert report.mean_recall >= 0.6, f"mean recall@5 below floor: {report.as_dict()}"
    assert report.mean_precision >= 0.15, f"mean precision@5 below floor: {report.as_dict()}"
    assert report.mrr >= 0.4, f"MRR below floor: {report.as_dict()}"
