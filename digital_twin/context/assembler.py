"""``ContextAssembler`` — orchestrates Twin + KnowledgeService context (MET-315).

Behaviour summary:

* The request's ``scope`` list selects which sub-collectors to run
  (knowledge, graph, work_product). ``ContextScope.ALL`` enables every
  collector.
* Each collector emits ``ContextFragment`` rows tagged with their
  origin (``ContextSourceKind``) and source identifier.
* After collection, fragments are sorted by relevance signal
  (similarity score for knowledge hits; structural distance for graph
  hits; pivot first for work_product) and truncated to the token
  budget. Dropped fragment ids land in ``dropped_source_ids`` so the
  caller can surface "context truncated" UX.
* Token counts are heuristic for now (see ``estimate_tokens``). MET-317
  will swap in tiktoken + a smarter priority score.
* Every public method emits a structlog record + an OTel span; metrics
  hook follows the project's observability taxonomy.

Constraints honoured:

* ``digital_twin`` may import only from ``twin_core``, ``observability``,
  and stdlib-equivalents (per the package's CLAUDE.md). No
  ``orchestrator`` / ``api_gateway`` dependencies.
* The assembler only knows about ``KnowledgeService`` (Protocol) and
  ``TwinAPI`` (abstract base) — never about the concrete LightRAG /
  Neo4j implementations. Swapping providers is a one-liner at the
  caller.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from digital_twin.context.models import (
    ContextAssemblyRequest,
    ContextAssemblyResponse,
    ContextFragment,
    ContextSourceKind,
    estimate_tokens,
    fragment_priority,
)
from digital_twin.context.role_scope import get_role_knowledge_types
from digital_twin.context.staleness import (
    annotate_cross_fragment_staleness,
    compute_staleness,
)
from digital_twin.knowledge.service import KnowledgeService, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from observability.tracing import get_tracer
from twin_core.api import TwinAPI
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.context.assembler")


_GRAPH_NODE_PRIORITY = 0.95
"""Synthetic relevance score for the pivot work_product node.

