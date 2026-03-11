"""Input/output schemas for the run_erc skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ErcViolation(BaseModel):
    """A single ERC violation reported by KiCad."""

    rule_id: str = Field(..., description="ERC rule identifier (e.g., 'ERC001')")
    severity: str = Field(..., description="Severity: 'error' or 'warning'")
    message: str = Field(..., description="Human-readable violation description")
    sheet: str = Field(default="", description="Schematic sheet where the violation occurs")
    component: str = Field(default="", description="Component reference (e.g., 'U1', 'R3')")
    pin: str = Field(default="", description="Pin identifier if applicable")
    location: str = Field(
        default="", description="Location in the schematic (e.g., coordinates or net name)"
    )


class RunErcInput(BaseModel):
    """Input for the run_erc skill."""

    artifact_id: UUID = Field(..., description="ID of the schematic artifact in the Digital Twin")
    schematic_file: str = Field(
        ..., min_length=1, description="Path to the KiCad schematic file (.kicad_sch)"
    )
    severity_filter: str = Field(
        default="all",
        description="Filter violations by severity: 'all', 'error', or 'warning'",
    )


class RunErcOutput(BaseModel):
    """Output from the run_erc skill."""

    artifact_id: UUID = Field(..., description="ID of the analyzed artifact")
    schematic_file: str = Field(..., description="Path to the schematic file that was checked")
    violations: list[ErcViolation] = Field(
        default_factory=list, description="List of ERC violations found"
    )
    total_violations: int = Field(..., ge=0, description="Total number of violations found")
    total_errors: int = Field(..., ge=0, description="Number of error-severity violations")
    total_warnings: int = Field(..., ge=0, description="Number of warning-severity violations")
    passed: bool = Field(
        ..., description="Whether the schematic passed ERC (no errors; warnings allowed)"
    )
    summary: str = Field(default="", description="Human-readable summary of ERC results")
