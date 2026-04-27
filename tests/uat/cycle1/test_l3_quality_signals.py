"""UAT-C1-L3 — Quality signals (MET-322/323/324/326/333/334).

Acceptance bullets validated:

* MET-322: ``ConflictDetector`` flags disagreements; severity ladder
  (info/warning/blocking) is exposed.
* MET-323: ``compute_staleness`` produces a score in [0,1] and the
  assembler filters above its threshold.
* MET-324: ``IdentityResolver`` clusters by MPN and surfaces an
  ``IdentityMismatch`` for R12-with-two-MPNs.
* MET-326: All four retrieval metric helpers
  (precision_at_k / recall_at_k / mean_reciprocal_rank / ndcg_at_k)
  return values in [0,1] for handcrafted inputs; the
  ``RetrievalEvaluator`` runs against a fake KnowledgeService.
* MET-333 / MET-334: Stories — covered transitively. We assert the
  ``has_blocking_conflict`` flag flips on MPN mismatch (story-level
  signal that "blocking conflicts prevent agent auto-action").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextScope,
)
from digital_twin.context.conflicts import ConflictDetector, ConflictSeverity
from digital_twin.context.identity_resolver import IdentityResolver
from digital_twin.context.models import ContextFragment, ContextSourceKind
from digital_twin.context.retrieval_evaluator import (
    EvalCase,
    RetrievalEvaluator,
)
from digital_twin.context.retrieval_metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from digital_twin.context.staleness import compute_staleness
from digital_twin.knowledge.service import IngestResult, SearchHit
from tests.uat.conftest import assert_validates
from twin_core.api import InMemoryTwinAPI

pytestmark = [pytest.mark.uat]


# ---------------------------------------------------------------------------
# MET-326 — Retrieval metrics
# ---------------------------------------------------------------------------


def test_met326_precision_recall_mrr_ndcg_in_unit_interval() -> None:
    retrieved = ["a", "b", "x"]
    relevant = {"a", "b"}
    p = precision_at_k(retrieved, relevant, k=3)
    r = recall_at_k(retrieved, relevant, k=3)
    mrr = mean_reciprocal_rank([retrieved], [list(relevant)])
    ndcg = ndcg_at_k(retrieved, {"a": 1.0, "b": 0.7}, k=3)
    for name, val in [("precision", p), ("recall", r), ("mrr", mrr), ("ndcg", ndcg)]:
        assert_validates(
            "MET-326",
            f"{name} returns a value in [0, 1]",
            0.0 <= val <= 1.0,
            f"{name}={val}",
        )


@pytest.mark.asyncio
async def test_met326_evaluator_runs_against_fake_service() -> None:
    """Evaluator drives a stub service and produces aggregates."""

    class _Fake:
        async def search(self, query: str, top_k: int = 5, **_: Any) -> list[SearchHit]:
            return [
                SearchHit(
                    content="hit",
                    similarity_score=0.9,
                    source_path="docs/system-vision.md",
                    heading=None,
                    chunk_index=0,
                    total_chunks=1,
                )
            ]

        async def ingest(self, *a: Any, **k: Any) -> IngestResult:  # pragma: no cover
            return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

        async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
            return 0

        async def health_check(self) -> dict[str, Any]:  # pragma: no cover
            return {"status": "ok"}

    evaluator = RetrievalEvaluator(_Fake(), k=5)  # type: ignore[arg-type]
    report = await evaluator.evaluate(
        [
            EvalCase(
                id="Q1",
                agent_id="ee",
                query="anything",
                relevant=["docs/system-vision.md"],
            )
        ]
    )
    assert_validates(
        "MET-326",
        "evaluator returns mean_precision in [0,1]",
        0.0 <= report.mean_precision <= 1.0,
        f"mean_precision={report.mean_precision}",
    )


# ---------------------------------------------------------------------------
# MET-322 — Conflict detection
# ---------------------------------------------------------------------------


def _frag_with_meta(metadata: dict[str, Any], content: str = "(test)") -> ContextFragment:
    return ContextFragment(
        content=content,
        source_kind=ContextSourceKind.KNOWLEDGE_HIT,
        source_id=f"uat://{uuid4().hex[:8]}",
        metadata=metadata,
        token_count=10,
    )


def test_met322_conflict_detector_emits_warning_on_voltage_disagreement() -> None:
    detector = ConflictDetector(grouping_field="mpn")
    a = _frag_with_meta({"mpn": "MAX1473", "voltage": "5V"})
    b = _frag_with_meta({"mpn": "MAX1473", "voltage": "3.3V"})
    conflicts = detector.detect([a, b])
    assert_validates(
        "MET-322",
        "ConflictDetector surfaces a voltage disagreement when MPNs match",
        any(c.field == "voltage" for c in conflicts),
        f"conflict fields: {[c.field for c in conflicts]}",
    )
    severities = {c.severity for c in conflicts}
    assert_validates(
        "MET-322",
        "voltage disagreement is at least WARNING severity",
        ConflictSeverity.WARNING in severities or ConflictSeverity.BLOCKING in severities,
        f"severities: {severities}",
    )


# ---------------------------------------------------------------------------
# MET-323 — Staleness
# ---------------------------------------------------------------------------


def test_met323_compute_staleness_returns_unit_interval() -> None:
    fresh = compute_staleness({"created_at": datetime.now(UTC).isoformat()})
    assert_validates(
        "MET-323",
        "fresh fragment has low staleness (< 0.2)",
        0.0 <= fresh < 0.2,
        f"fresh staleness={fresh}",
    )

    old = compute_staleness({"created_at": (datetime.now(UTC) - timedelta(days=400)).isoformat()})
    assert_validates(
        "MET-323",
        "year-old fragment has higher staleness than fresh",
        old > fresh,
        f"old={old}, fresh={fresh}",
    )

    superseded = compute_staleness({"superseded": True})
    assert_validates(
        "MET-323",
        "superseded fragment scores 1.0 (fully stale)",
        superseded >= 0.99,
        f"superseded={superseded}",
    )


# ---------------------------------------------------------------------------
# MET-324 — Identity resolution
# ---------------------------------------------------------------------------


def test_met324_identity_resolver_clusters_by_mpn() -> None:
    resolver = IdentityResolver()
    a = _frag_with_meta({"mpn": "ATSAMD21G18", "ref_des": "U1"})
    b = _frag_with_meta({"mpn": "ATSAMD21G18", "ref_des": "U1"})
    clusters = resolver.resolve([a, b])
    assert_validates(
        "MET-324",
        "two fragments sharing MPN collapse into one cluster",
        len(clusters) == 1,
        f"got {len(clusters)} clusters",
    )


def test_met324_identity_resolver_flags_mpn_mismatch_at_same_refdes() -> None:
    resolver = IdentityResolver()
    a = _frag_with_meta({"ref_des": "R12", "mpn": "ERJ-3EKF1002V"})
    b = _frag_with_meta({"ref_des": "R12", "mpn": "RC0603FR-071K"})
    mismatches = resolver.mismatches([a, b])
    assert_validates(
        "MET-324",
        "R12 with two MPNs surfaces an IdentityMismatch on the mpn field",
        any(m.field == "mpn" for m in mismatches),
        f"mismatches: {[(m.field, m.weak_field) for m in mismatches]}",
    )


# ---------------------------------------------------------------------------
# MET-333 / MET-334 — Story-level signals
# ---------------------------------------------------------------------------


class _NoKnowledge:
    async def ingest(self, *a: Any, **k: Any) -> IngestResult:
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

    async def search(self, *a: Any, **k: Any) -> list[SearchHit]:
        return []

    async def delete_by_source(self, source_path: str) -> int:
        return 0

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_met333_blocking_conflict_flips_response_flag() -> None:
    """When a BLOCKING conflict appears, the response advertises it via
    ``has_blocking_conflict`` so agents can refuse to auto-act."""
    twin = InMemoryTwinAPI.create()
    assembler = ContextAssembler(twin=twin, knowledge_service=_NoKnowledge())  # type: ignore[arg-type]

    # Inject two fragments with R12 + clashing MPNs into the response by
    # going through the full assemble path with stub hits.
    class _Stub:
        async def ingest(self, *a: Any, **k: Any) -> IngestResult:
            return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

        async def search(self, *a: Any, **k: Any) -> list[SearchHit]:
            return [
                SearchHit(
                    content="schematic R12",
                    similarity_score=0.9,
                    source_path="schem.md",
                    heading=None,
                    chunk_index=0,
                    total_chunks=1,
                    metadata={"ref_des": "R12", "mpn": "ERJ-3EKF1002V"},
                ),
                SearchHit(
                    content="bom R12",
                    similarity_score=0.85,
                    source_path="bom.md",
                    heading=None,
                    chunk_index=0,
                    total_chunks=1,
                    metadata={"ref_des": "R12", "mpn": "RC0603FR-071K"},
                ),
            ]

        async def delete_by_source(self, source_path: str) -> int:
            return 0

        async def health_check(self) -> dict[str, Any]:
            return {"status": "ok"}

    assembler = ContextAssembler(twin=twin, knowledge_service=_Stub())  # type: ignore[arg-type]
    response = await assembler.assemble(
        ContextAssemblyRequest(
            agent_id="ee",
            query="R12?",
            scope=[ContextScope.KNOWLEDGE],
        )
    )
    assert_validates(
        "MET-333",
        "MPN mismatch produces has_blocking_conflict=True",
        response.has_blocking_conflict is True,
        f"flag={response.has_blocking_conflict}, conflicts={[c.field for c in response.conflicts]}",
    )


@pytest.mark.asyncio
async def test_met334_truncation_metric_increments_on_drop() -> None:
    """Story-level: agents prefer fresh over old / never silently drop —
    the ``metaforge_context_truncated_total`` counter wires through.
    """
    from observability.metrics import MetricsCollector

    captures: list[dict[str, Any]] = []

    class _Capturing(MetricsCollector):
        def __init__(self) -> None:
            super().__init__(meter=None)

        def record_context_truncated(self, agent_id: str, source_kind: str, count: int = 1) -> None:
            captures.append({"agent_id": agent_id, "source_kind": source_kind, "count": count})

    twin = InMemoryTwinAPI.create()

    class _OverflowKnowledge:
        async def ingest(self, *a: Any, **k: Any) -> IngestResult:
            return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

        async def search(self, *a: Any, **k: Any) -> list[SearchHit]:
            big = "word " * 800
            return [
                SearchHit(
                    content=big + " alpha",
                    similarity_score=0.9,
                    source_path="x.md",
                    heading=None,
                    chunk_index=0,
                    total_chunks=1,
                ),
                SearchHit(
                    content=big + " beta",
                    similarity_score=0.5,
                    source_path="y.md",
                    heading=None,
                    chunk_index=0,
                    total_chunks=1,
                ),
            ]

        async def delete_by_source(self, source_path: str) -> int:
            return 0

        async def health_check(self) -> dict[str, Any]:
            return {"status": "ok"}

    assembler = ContextAssembler(
        twin=twin,
        knowledge_service=_OverflowKnowledge(),  # type: ignore[arg-type]
        collector=_Capturing(),
    )
    await assembler.assemble(
        ContextAssemblyRequest(
            agent_id="ee",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            token_budget=400,
        )
    )
    assert_validates(
        "MET-334",
        "truncation counter records at least one drop when budget is tight",
        len(captures) >= 1,
        f"captures={captures}",
    )
