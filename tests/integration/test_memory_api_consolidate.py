"""Integration test for ``POST /v1/memory/consolidate`` (MET-454)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.memory import router as memory_router
from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.consolidation import (
    ConsolidationOrchestrator,
    EventGrouper,
    InMemoryEventFetcher,
    InMemoryInsightStore,
    InsightSynthesizer,
    InsightValidator,
    SemanticMemoryWriter,
    StubLLMClient,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


class _FakeEmbeddings(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(8)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.fixture
def app_with_orchestrator() -> tuple[
    FastAPI, InMemoryExperienceStore, InMemoryInsightStore, StubLLMClient
]:
    memory_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    llm = StubLLMClient(
        responses=[
            {
                "narrative": "A reasonably long lesson learned from observed runs",
                "confidence": 0.85,
                "kind": "principle",
            }
        ]
    )
    writer = SemanticMemoryWriter(insight_store)
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(memory_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(llm),
        validator=InsightValidator(),
        writer=writer,
        insight_store=insight_store,
    )

    app = FastAPI()
    app.state.consolidation_orchestrator = orchestrator
    app.state.consolidation_insight_store = insight_store
    app.include_router(memory_router)
    return app, memory_store, insight_store, llm


async def _seed(
    store: InMemoryExperienceStore,
    count: int,
    *,
    task_type: str = "stress_check",
) -> None:
    for _ in range(count):
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id="r",
                step_id="s",
                agent_code="mech",
                task_type=task_type,
                success=True,
                result_summary=f"{task_type} run",
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.7,
                confidence=ConfidenceTier.VERBATIM,
            )
        )


def test_post_consolidate_runs_pipeline(app_with_orchestrator):
    import anyio

    app, memory_store, insight_store, _llm = app_with_orchestrator
    anyio.run(_seed, memory_store, 3)

    with TestClient(app) as client:
        response = client.post("/v1/memory/consolidate", json={"mode": "on_demand"})

    assert response.status_code == 200
    body = response.json()
    # camelCase aliases per the established convention.
    assert body["fetchedCount"] == 3
    assert body["acceptedCount"] == 1
    assert body["mode"] == "on_demand"

    listed = anyio.run(insight_store.list)
    assert len(listed) == 1


def test_post_consolidate_default_mode_is_on_demand(app_with_orchestrator):
    import anyio

    app, memory_store, _insight_store, _llm = app_with_orchestrator
    # Low-importance event the BACKGROUND pass would skip — ON_DEMAND must pick it up.
    for _ in range(2):
        anyio.run(memory_store.store, _low_importance_exp())

    with TestClient(app) as client:
        response = client.post("/v1/memory/consolidate", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "on_demand"
    assert body["fetchedCount"] == 2  # floor relaxed


def test_post_consolidate_503_when_orchestrator_unbound():
    app = FastAPI()
    app.include_router(memory_router)
    with TestClient(app) as client:
        response = client.post("/v1/memory/consolidate", json={"mode": "on_demand"})
    assert response.status_code == 503
    assert response.json()["detail"] == "consolidation_orchestrator_not_ready"


def test_post_consolidate_422_when_proactive_missing_project_id(app_with_orchestrator):
    app, _store, _insight_store, _llm = app_with_orchestrator
    with TestClient(app) as client:
        response = client.post(
            "/v1/memory/consolidate", json={"mode": "proactive"}
        )
    assert response.status_code == 422
    assert "project_id" in response.json()["detail"]


def test_post_consolidate_janitor_runs_without_synthesis(app_with_orchestrator):
    """JANITOR mode skips synthesis even when events exist."""
    import anyio

    app, memory_store, insight_store, llm = app_with_orchestrator
    anyio.run(_seed, memory_store, 3)

    with TestClient(app) as client:
        response = client.post("/v1/memory/consolidate", json={"mode": "janitor"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "janitor"
    assert body["synthesizedCount"] == 0
    # The synthesizer should not have been called.
    assert llm.calls == []


def test_post_consolidate_accepts_project_filter(app_with_orchestrator):
    import anyio

    app, memory_store, _insight_store, _llm = app_with_orchestrator
    project_id = UUID("11111111-1111-1111-1111-111111111111")
    for _ in range(2):
        anyio.run(
            memory_store.store,
            ExperienceMemory(
                id=uuid4(),
                run_id="r",
                step_id="s",
                agent_code="mech",
                task_type="stress_check",
                success=True,
                result_summary="stress run",
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.7,
                confidence=ConfidenceTier.VERBATIM,
                project_id=project_id,
            ),
        )

    with TestClient(app) as client:
        response = client.post(
            "/v1/memory/consolidate",
            json={"mode": "proactive", "projectId": str(project_id)},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["fetchedCount"] == 2


def _low_importance_exp() -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech",
        task_type="stress_check",
        success=True,
        result_summary="low-importance run",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.15,  # below DEFAULT_MIN_IMPORTANCE
        confidence=ConfidenceTier.VERBATIM,
    )
