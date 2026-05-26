"""Neo4j backend for ``InsightStore`` — the structural half of the dual write.

pgvector (``PgVectorInsightStore``) owns the embedded narrative for
semantic search; Neo4j owns the *structure* — insights as
``(:ConsolidationInsight)`` nodes that downstream graph queries can walk
(e.g. "which insights cite experiences from run X"). This adapter writes
the node + its scalar properties; relationship edges to experience /
component nodes are a follow-up once the experience graph lands in
Neo4j.

Driver access mirrors ``twin_core.neo4j_graph_engine.Neo4jGraphEngine``:
the ``neo4j`` package is imported lazily so environments without it (or
without a live database) still import this module. ``connect()`` is the
explicit lifecycle hook; ``write`` / ``list`` / ``get`` raise if called
before connecting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InsightStore
from digital_twin.memory.models import ConfidenceTier
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.neo4j_insight_store")

_NODE_LABEL = "ConsolidationInsight"


class Neo4jInsightStoreError(RuntimeError):
    """Raised on connection / driver failures."""


class Neo4jInsightStore(InsightStore):
    """Neo4j-backed ``InsightStore``.

    Use ``await store.connect()`` at gateway boot to open the driver and
    ensure the uniqueness constraint on insight id. Pair with
    ``close()`` on shutdown.
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        database: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Any = None
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            import neo4j
        except ImportError as exc:  # pragma: no cover — env-dependent
            raise Neo4jInsightStoreError(
                "neo4j package is not installed. Install with: pip install metaforge[neo4j]"
            ) from exc

        with tracer.start_as_current_span("neo4j_insight.connect") as span:
            span.set_attribute("db.system", "neo4j")
            span.set_attribute("db.uri", self._uri)
            try:
                self._driver = neo4j.AsyncGraphDatabase.driver(
                    self._uri,
                    auth=(self._user, self._password),
                )
                await self._driver.verify_connectivity()
                self._connected = True
                async with self._driver.session(database=self._database) as session:
                    await session.run(
                        f"CREATE CONSTRAINT consolidation_insight_id IF NOT EXISTS "
                        f"FOR (n:{_NODE_LABEL}) REQUIRE n.id IS UNIQUE"
                    )
                logger.info("neo4j_insight_store_connected", uri=self._uri)
            except Exception as exc:
                span.record_exception(exc)
                self._connected = False
                raise Neo4jInsightStoreError(
                    f"Failed to connect to Neo4j at {self._uri}: {exc}"
                ) from exc

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._connected = False
            logger.info("neo4j_insight_store_disconnected", uri=self._uri)

    def _require_driver(self) -> Any:
        if self._driver is None or not self._connected:
            raise Neo4jInsightStoreError(
                "Neo4jInsightStore.connect() must be called before use"
            )
        return self._driver

    # ------------------------------------------------------------------
    # InsightStore impl
    # ------------------------------------------------------------------

    async def write(self, insight: Insight) -> Insight:
        with tracer.start_as_current_span("neo4j_insight.write") as span:
            span.set_attribute("memory.insight_id", str(insight.id))
            span.set_attribute("memory.theme", insight.theme.value)
            driver = self._require_driver()
            props = _insight_to_props(insight)
            async with driver.session(database=self._database) as session:
                await session.run(
                    f"""
                    MERGE (n:{_NODE_LABEL} {{id: $id}})
                    SET n += $props
                    """,
                    id=str(insight.id),
                    props=props,
                )
            logger.info(
                "neo4j_insight_stored",
                insight_id=str(insight.id),
                theme=insight.theme.value,
            )
            return insight

    async def list(
        self,
        *,
        theme: ConsolidationTheme | None = None,
        limit: int = 50,
    ) -> list[Insight]:
        with tracer.start_as_current_span("neo4j_insight.list") as span:
            driver = self._require_driver()
            if theme is None:
                query = (
                    f"MATCH (n:{_NODE_LABEL}) "
                    "RETURN n ORDER BY n.synthesized_at DESC LIMIT $limit"
                )
                params: dict[str, Any] = {"limit": limit}
            else:
                query = (
                    f"MATCH (n:{_NODE_LABEL} {{theme: $theme}}) "
                    "RETURN n ORDER BY n.synthesized_at DESC LIMIT $limit"
                )
                params = {"theme": theme.value, "limit": limit}
            async with driver.session(database=self._database) as session:
                result = await session.run(query, **params)
                records = [record async for record in result]
            insights = [_node_to_insight(dict(record["n"])) for record in records]
            span.set_attribute("memory.result_count", len(insights))
            return insights

    async def get(self, insight_id: UUID) -> Insight | None:
        driver = self._require_driver()
        async with driver.session(database=self._database) as session:
            result = await session.run(
                f"MATCH (n:{_NODE_LABEL} {{id: $id}}) RETURN n",
                id=str(insight_id),
            )
            record = await result.single()
        if record is None:
            return None
        return _node_to_insight(dict(record["n"]))


def _insight_to_props(insight: Insight) -> dict[str, Any]:
    """Flatten an ``Insight`` into Neo4j-storable scalar properties.

    Neo4j can't store UUIDs or nested objects, so everything is coerced
    to strings / string-lists / scalars. ``supporting_experience_ids``
    becomes a ``list[str]`` (Neo4j supports homogeneous primitive
    arrays).
    """
    return {
        "id": str(insight.id),
        "theme": insight.theme.value,
        "kind": insight.kind.value,
        "narrative": insight.narrative,
        "supporting_experience_ids": [str(uid) for uid in insight.supporting_experience_ids],
        "confidence": insight.confidence,
        "confidence_tier": insight.confidence_tier.value,
        "synthesized_at": insight.synthesized_at.isoformat(),
    }


def _node_to_insight(node: dict[str, Any]) -> Insight:
    """Reconstruct an ``Insight`` from a Neo4j node's property map."""
    supporting = [
        item if isinstance(item, UUID) else UUID(str(item))
        for item in (node.get("supporting_experience_ids") or [])
    ]
    return Insight(
        id=UUID(str(node["id"])),
        theme=ConsolidationTheme(node["theme"]),
        kind=InsightKind(node["kind"]),
        narrative=node["narrative"],
        supporting_experience_ids=supporting,
        confidence=node["confidence"],
        confidence_tier=ConfidenceTier(node["confidence_tier"]),
        synthesized_at=_parse_timestamp(node.get("synthesized_at")),
    )


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts
