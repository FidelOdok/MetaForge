"""Render adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RenderConfig(BaseModel):
    """Configuration for the browser render adapter."""

    work_dir: str = Field(default="/workspace", description="Adapter workspace: models read here")
    three_dir: str = Field(
        default="/app/vendor/three",
        description="The three.js package shipped in the image (build/ and examples/jsm/)",
    )
    max_operation_time: int = Field(default=120, ge=5, description="Longest one render, seconds")
    max_input_mb: int = Field(default=100, ge=1, description="Largest GLB accepted, MB")
    max_concurrent: int = Field(default=2, ge=1, description="Renders run at the same time")
