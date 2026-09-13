"""Input/output schemas for the analyze_hazards skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Hazard(BaseModel):
    """A single hazard entry (ISO 12100 / ISO 14971 style)."""

    hazard: str = Field(..., min_length=1, description="What can go wrong")
    cause: str = Field(..., min_length=1, description="What triggers it")
    effect: str = Field(..., min_length=1, description="What happens if it isn't mitigated")
    severity: int = Field(..., ge=1, le=5, description="1 (negligible) - 5 (catastrophic)")
    likelihood: int = Field(..., ge=1, le=5, description="1 (improbable) - 5 (frequent)")
    mitigation: str = Field(default="", description="Mitigation in place, if any")


class AnalyzeHazardsInput(BaseModel):
    """Input for the hazard-analysis skill."""

    project_id: str = Field(..., min_length=1, description="Project identifier")
    system_name: str = Field(..., min_length=1, description="System or subsystem under analysis")
    hazards: list[Hazard] = Field(..., min_length=1, description="Hazards to log and score")


class AnalyzeHazardsOutput(BaseModel):
    """Output from the hazard-analysis skill."""

    node_id: str = Field(..., description="HAZARD_ANALYSIS work-product node id")
    hazard_count: int = Field(..., ge=0)
    highest_risk_score: int = Field(..., ge=0, description="max(severity x likelihood)")
    overall_risk_level: str = Field(..., description="low | medium | high | critical")
    unmitigated_count: int = Field(..., ge=0, description="Hazards with no mitigation entered")
