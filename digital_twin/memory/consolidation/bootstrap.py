"""One construction path for the consolidation orchestrator (MET-723).

The full fetcher → grouper → synthesizer → validator → writer chain used to be
built inline in ``api_gateway/server.py``'s lifespan — about ninety lines of
pgvector / Neo4j / LLM wiring reachable from nowhere else. That was fine while
the gateway was the only process that ran a pass, and became a bug the moment
the Temporal worker started serving ``ConsolidationWorkflow``: the worker could
accept the workflow and then fail its activity with "orchestrator was not bound
before activity ran", because binding happened in a process it does not share.

So the wiring lives here, and both callers use it:

* the gateway lifespan, passing the experience store it already opened (so a
  second pgvector pool is never created for the same data);
* ``orchestrator.temporal_worker``, which has no such store and lets this
  build one from the environment.

Every backend choice degrades rather than raises — a consolidation tier that
cannot start must never take down the process hosting it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import structlog

from digital_twin.memory.consolidation.contradiction_detector import ContradictionDetector
from digital_twin.memory.consolidation.decay import ConfidenceDecay
from digital_twin.memory.consolidation.dual_write import DualWriteInsightStore
from digital_twin.memory.consolidation.fetcher import (
    EventFetcher,
    InMemoryEventFetcher,
    PgVectorEventFetcher,
)
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.llm import LLMClient, StubLLMClient
from digital_twin.memory.consolidation.neo4j_insight_store import Neo4jInsightStore
from digital_twin.memory.consolidation.openrouter import (
    OpenRouterConfig,
    OpenRouterError,
    OpenRouterLLMClient,
)
from digital_twin.memory.consolidation.orchestrator import ConsolidationOrchestrator
from digital_twin.memory.consolidation.pgvector_insight_store import PgVectorInsightStore
from digital_twin.memory.consolidation.synthesizer import InsightSynthesizer
from digital_twin.memory.consolidation.validator import InsightValidator
from digital_twin.memory.consolidation.writer import (
    InMemoryInsightStore,
    InsightStore,
    SemanticMemoryWriter,
)

logger = structlog.get_logger(__name__)


def _dsn() -> str | None:
    """The psycopg-style DSN, or ``None`` when no database is configured."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


@dataclass
class ConsolidationStack:
    """What a caller needs to run and later tear down a consolidation tier."""

    orchestrator: ConsolidationOrchestrator
    insight_store: InsightStore
    experience_store: Any = None
    #: Awaitables to run on shutdown, in order. Only the resources THIS factory
    #: opened appear here — a store handed in by the caller stays the caller's
    #: to close, so a gateway restart cannot close a pool it still uses.
    closers: list[Any] = field(default_factory=list)

    async def aclose(self) -> None:
        """Close everything this stack opened. Never raises."""
        for close in self.closers:
            try:
                await close()
            except Exception as exc:  # noqa: BLE001 — shutdown is best-effort
                logger.warning("consolidation_stack_close_failed", error=str(exc))


async def _build_experience_store() -> tuple[Any, list[Any]]:
    """Open an experience store from the environment."""
    from digital_twin.memory.pgvector_store import PgVectorExperienceStore
    from digital_twin.memory.store import InMemoryExperienceStore

    dsn = _dsn()
    if dsn:
        try:
            store = PgVectorExperienceStore(dsn=dsn)
            await store.initialize()
            logger.info("consolidation_experience_store_pgvector_initialized")
            return store, [store.close]
        except Exception as exc:  # noqa: BLE001
            logger.warning("consolidation_experience_store_pgvector_failed", error=str(exc))
    logger.info("consolidation_experience_store_in_memory_initialized")
    return InMemoryExperienceStore(), []


