"""Blender adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BlenderConfig(BaseModel):
    """Configuration for the Blender render adapter."""

    blender_binary: str = Field(default="blender", description="Path to the Blender executable")
    work_dir: str = Field(default="/workspace", description="Shared workspace models are read from")
    timeout_seconds: int = Field(default=600, ge=10, description="Wall-clock limit per job")
    max_pixels: int = Field(
        default=3840 * 2160, ge=64 * 64, description="Largest render (width x height)"
    )
    max_images: int = Field(default=36, ge=1, description="Most images one job may render")
