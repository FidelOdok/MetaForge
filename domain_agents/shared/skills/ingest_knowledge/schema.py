"""Input/output schemas for the ingest_knowledge skill."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

from digital_twin.knowledge.types import KnowledgeType


class IngestKnowledgeInput(BaseModel):
    """Input for the ingest_knowledge skill.

    Field names and the ``knowledge_type`` values are the ones the
    ``knowledge.ingest`` MCP tool takes (FORGE-552). They used to be
    ``source`` and free text documented as ``design_rule`` /
    ``material_property``, values no store accepts, which the handler then
    silently filed as ``design_decision``.
    """

    content: str = Field(..., min_length=1, description="The text content to ingest")
    knowledge_type: KnowledgeType = Field(
        ...,
        description=(
            "Category: design_decision, component, failure, constraint or session "
            "(the knowledge.ingest enum); anything else is rejected"
        ),
    )
    source_path: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("source_path", "source"),
        description="Stable id of the source (a URL or path), the de-duplication key",
    )
    metadata: dict[str, str] | None = Field(
        default=None, description="Optional additional metadata key-value pairs"
    )


class IngestKnowledgeOutput(BaseModel):
    """Output from the ingest_knowledge skill."""

    entry_id: str = Field(..., description="UUID of the primary knowledge entry created")
    embedded: bool = Field(
        ...,
        description="Whether the content was embedded; unembedded entries are not searchable",
    )
    chunk_count: int = Field(default=1, ge=1, description="Number of chunks created")
    content_length: int = Field(default=0, ge=0, description="Total length of ingested content")
