"""Pydantic v2 models for the knowledge pipeline.

Re-exports core types from ``store.py`` and adds search-specific models
(``SearchResult``, ``SearchQuery``) used by the API layer and consumers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from digital_twin.knowledge.store import KnowledgeEntry, KnowledgeType

# Re-export for convenience
__all__ = [
    "KnowledgeEntry",
    "KnowledgeType",
    "SearchQuery",
    "SearchResult",
]


class SearchResult(BaseModel):
    """A knowledge entry paired with its cosine similarity score."""

    entry: KnowledgeEntry = Field(..., description="The matched knowledge entry")
    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine similarity score (0.0 to 1.0)",
    )


class SearchQuery(BaseModel):
    """Parameters for a semantic search over the knowledge store."""

    query: str = Field(..., min_length=1, description="Free-text search query")
    knowledge_type: KnowledgeType | None = Field(
        default=None,
        description="Optional filter by knowledge type",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return",
    )
    metadata_filter: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata key-value filter",
    )
    source_work_product_id: UUID | None = Field(
        default=None,
        description="Optional filter to entries linked to a specific work_product",
    )
