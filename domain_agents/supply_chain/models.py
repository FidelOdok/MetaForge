"""Domain models for supply chain BOM risk scoring and alternate parts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.supply_chain.models")


class LifecycleStatus(str, Enum):
    """Component lifecycle status."""

    ACTIVE = "active"
    NRND = "nrnd"  # Not Recommended for New Designs
    EOL = "eol"  # End of Life
    OBSOLETE = "obsolete"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Risk level classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskFactor(BaseModel):
    """A single risk factor contributing to overall part risk."""

    name: str = Field(..., description="Risk factor name")
    weight: float = Field(..., ge=0.0, le=1.0, description="Factor weight (0-1)")
    score: int = Field(..., ge=0, le=100, description="Factor score (0-100)")
    description: str = Field(default="", description="Explanation of the score")


class PartRiskScore(BaseModel):
    """Risk assessment for a single part."""

    mpn: str = Field(..., description="Manufacturer Part Number")
    manufacturer: str = Field(default="", description="Part manufacturer")
    overall_score: int = Field(..., ge=0, le=100, description="Overall risk score (0-100)")
    risk_level: RiskLevel = Field(..., description="Risk level classification")
    factors: list[RiskFactor] = Field(default_factory=list, description="Individual risk factors")
    flagged: bool = Field(default=False, description="Whether this part is flagged for attention")


class BOMRiskReport(BaseModel):
    """Complete BOM risk assessment report."""

    project_id: str = Field(..., description="Project identifier")
    total_parts: int = Field(default=0, description="Total number of parts assessed")
    overall_score: int = Field(default=0, ge=0, le=100, description="Overall BOM risk score")
    critical_count: int = Field(default=0, description="Number of critical-risk parts")
    high_count: int = Field(default=0, description="Number of high-risk parts")
    medium_count: int = Field(default=0, description="Number of medium-risk parts")
    low_count: int = Field(default=0, description="Number of low-risk parts")
    part_scores: list[PartRiskScore] = Field(
        default_factory=list, description="Per-part risk scores"
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Report generation timestamp",
    )


class AlternatePart(BaseModel):
    """An alternate part suggestion."""

    mpn: str = Field(..., description="Manufacturer Part Number")
    manufacturer: str = Field(default="", description="Part manufacturer")
    compatibility_score: int = Field(
        ..., ge=0, le=100, description="Compatibility score (0-100)"
    )
    availability: str = Field(default="unknown", description="Availability status")
    price_comparison: str = Field(
        default="unknown", description="Price comparison vs original (lower/similar/higher)"
    )
    risk_reduction: int = Field(
        default=0, description="Estimated risk score reduction if substituted"
    )
    notes: str = Field(default="", description="Additional notes about this alternate")


class AlternatePartsResult(BaseModel):
    """Result of an alternate parts search."""

    original_mpn: str = Field(..., description="Original part MPN")
    original_risk_score: int = Field(
        default=0, ge=0, le=100, description="Risk score of the original part"
    )
    alternates: list[AlternatePart] = Field(
        default_factory=list, description="Ranked list of alternate parts"
    )
    recommendation: str = Field(
        default="", description="Overall recommendation for this part"
    )