Sits just below 1.0 so an identical-content knowledge hit (which would
score 1.0) still wins the tie-break, but higher than typical knowledge
hit scores (~0.5–0.8) so structural pivots survive the budget cut.
"""

_GRAPH_EDGE_PRIORITY = 0.55
"""Synthetic relevance score for graph edges traversed from the pivot."""


class ContextAssembler:
    """Assemble attributed context from the Twin + KnowledgeService."""

    def __init__(
        self,
        twin: TwinAPI,
        knowledge_service: KnowledgeService,
    ) -> None:
        self._twin = twin
        self._knowledge_service = knowledge_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assemble(self, request: ContextAssemblyRequest) -> ContextAssemblyResponse:
        """Run every requested collector and return a budgeted response.

        Order of operations:

        1. Knowledge collector (if requested) — semantic hits via
           ``KnowledgeService.search``.
        2. Work-product collector (if requested) — pivot node fetched
           from the Twin.
        3. Graph collector (if requested) — neighbours of the pivot,
           keyed by edge type.
        4. Sort + budget enforcement.
        """
        with tracer.start_as_current_span("context.assemble") as span:
            span.set_attribute("context.agent_id", request.agent_id)
            span.set_attribute("context.scope", ",".join(s.value for s in request.scope))
            span.set_attribute("context.token_budget", request.token_budget)

            collected: list[ContextFragment] = []

            if request.includes_knowledge and request.query:
                collected.extend(await self._collect_knowledge(request))

            if request.includes_work_product and request.work_product_id is not None:
                collected.extend(await self._collect_work_product(request))

            if request.includes_graph and request.work_product_id is not None:
                collected.extend(await self._collect_graph(request))

            # MET-323: staleness annotation + threshold filtering runs
            # *before* ranking so the budget pass works on a fresh-only
            # set when the caller asked for one.
            annotate_cross_fragment_staleness(collected)
            stale_dropped: list[ContextFragment] = []
            after_staleness: list[ContextFragment] = []
            for fragment in collected:
                fragment.staleness_score = compute_staleness(fragment.metadata)
                if fragment.staleness_score > request.staleness_threshold:
                    stale_dropped.append(fragment)
                else:
                    after_staleness.append(fragment)

            sorted_fragments = self._rank(after_staleness)
            kept, dropped = self._enforce_budget(sorted_fragments, request.token_budget)

            sources: dict[str, int] = {}
            for fragment in kept:
                sources[fragment.source_kind.value] = sources.get(fragment.source_kind.value, 0) + 1

            response = ContextAssemblyResponse(
                fragments=kept,
                token_count=sum(f.token_count for f in kept),
                truncated=bool(dropped),
                dropped_source_ids=[f.source_id for f in dropped],
                sources=sources,
                metadata={
                    "agent_id": request.agent_id,
                    "scope": [s.value for s in request.scope],
                    "knowledge_top_k": request.knowledge_top_k,
                    "graph_depth": request.graph_depth,
                    "staleness_threshold": request.staleness_threshold,
                    "stale_dropped_count": len(stale_dropped),
                    "stale_dropped_ids": [f.source_id for f in stale_dropped],
                },
            )
            span.set_attribute("context.fragment_count", len(response.fragments))
            span.set_attribute("context.token_count", response.token_count)
            span.set_attribute("context.truncated", response.truncated)
            if response.truncated:
                # Per-source-kind tally of dropped fragments — feeds
                # MET-326 retrieval-quality metrics without coupling.
                dropped_sources: dict[str, int] = {}
                for fragment in dropped:
                    dropped_sources[fragment.source_kind.value] = (
                        dropped_sources.get(fragment.source_kind.value, 0) + 1
                    )
                logger.info(
                    "context_truncated",
                    agent_id=request.agent_id,
                    token_budget=request.token_budget,
                    token_count=response.token_count,
                    dropped_count=len(dropped),
                    dropped_sources=dropped_sources,
                )
            logger.info(
                "context_assembled",
                agent_id=request.agent_id,
                fragments=len(response.fragments),
                token_count=response.token_count,
                truncated=response.truncated,
                dropped=len(response.dropped_source_ids),
            )
            return response

    # ------------------------------------------------------------------
    # Sub-collectors
    # ------------------------------------------------------------------

    async def _collect_knowledge(self, request: ContextAssemblyRequest) -> list[ContextFragment]:
        """Collect semantic hits with optional role-based scoping (MET-316).

        Filter precedence:

        1. ``request.knowledge_type`` (caller-explicit) — wins always.
        2. Role map (via ``request.agent_id``) — narrows to the role's
           allow-list when no caller-explicit filter is set.
        3. No filter — back-compat with the pre-MET-316 contract.

        Role narrowing is post-applied so a single ``search`` call
        returns hits across the role's union of types; we just over-
        fetch (``top_k * 4``) and drop anything outside the allow-list.
        Avoids one ``search`` round-trip per allowed type.
        """
        if not request.query:
            return []
        with tracer.start_as_current_span("context.collect_knowledge") as span:
            span.set_attribute("context.query_length", len(request.query))
            span.set_attribute("context.agent_id", request.agent_id)

            allowed_types: frozenset[KnowledgeType] | None = None
            if request.knowledge_type is None:
                allowed_types = get_role_knowledge_types(request.agent_id)

            # Caller-explicit filter wins; otherwise role-narrow if known.
            if request.knowledge_type is not None:
                explicit_type: KnowledgeType | None = request.knowledge_type
                fetch_k = request.knowledge_top_k
            elif allowed_types is not None:
                explicit_type = None
                fetch_k = request.knowledge_top_k * 4
                span.set_attribute(
                    "context.role_filter",
                    ",".join(sorted(t.value for t in allowed_types)),
                )
            else:
                explicit_type = None
                fetch_k = request.knowledge_top_k

            try:
                hits = await self._knowledge_service.search(
                    query=request.query,
                    top_k=fetch_k,
                    knowledge_type=explicit_type,
                    filters=request.filters or None,
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("context_knowledge_search_failed", error=str(exc))
                return []

            if allowed_types is not None:
                hits = [
                    h for h in hits if h.knowledge_type is None or h.knowledge_type in allowed_types
                ]
                hits = hits[: request.knowledge_top_k]

            return [self._hit_to_fragment(hit) for hit in hits]

    async def _collect_work_product(self, request: ContextAssemblyRequest) -> list[ContextFragment]:
        wp_id = request.work_product_id
        if wp_id is None:
            return []
        with tracer.start_as_current_span("context.collect_work_product") as span:
            span.set_attribute("context.work_product_id", str(wp_id))
            try:
                wp = await self._twin.get_work_product(wp_id)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning(
                    "context_work_product_fetch_failed",
                    work_product_id=str(wp_id),
                    error=str(exc),
                )
                return []
            if wp is None:
                logger.debug("context_work_product_missing", work_product_id=str(wp_id))
                return []
            return [self._work_product_to_fragment(wp)]

    async def _collect_graph(self, request: ContextAssemblyRequest) -> list[ContextFragment]:
        wp_id = request.work_product_id
        if wp_id is None or request.graph_depth == 0:
            return []
        with tracer.start_as_current_span("context.collect_graph") as span:
            span.set_attribute("context.work_product_id", str(wp_id))
            span.set_attribute("context.graph_depth", request.graph_depth)
            try:
                subgraph = await self._twin.get_subgraph(wp_id, depth=request.graph_depth)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("context_graph_fetch_failed", error=str(exc))
                return []

            fragments: list[ContextFragment] = []
            seen_node_ids: set[UUID] = {wp_id}
            for node in getattr(subgraph, "nodes", []) or []:
                node_id = getattr(node, "id", None)
                if node_id is None or node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                fragments.append(self._graph_node_to_fragment(node))
            for edge in getattr(subgraph, "edges", []) or []:
                fragments.append(self._graph_edge_to_fragment(edge))
            return fragments

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hit_to_fragment(hit: SearchHit) -> ContextFragment:
        return ContextFragment(
            content=hit.content,
            source_kind=ContextSourceKind.KNOWLEDGE_HIT,
            source_id=hit.source_path or f"knowledge://chunk/{hit.chunk_index}",
            source_path=hit.source_path,
            heading=hit.heading,
            chunk_index=hit.chunk_index,
            total_chunks=hit.total_chunks,
            similarity_score=hit.similarity_score,
            knowledge_type=hit.knowledge_type,
            work_product_id=hit.source_work_product_id,
            metadata=dict(hit.metadata or {}),
            token_count=estimate_tokens(hit.content),
        )

    @staticmethod
    def _work_product_to_fragment(wp: WorkProduct) -> ContextFragment:
        body = (
            f"WorkProduct: {wp.name}\n"
            f"  type={wp.type}\n"
            f"  domain={wp.domain}\n"
            f"  file_path={wp.file_path}\n"
            f"  format={wp.format}\n"
            f"  content_hash={wp.content_hash}\n"
        )
        return ContextFragment(
            content=body,
            source_kind=ContextSourceKind.GRAPH_NODE,
            source_id=f"work_product://{wp.id}",
            source_path=wp.file_path,
            similarity_score=_GRAPH_NODE_PRIORITY,
            work_product_id=wp.id,
            metadata={
                "node_type": "work_product",
                "wp_type": str(wp.type),
                "domain": wp.domain,
                **(wp.metadata or {}),
            },
            token_count=estimate_tokens(body),
        )

    @staticmethod
    def _graph_node_to_fragment(node: Any) -> ContextFragment:
        node_id = getattr(node, "id", None)
        name = getattr(node, "name", None) or getattr(node, "label", None) or "node"
        node_type = getattr(node, "node_type", None)
        body = f"Graph node {name} (id={node_id}, type={node_type})"
        return ContextFragment(
            content=body,
            source_kind=ContextSourceKind.GRAPH_NODE,
            source_id=f"graph://{node_id}",
            similarity_score=_GRAPH_NODE_PRIORITY * 0.85,
            work_product_id=node_id if isinstance(node_id, UUID) else None,
            metadata={"node_type": str(node_type) if node_type else None},
            token_count=estimate_tokens(body),
        )

    @staticmethod
    def _graph_edge_to_fragment(edge: Any) -> ContextFragment:
        source = getattr(edge, "source_id", None)
        target = getattr(edge, "target_id", None)
        edge_type = getattr(edge, "edge_type", None)
        body = f"Graph edge {edge_type}: {source} -> {target}"
        return ContextFragment(
            content=body,
            source_kind=ContextSourceKind.GRAPH_EDGE,
            source_id=f"edge://{source}/{edge_type}/{target}",
            similarity_score=_GRAPH_EDGE_PRIORITY,
            metadata={"edge_type": str(edge_type) if edge_type else None},
            token_count=estimate_tokens(body),
        )

    # ------------------------------------------------------------------
    # Ranking + budget
    # ------------------------------------------------------------------

    @staticmethod
    def _rank(fragments: list[ContextFragment]) -> list[ContextFragment]:
        """Sort fragments by composite priority, highest first (MET-317).

        Composite key, descending::

            priority = recency × authority × relevance

        See ``digital_twin.context.models.fragment_priority`` for the
        component definitions. Tie-break order:

        1. Higher composite priority wins.
        2. Newer ``metadata["created_at"]`` wins (deterministic given
           timestamps; 0 when absent).
        3. Smaller ``token_count`` wins so a budget cap retains more
           fragments overall.
        """

        def _sort_key(f: ContextFragment) -> tuple[float, float, int]:
            priority = fragment_priority(
                source_kind=f.source_kind.value,
                similarity=f.similarity_score,
                metadata=f.metadata,
            )
            ts_raw = f.metadata.get("created_at", 0) or 0
            ts: float
            if isinstance(ts_raw, int | float):
                ts = float(ts_raw)
            else:
                from datetime import datetime

                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    ts = 0.0
            return (-priority, -ts, f.token_count)

        return sorted(fragments, key=_sort_key)

    @staticmethod
    def _enforce_budget(
        fragments: list[ContextFragment], budget: int
    ) -> tuple[list[ContextFragment], list[ContextFragment]]:
        """Greedily keep fragments while total tokens ≤ budget."""
        kept: list[ContextFragment] = []
        dropped: list[ContextFragment] = []
        used = 0
        for fragment in fragments:
            if used + fragment.token_count <= budget:
                kept.append(fragment)
                used += fragment.token_count
            else:
                dropped.append(fragment)
        return kept, dropped
