"""The shared consolidation factory (MET-723).

The construction used to live inline in the gateway's lifespan — ~90 lines of
pgvector / Neo4j / LLM wiring reachable from nowhere else. That was fine until
the Temporal worker started serving ``ConsolidationWorkflow``: the worker could
accept the workflow and then fail its activity with "orchestrator was not bound
before activity ran", because the binding happened in a process it does not
share.

The two properties worth pinning are ownership of resources (a store handed in
must not be closed by the factory) and the activity binding (the entire reason
the extraction exists).
"""

from __future__ import annotations

import pytest

from digital_twin.memory.consolidation.bootstrap import (
    ConsolidationStack,
    build_consolidation_stack,
    build_llm_client,
    select_fetcher,
)
from digital_twin.memory.consolidation.fetcher import (
    InMemoryEventFetcher,
    PgVectorEventFetcher,
)
from digital_twin.memory.consolidation.llm import StubLLMClient
from digital_twin.memory.consolidation.writer import InMemoryInsightStore
from digital_twin.memory.store import InMemoryExperienceStore


@pytest.fixture(autouse=True)
def _no_backends(monkeypatch: pytest.MonkeyPatch):
    """Keep every test on in-memory backends unless it says otherwise."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("METAFORGE_NEO4J_URI", raising=False)
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)


class TestFetcherSelection:
    def test_a_windowed_store_gets_the_pgvector_fetcher(self):
        # MET-567's fatal pairing: the in-memory fetcher snapshots a private
        # dict the pgvector store does not have, so it fetched zero forever.
        class _Windowed:
            async def list_window(self, **kw):
                return []

        assert isinstance(select_fetcher(_Windowed()), PgVectorEventFetcher)

    def test_an_in_memory_store_gets_the_in_memory_fetcher(self):
        assert isinstance(select_fetcher(InMemoryExperienceStore()), InMemoryEventFetcher)


class TestLLMSelection:
    def test_falls_back_to_the_stub_without_a_key(self):
        # The stub answers confidence 0.0, which the validator rejects -- so an
        # unconfigured deployment synthesises nothing rather than writing junk.
        assert isinstance(build_llm_client(), StubLLMClient)


class TestStackConstruction:
    @pytest.mark.asyncio
    async def test_builds_a_usable_stack_with_no_backends(self):
        stack = await build_consolidation_stack(register_activities=False)

        assert isinstance(stack, ConsolidationStack)
        assert isinstance(stack.insight_store, InMemoryInsightStore)
        assert stack.orchestrator is not None

    @pytest.mark.asyncio
    async def test_a_supplied_store_is_reused_and_not_owned(self):
        # The gateway passes its already-open store so a second pgvector pool
        # is never created for the same data -- and closing the stack must not
        # close a pool the caller still uses.
        store = InMemoryExperienceStore()

        stack = await build_consolidation_stack(experience_store=store, register_activities=False)

        assert stack.experience_store is store
        assert stack.closers == []

    @pytest.mark.asyncio
    async def test_it_binds_the_temporal_activity(self):
        # THE reason this factory exists: the worker needs the same binding the
        # gateway performs, in its own process.
        from digital_twin.memory.consolidation.workflow import _DEFAULT_ACTIVITIES

        previous = _DEFAULT_ACTIVITIES.orchestrator
        try:
            stack = await build_consolidation_stack(register_activities=True)
            assert _DEFAULT_ACTIVITIES.orchestrator is stack.orchestrator
        finally:
            _DEFAULT_ACTIVITIES.orchestrator = previous

    @pytest.mark.asyncio
    async def test_binding_is_opt_out(self):
        from digital_twin.memory.consolidation.workflow import _DEFAULT_ACTIVITIES

        previous = _DEFAULT_ACTIVITIES.orchestrator
        _DEFAULT_ACTIVITIES.orchestrator = None
        try:
            await build_consolidation_stack(register_activities=False)
            assert _DEFAULT_ACTIVITIES.orchestrator is None
        finally:
            _DEFAULT_ACTIVITIES.orchestrator = previous

    @pytest.mark.asyncio
    async def test_a_broken_backend_degrades_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # A consolidation tier that cannot start must never take down the
        # process hosting it -- the gateway serves HTTP, the worker serves
        # agent workflows, and both matter more than a pass.
        monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/none")

        stack = await build_consolidation_stack(register_activities=False)

        assert isinstance(stack.insight_store, InMemoryInsightStore)
        assert stack.orchestrator is not None


class TestTeardown:
    @pytest.mark.asyncio
    async def test_aclose_runs_every_closer(self):
        called: list[str] = []

        async def _a() -> None:
            called.append("a")

        async def _b() -> None:
            called.append("b")

        stack = ConsolidationStack(
            orchestrator=object(),  # type: ignore[arg-type]
            insight_store=InMemoryInsightStore(),
            closers=[_a, _b],
        )
        await stack.aclose()

        assert called == ["a", "b"]

    @pytest.mark.asyncio
    async def test_a_failing_closer_does_not_block_the_others(self):
        called: list[str] = []

        async def _boom() -> None:
            raise RuntimeError("pool already gone")

        async def _ok() -> None:
            called.append("ok")

        stack = ConsolidationStack(
            orchestrator=object(),  # type: ignore[arg-type]
            insight_store=InMemoryInsightStore(),
            closers=[_boom, _ok],
        )
        await stack.aclose()  # must not raise

        assert called == ["ok"]
