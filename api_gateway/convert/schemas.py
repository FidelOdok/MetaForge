"""Pydantic schemas for the CAD conversion API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConversionRequest(BaseModel):
    """Query parameters for a conversion request."""

    quality: str = Field(
        default="standard",
        pattern="^(preview|standard|fine)$",
        description="Tessellation quality tier.",
    )


class ConversionResult(BaseModel):
    """Response payload for a completed conversion."""

    hash: str = Field(description="SHA-256 content hash of the source file.")
    glb_url: str = Field(description="URL to download the GLB file.")
    metadata: dict[str, Any] = Field(description="Part tree, stats, materials.")
    cached: bool = Field(description="True if result was served from cache.")


class ConversionJob(BaseModel):
    """Status of a conversion job (for future async support)."""

    job_id: str
    status: str = Field(description="pending | running | completed | failed")
    result: ConversionResult | None = None
