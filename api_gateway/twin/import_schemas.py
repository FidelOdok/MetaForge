"""Request/response schemas for the work product import API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ImportWorkProductResponse(BaseModel):
    """Response from a successful work product import."""

    id: str = Field(..., description="WorkProduct UUID")
    name: str = Field(..., description="WorkProduct name")
    domain: str = Field(..., description="Domain (mechanical, electronics, etc.)")
    wp_type: str = Field(..., description="WorkProduct type")
    file_path: str = Field(..., description="Stored file path")
    content_hash: str = Field(..., description="SHA-256 of file content")
    format: str = Field(..., description="File format (step, kicad_sch, etc.)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extracted metadata")
    project_id: str | None = Field(None, description="Linked project ID")
    created_at: str = Field(..., description="ISO-8601 creation timestamp")
