"""Knowledge Layer — semantic search and retrieval over design knowledge.

The L1 contract is the ``KnowledgeService`` Protocol (see ``service.py``)
and the ``create_knowledge_service`` factory below. Per ADR-008, all new
callers must depend on the Protocol — never on a concrete adapter.

The legacy ``KnowledgeStore`` / ``KnowledgeEntry`` API and
``PgVectorKnowledgeStore`` are still exported for backwards compatibility
with existing skills and consumers; they will be removed in the cleanup
PR after MET-307 lands.
"""

from typing import Any, Literal

from digital_twin.knowledge.chunker import TextChunker
from digital_twin.knowledge.consumer import KnowledgeConsumer
from digital_twin.knowledge.embedding_service import (
    EmbeddingService,
    LocalEmbeddingService,
    OpenAIEmbeddingService,
    create_embedding_service,
)
from digital_twin.knowledge.models import SearchQuery, SearchResult
from digital_twin.knowledge.service import (
    IngestResult,
    KnowledgeService,
    SearchHit,
)
from digital_twin.knowledge.store import (
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    KnowledgeStore,
    KnowledgeType,
    PgVectorKnowledgeStore,
)
from digital_twin.knowledge.templates import (
    render_component_selection,
    render_constraint_rationale,
    render_design_decision,
    render_failure_mode,
    render_session_summary,
    render_template,
)

__all__ = [
    "EmbeddingService",
    "InMemoryKnowledgeStore",
    "IngestResult",
    "KnowledgeConsumer",
    "KnowledgeEntry",
    "KnowledgeService",
    "KnowledgeStore",
    "KnowledgeType",
    "LocalEmbeddingService",
    "OpenAIEmbeddingService",
    "PgVectorKnowledgeStore",
    "SearchHit",
    "SearchQuery",
    "SearchResult",
    "TextChunker",
    "create_embedding_service",
    "create_knowledge_service",
    "render_component_selection",
    "render_constraint_rationale",
    "render_design_decision",
    "render_failure_mode",
    "render_session_summary",
    "render_template",
]


def create_knowledge_service(
    provider: Literal["lightrag", "llamaindex"] = "lightrag",
    **config: Any,
) -> KnowledgeService:
    """Factory for the L1 ``KnowledgeService``.

    Parameters
    ----------
    provider:
        ``"lightrag"`` (default) — production. ``"llamaindex"`` —
        documented fallback per ADR-008; raises ``NotImplementedError``
        until the adapter lands.
    **config:
        Forwarded to the chosen adapter's constructor (e.g.
        ``working_dir``, ``postgres_dsn``, ``embedding_model``).

    Returns
    -------
    KnowledgeService
        An adapter that satisfies the Protocol. Call ``initialize()``
        on the returned object before the first ingest/search.
    """
    if provider == "lightrag":
        from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService

        return LightRAGKnowledgeService(**config)
    if provider == "llamaindex":
        raise NotImplementedError(
            "llamaindex adapter is documented in ADR-008 but not implemented in MET-346. "
            "Add digital_twin/knowledge/llamaindex_service.py and wire it here when needed."
        )
    raise ValueError(f"Unknown knowledge provider: {provider!r}. Use 'lightrag' or 'llamaindex'.")
