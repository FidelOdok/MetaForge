"""Data models for the compliance domain agent.

Defines enums for regimes, evidence status/types, and Pydantic models
for checklist items, checklists, and evidence records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ComplianceRegime(StrEnum):
    """Supported regulatory compliance regimes."""

    UKCA = "UKCA"
    CE = "CE"
    FCC = "FCC"
    PSTI = "PSTI"


class EvidenceStatus(StrEnum):
    """Lifecycle status of a piece of compliance evidence."""

    MISSING = "MISSING"
    UPLOADED = "UPLOADED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"


class EvidenceType(StrEnum):
    """Types of compliance evidence artifacts."""

    TEST_REPORT = "TEST_REPORT"
    DECLARATION = "DECLARATION"
    CERTIFICATE = "CERTIFICATE"
    TECHNICAL_FILE = "TECHNICAL_FILE"
    RISK_ASSESSMENT = "RISK_ASSESSMENT"


class ChecklistItem(BaseModel):
    """A single requirement within a compliance checklist."""

    id: str = Field(..., description="Unique identifier e.g. UKCA-SAF-001")
    regime: ComplianceRegime = Field(..., description="Regulatory regime this item belongs to")
    category: str = Field(..., description="Category within the regime (e.g. safety, EMC)")
    requirement: str = Field(..., description="Human-readable requirement description")
    standard: str = Field(..., description="Reference standard (e.g. EN 62368-1:2020)")
    evidence_type: EvidenceType = Field(..., description="Type of evidence required")
    evidence_status: EvidenceStatus = Field(
        default=EvidenceStatus.MISSING, description="Current evidence status"
    )
    evidence_artifact_id: UUID | None = Field(
        default=None, description="UUID of linked evidence artifact"
    )
    notes: str = Field(default="", description="Free-form notes")


class ComplianceChecklist(BaseModel):
    """A generated compliance checklist for a project."""

    project_id: str = Field(..., description="Project identifier")
    product_category: str = Field(default="consumer_electronics", description="Product category")
    target_markets: list[ComplianceRegime] = Field(..., description="Target market regimes")
    items: list[ChecklistItem] = Field(default_factory=list, description="Checklist items")
    total_items: int = Field(default=0, description="Total number of items")
    evidenced_items: int = Field(default=0, description="Items with evidence uploaded or better")
    coverage_percent: float = Field(default=0.0, description="Evidence coverage percentage")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the checklist was generated",
    )


class ComplianceEvidence(BaseModel):
    """A piece of compliance evidence linked to a checklist item."""

    id: UUID = Field(default_factory=uuid4, description="Evidence record UUID")
    checklist_item_id: str = Field(..., description="ID of the checklist item this evidence covers")
    evidence_type: EvidenceType = Field(..., description="Type of evidence")
    status: EvidenceStatus = Field(
        default=EvidenceStatus.UPLOADED, description="Current review status"
    )
    title: str = Field(..., description="Short title for the evidence")
    description: str = Field(default="", description="Longer description")
    artifact_id: UUID | None = Field(default=None, description="UUID of the stored artifact")
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Upload timestamp",
    )
    reviewed_by: str | None = Field(default=None, description="Reviewer identifier")
    approved_by: str | None = Field(default=None, description="Approver identifier")
