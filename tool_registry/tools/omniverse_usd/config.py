"""OpenUSD conversion adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OmniverseUsdConfig(BaseModel):
    """Configuration for the OpenUSD conversion tool adapter."""

    work_dir: str = Field(
        default="/tmp/omniverse_usd", description="Working directory for converted stages"
    )
    default_meters_per_unit: float = Field(
        default=0.001,
        gt=0,
        description="Stage metersPerUnit to author when the caller doesn't specify one "
        "(0.001 = millimeters, matching MetaForge's STEP/GLB convention)",
    )
    stage_format: str = Field(
        default=".usda",
        description="Output USD layer format -- .usda (human-readable ASCII, diffable in "
        "git) by default; .usdc (binary) also supported",
    )