async def _build_insight_store() -> tuple[InsightStore, list[Any]]:
    """pgvector when configured, mirrored to Neo4j when that is too."""
    closers: list[Any] = []
    dsn = _dsn()
    pg_insight: PgVectorInsightStore | None = None
    if dsn:
        try:
            candidate = PgVectorInsightStore(dsn=dsn)
            await candidate.initialize()
            pg_insight = candidate
            closers.append(candidate.close)
            logger.info("consolidation_insight_store_pgvector_initialized")
        except Exception as exc:  # noqa: BLE001
            logger.warning("consolidation_insight_store_pgvector_failed", error=str(exc))

    if pg_insight is None:
        logger.info("consolidation_insight_store_in_memory_initialized")
        return InMemoryInsightStore(), closers

    # pgvector stays the read source of truth; Neo4j gets the structural
    # mirror. A Neo4j failure degrades to pgvector-only rather than failing.
    neo4j_uri = os.environ.get("NEO4J_URI") or os.environ.get("METAFORGE_NEO4J_URI")
    if not neo4j_uri:
        return pg_insight, closers
    try:
        neo4j_insight = Neo4jInsightStore(
            uri=neo4j_uri,
            user=os.environ.get("NEO4J_USER") or os.environ.get("METAFORGE_NEO4J_USER") or "neo4j",
            password=(
                os.environ.get("NEO4J_PASSWORD")
                or os.environ.get("METAFORGE_NEO4J_PASSWORD")
                or "password"
            ),
        )
        await neo4j_insight.connect()
        logger.info("consolidation_insight_store_dual_write_initialized")
        return DualWriteInsightStore(pg_insight, neo4j_insight), closers
    except Exception as exc:  # noqa: BLE001
        logger.warning("consolidation_insight_store_neo4j_failed", error=str(exc))
        return pg_insight, closers


def build_llm_client() -> LLMClient:
    """Open Router when configured, deterministic stub otherwise.

    The stub answers with ``confidence: 0.0``, which the validator rejects, so
    an unconfigured deployment runs passes that synthesise nothing rather than
    writing junk insights.
    """
    try:
        client = OpenRouterLLMClient(OpenRouterConfig.from_env())
        logger.info("consolidation_llm_open_router_initialized")
        return client
    except OpenRouterError as exc:
        logger.warning("consolidation_llm_open_router_skipped", reason=str(exc))
        return StubLLMClient()


def select_fetcher(experience_store: Any) -> EventFetcher:
    """Pick the fetcher that matches the store (MET-567).

    ``InMemoryEventFetcher`` snapshots ``InMemoryExperienceStore._experiences``,
    an attribute the pgvector store does not have — so pairing it with pgvector
    silently fetched zero experiences forever, however full the table was.
    """
    if hasattr(experience_store, "list_window"):
        logger.info("consolidation_fetcher_selected", backend="pgvector")
        return PgVectorEventFetcher(experience_store)
    logger.info("consolidation_fetcher_selected", backend="in_memory")
    return InMemoryEventFetcher(experience_store)


async def build_consolidation_stack(
    *,
    experience_store: Any = None,
    collector: Any = None,
    register_activities: bool = True,
) -> ConsolidationStack:
    """Assemble the consolidation tier from the environment.

    ``experience_store`` lets a caller that already has one (the gateway) reuse
    it instead of opening a second pool; omit it and one is built here.

    ``register_activities`` binds the orchestrator into the module-level
    Temporal activity holder so ``ConsolidationWorkflow``'s activity can run in
    THIS process. That is the whole point of the extraction: the worker needs
    the same binding the gateway performs, and could not reach this wiring
    while it lived in the gateway's lifespan.
    """
    closers: list[Any] = []
    store = experience_store
    if store is None:
        store, store_closers = await _build_experience_store()
        closers.extend(store_closers)

    insight_store, insight_closers = await _build_insight_store()
    closers.extend(insight_closers)

    llm_client = build_llm_client()
    orchestrator = ConsolidationOrchestrator(
        fetcher=select_fetcher(store),
        grouper=EventGrouper(),
        synthesizer=InsightSynthesizer(llm_client),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        decay=ConfidenceDecay(),
        janitor_marks_stale=True,
        contradiction_detector=ContradictionDetector(llm_client),
        collector=collector,
    )

    if register_activities:
        from digital_twin.memory.consolidation.workflow import register_consolidation_activities

        register_consolidation_activities(orchestrator)

    logger.info("consolidation_stack_built", registered_activities=register_activities)
    return ConsolidationStack(
        orchestrator=orchestrator,
        insight_store=insight_store,
        experience_store=store,
        closers=closers,
    )
