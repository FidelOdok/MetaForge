"""Input/output schemas for the retrieve_knowledge skill."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field

from digital_twin.knowledge.types import KnowledgeType


class KnowledgeResult(BaseModel):
    """A single knowledge search result."""

    entry_id: str = Field(..., description="UUID of the knowledge entry")
    content: str = Field(..., description="The knowledge content text")
    knowledge_type: str = Field(..., description="Category of this knowledge")
    source_path: str = Field(
        default="", description="Source the entry was ingested from, for citation"
    )
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Cosine similarity to the query, clamped to 0-1"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class RetrieveKnowledgeInput(BaseModel):
    """Input for the retrieve_knowledge skill.

    Names follow the ``knowledge.search`` MCP tool (``top_k``, the
    ``knowledge_type`` enum); ``limit`` is still accepted (FORGE-552).
    """

    query: str = Field(..., min_length=1, description="Natural language search query")
    knowledge_type: KnowledgeType | None = Field(
        default=None,
        description=("Optional filter: design_decision, component, failure, constraint or session"),
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        validation_alias=AliasChoices("top_k", "limit"),
        description="Maximum number of results to return",
    )


class RetrieveKnowledgeOutput(BaseModel):
    """Output from the retrieve_knowledge skill."""

    results: list[KnowledgeResult] = Field(
        default_factory=list, description="Ranked knowledge results"
    )
    query: str = Field(..., description="The original query")
    total_results: int = Field(default=0, description="Number of results returned")
