"""Pydantic models for the knowledge subsystem."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class KnowledgeType(str, enum.Enum):
    """Categories of knowledge that can be stored and retrieved."""

    DESIGN_RULE = "design_rule"
    MATERIAL_PROPERTY = "material_property"
    COMPONENT_DATASHEET = "component_datasheet"
    BEST_PRACTICE = "best_practice"
    FAILURE_MODE = "failure_mode"
    STANDARD = "standard"
    CONSTRAINT = "constraint"
    LESSON_LEARNED = "lesson_learned"
    GENERAL = "general"


class KnowledgeEntry(BaseModel):
    """A single knowledge entry stored in the knowledge base."""

    id: UUID = Field(default_factory=uuid4, description="Unique entry identifier")
    content: str = Field(..., min_length=1, description="The knowledge content text")
    knowledge_type: KnowledgeType = Field(
        default=KnowledgeType.GENERAL, description="Category of this knowledge"
    )
    source: str = Field(default="unknown", description="Origin of this knowledge")
    metadata: dict[str, str] = Field(
        default_factory=dict, description="Additional metadata key-value pairs"
    )
    embedding: list[float] = Field(
        default_factory=list, description="Vector embedding of the content"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )

    @property
    def has_embedding(self) -> bool:
        """Whether this entry has a computed embedding."""
        return len(self.embedding) > 0


class SearchResult(BaseModel):
    """A knowledge entry with a relevance score from semantic search."""

    entry: KnowledgeEntry = Field(..., description="The matched knowledge entry")
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Similarity score (0-1, higher is better)"
    )
