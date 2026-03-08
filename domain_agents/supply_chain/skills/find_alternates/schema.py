"""Input/output schemas for the find_alternates skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from domain_agents.supply_chain.models import AlternatePartsResult


class FindAlternatesInput(BaseModel):
    """Input for the find_alternates skill."""

    mpn: str = Field(..., description="Original manufacturer part number")
    specs: dict[str, Any] = Field(
        default_factory=dict,
        description="Original part specifications (package, voltage_rating, etc.)",
    )
    distributor_results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate parts from distributor search",
    )


class FindAlternatesOutput(BaseModel):
    """Output from the find_alternates skill."""

    result: AlternatePartsResult = Field(
        ..., description="Ranked alternate parts result"
    )
