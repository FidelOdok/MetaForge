"""Gateway-boot wiring test for the consolidation orchestrator (MET-454).

The full gateway boot is heavy and pulls in Temporal / Neo4j / Kafka
clients. This test mirrors the relevant slice — the consolidation
construction block in ``api_gateway/server.py`` — to verify the
behaviour without booting uvicorn. The wiring code itself is import-
level on the gateway side, so this test exercises the same construction
path.
"""

from __future__ import annotations

import hashlib

import pytest

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.client import MemoryClient
from digital_twin.memory.consolidation import (
    ConsolidationOrchestrator,
    EventGrouper,
    InMemoryEventFetcher,
    InMemoryInsightStore,
    InsightSynthesizer,
    InsightValidator,
    OpenRouterConfig,
    OpenRouterError,
    OpenRouterLLMClient,
    SemanticMemoryWriter,
    StubLLMClient,
    register_consolidation_activities,
)
from digital_twin.memory.consolidation.llm import LLMClient
from digital_twin.memory.store import InMemoryExperienceStore


class _FakeEmbeddings(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(8)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


def _build_orchestrator(
    memory_store: InMemoryExperienceStore,
    llm: LLMClient,
) -> tuple[ConsolidationOrchestrator, InMemoryInsightStore]:
    """Mirrors the construction block in api_gateway/server.py."""
    insight_store = InMemoryInsightStore()
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(memory_store),
        grouper=EventGrouper(),
        synthesizer=InsightSynthesizer(llm),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
    )
    return orchestrator, insight_store


def test_orchestrator_construction_without_open_router(monkeypatch):
    """Default boot path: no OPEN_ROUTER_API_KEY → StubLLMClient."""
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)

    llm: LLMClient
    try:
        llm = OpenRouterLLMClient(OpenRouterConfig.from_env())
    except OpenRouterError:
        llm = StubLLMClient()
    assert isinstance(llm, StubLLMClient)

    memory_store = InMemoryExperienceStore()
    orchestrator, insight_store = _build_orchestrator(memory_store, llm)

    assert isinstance(orchestrator, ConsolidationOrchestrator)
    assert isinstance(insight_store, InMemoryInsightStore)


def test_orchestrator_construction_with_open_router(monkeypatch):
    """When OPEN_ROUTER_API_KEY is set, the synthesizer uses OpenRouterLLMClient."""
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-test")

    llm: LLMClient
    try:
        llm = OpenRouterLLMClient(OpenRouterConfig.from_env())
    except OpenRouterError:
        llm = StubLLMClient()
    assert isinstance(llm, OpenRouterLLMClient)


def test_register_consolidation_activities_binds_module_level_handle(monkeypatch):
    """Gateway boot must register the orchestrator so the Temporal activity runs."""
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)

    memory_store = InMemoryExperienceStore()
    orchestrator, _store = _build_orchestrator(memory_store, StubLLMClient())
    activities = register_consolidation_activities(orchestrator)
    assert activities.orchestrator is orchestrator


@pytest.mark.asyncio
async def test_orchestrator_runs_end_to_end_via_gateway_state():
    """Simulates the gateway calling consolidation_orchestrator.run() once memory is wired."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from digital_twin.memory.consolidation.modes import (
        ConsolidationMode,
        ConsolidationRunRequest,
    )
    from digital_twin.memory.models import ConfidenceTier, ExperienceMemory

    memory_store = InMemoryExperienceStore()
    memory_client = MemoryClient(memory_store, _FakeEmbeddings())
    assert memory_client is not None  # ensure construction works

    for _ in range(2):
        await memory_store.store(
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
            )
        )

    llm = StubLLMClient(
        responses=[
            {
                "narrative": "Stress validation consistently passes under nominal load",
                "confidence": 0.85,
                "kind": "principle",
            }
        ]
    )
    orchestrator, insight_store = _build_orchestrator(memory_store, llm)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    assert report.fetched_count == 2
    assert report.accepted_count == 1
    stored = await insight_store.list()
    assert len(stored) == 1
