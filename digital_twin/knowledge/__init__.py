"""Knowledge Layer — semantic search and retrieval over design knowledge.

Provides embedding-based storage and retrieval of design decisions,
component datasheets, failure modes, constraints, and session insights.
"""

from digital_twin.knowledge.chunker import TextChunker
from digital_twin.knowledge.consumer import KnowledgeConsumer
from digital_twin.knowledge.embedding_service import (
    EmbeddingService,
    LocalEmbeddingService,
    OpenAIEmbeddingService,
    create_embedding_service,
)
from digital_twin.knowledge.models import SearchQuery, SearchResult
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
    "KnowledgeConsumer",
    "KnowledgeEntry",
    "KnowledgeStore",
    "KnowledgeType",
    "LocalEmbeddingService",
    "OpenAIEmbeddingService",
    "PgVectorKnowledgeStore",
    "SearchQuery",
    "SearchResult",
    "TextChunker",
    "create_embedding_service",
    "render_component_selection",
    "render_constraint_rationale",
    "render_design_decision",
    "render_failure_mode",
    "render_session_summary",
    "render_template",
]
