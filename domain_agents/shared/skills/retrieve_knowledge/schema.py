"""Input/output schemas for the retrieve_knowledge skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeResult(BaseModel):
    """A single knowledge search result."""

    entry_id: str = Field(..., description="UUID of the knowledge entry")
    content: str = Field(..., description="The knowledge content text")
    knowledge_type: str = Field(..., description="Category of this knowledge")
    source: str = Field(..., description="Origin of this knowledge")
    score: float = Field(..., ge=0.0, le=1.0, description="Relevance score (0-1)")
    metadata: dict[str, str] = Field(
        default_factory=dict, description="Additional metadata"
    )


class RetrieveKnowledgeInput(BaseModel):
    """Input for the retrieve_knowledge skill."""

    query: str = Field(
        ..., min_length=1, description="Natural language search query"
    )
    knowledge_type: str | None = Field(
        default=None,
        description="Optional filter by knowledge type (e.g. 'design_rule', 'material_property')",
    )
    limit: int = Field(
        default=5, ge=1, le=50, description="Maximum number of results to return"
    )


class RetrieveKnowledgeOutput(BaseModel):
    """Output from the retrieve_knowledge skill."""

    results: list[KnowledgeResult] = Field(
        default_factory=list, description="Ranked knowledge results"
    )
    query: str = Field(..., description="The original query")
    total_results: int = Field(
        default=0, description="Number of results returned"
    )
