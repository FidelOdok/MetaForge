"""Input/output schemas for the ingest_knowledge skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestKnowledgeInput(BaseModel):
    """Input for the ingest_knowledge skill."""

    content: str = Field(..., min_length=1, description="The text content to ingest")
    knowledge_type: str = Field(
        ...,
        min_length=1,
        description="Category of this knowledge (e.g. 'design_rule', 'material_property')",
    )
    source: str = Field(..., min_length=1, description="Origin/source of this knowledge")
    metadata: dict[str, str] | None = Field(
        default=None, description="Optional additional metadata key-value pairs"
    )


class IngestKnowledgeOutput(BaseModel):
    """Output from the ingest_knowledge skill."""

    entry_id: str = Field(..., description="UUID of the primary knowledge entry created")
    embedded: bool = Field(..., description="Whether the content was successfully embedded")
    chunk_count: int = Field(default=1, ge=1, description="Number of chunks created")
    content_length: int = Field(default=0, ge=0, description="Total length of ingested content")
