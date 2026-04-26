"""Unit tests for ``digital_twin.context.simulation_capture`` (MET-331)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.context.simulation_capture import (
    SimulationCapture,
    SimulationParams,
    SimulationResult,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType


def _params(
    *,
    solver: str = "calculix",
    sim_type: str = "fea",
    mesh: int = 10_000,
    materials: list[str] | None = None,
    bcs: list[dict[str, Any]] | None = None,
) -> SimulationParams:
    return SimulationParams(
        solver=solver,
        simulation_type=sim_type,
        mesh_element_count=mesh,
        mesh_element_type="tet10",
        materials=materials or ["steel_316"],
        boundary_conditions=bcs or [{"face": "fixed_base", "type": "fixed"}],
        load_cases=[{"name": "load_1", "magnitude_n": 1000.0}],
    )


def _result(*, status: str = "success", duration: float = 12.5) -> SimulationResult:
    return SimulationResult(
        status=status,
        duration_seconds=duration,
        max_stress=180.5,
        max_displacement=0.42,
        converged=True,
        iterations=23,
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


class TestRecord:
    @pytest.mark.asyncio
    async def test_records_full_input_set(self) -> None:
        cap = SimulationCapture()
        run = await cap.record_run(_params(), _result(), cad_model_id=uuid4())
        assert run.params.solver == "calculix"
        assert run.params.materials == ["steel_316"]
        assert run.result.max_stress == 180.5
        assert len(cap.all_runs()) == 1
        # Summary leads with sim_type + solver (vector-search friendly)
        assert "FEA" in run.summary and "calculix" in run.summary
        assert "max_stress" in run.summary

    @pytest.mark.asyncio
    async def test_completed_at_set_on_record(self) -> None:
        cap = SimulationCapture()
        run = await cap.record_run(_params(), _result())
        assert run.completed_at is not None
        assert run.completed_at >= run.started_at


# ---------------------------------------------------------------------------
# Fingerprint determinism
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_identical_params_same_fingerprint(self) -> None:
        a = _params()
        b = _params()
        assert a.fingerprint() == b.fingerprint()

    def test_different_solver_different_fingerprint(self) -> None:
        assert _params(solver="calculix").fingerprint() != _params(solver="elmer").fingerprint()

    def test_dict_key_order_does_not_matter(self) -> None:
        a = SimulationParams(
            solver="calculix",
            simulation_type="fea",
            solver_options={"tol": 1e-6, "max_iter": 100},
        )
        b = SimulationParams(
            solver="calculix",
            simulation_type="fea",
            solver_options={"max_iter": 100, "tol": 1e-6},
        )
        assert a.fingerprint() == b.fingerprint()


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


class TestSimilarity:
    @pytest.mark.asyncio
    async def test_identical_run_has_zero_distance(self) -> None:
        cap = SimulationCapture()
        await cap.record_run(_params(), _result())
        hits = cap.find_similar(_params(), top_k=1)
        assert hits[0].distance == 0.0

    @pytest.mark.asyncio
    async def test_solver_change_increases_distance(self) -> None:
        cap = SimulationCapture()
        await cap.record_run(_params(solver="calculix"), _result())
        hits = cap.find_similar(_params(solver="elmer"), top_k=1)
        assert hits[0].distance >= 1.0

    @pytest.mark.asyncio
    async def test_top_k_orders_by_distance_then_recency(self) -> None:
        cap = SimulationCapture()
        # Two runs that differ only in mesh count → smaller delta wins.
        await cap.record_run(_params(mesh=20_000), _result())
        await cap.record_run(_params(mesh=10_500), _result())
        hits = cap.find_similar(_params(mesh=10_000), top_k=2)
        assert len(hits) == 2
        assert hits[0].distance < hits[1].distance
        assert hits[0].run.params.mesh_element_count == 10_500

    @pytest.mark.asyncio
    async def test_cad_model_filter_penalises_other_models(self) -> None:
        cap = SimulationCapture()
        target_cad = uuid4()
        other_cad = uuid4()
        await cap.record_run(_params(), _result(), cad_model_id=other_cad)
        await cap.record_run(_params(), _result(), cad_model_id=target_cad)
        hits = cap.find_similar(_params(), top_k=2, cad_model_id=target_cad)
        assert hits[0].run.cad_model_id == target_cad
        assert hits[0].distance < hits[1].distance


# ---------------------------------------------------------------------------
# Knowledge integration
# ---------------------------------------------------------------------------


class _FakeKnowledge:
    def __init__(self) -> None:
        self.ingested: list[dict[str, Any]] = []

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        self.ingested.append(
            {
                "content": content,
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "source_work_product_id": source_work_product_id,
                "metadata": metadata or {},
            }
        )
        return IngestResult(entry_ids=[uuid4()], chunks_indexed=1, source_path=source_path)

    async def search(self, *args: Any, **kwargs: Any) -> list[SearchHit]:  # pragma: no cover
        return []

    async def delete_by_source(self, source_path: str) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


class TestKnowledgePush:
    @pytest.mark.asyncio
    async def test_session_summary_published_when_service_provided(self) -> None:
        knowledge = _FakeKnowledge()
        cap = SimulationCapture(knowledge_service=knowledge)  # type: ignore[arg-type]
        cad = uuid4()
        run = await cap.record_run(_params(), _result(), cad_model_id=cad)
        assert len(knowledge.ingested) == 1
        entry = knowledge.ingested[0]
        assert entry["knowledge_type"] == KnowledgeType.SESSION
        assert entry["source_path"] == f"simulation_run://{run.id}"
        assert entry["source_work_product_id"] == cad
        assert entry["metadata"]["solver"] == "calculix"
        assert entry["metadata"]["fingerprint"] == run.fingerprint
        assert entry["metadata"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_no_publish_when_service_absent(self) -> None:
        cap = SimulationCapture()
        await cap.record_run(_params(), _result())
        # No exception, no side effect
        assert cap.all_runs()

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_break_record(self) -> None:
        class _Broken(_FakeKnowledge):
            async def ingest(self, *args: Any, **kwargs: Any) -> IngestResult:
                raise RuntimeError("kafka down")

        cap = SimulationCapture(knowledge_service=_Broken())  # type: ignore[arg-type]
        run = await cap.record_run(_params(), _result())
        # The run was still captured; the publish failure logged a warning.
        assert cap.all_runs() == [run]
