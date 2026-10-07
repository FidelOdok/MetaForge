"""Input/output schemas for the run_drc skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class DrcViolation(BaseModel):
    """A single DRC violation reported by KiCad."""

    rule_id: str = Field(..., description="DRC rule identifier (e.g., 'DRC001')")
    severity: str = Field(..., description="Severity: 'error' or 'warning'")
    message: str = Field(..., description="Human-readable violation description")
    layer: str = Field(default="", description="PCB layer where the violation occurs")
    location: str = Field(default="", description="Location on the PCB (e.g., coordinates)")
    items: list[str] = Field(
        default_factory=list,
        description="Related items (e.g., pad names, net names, track segments)",
    )


class RunDrcInput(BaseModel):
    """Input for the run_drc skill."""

    work_product_id: UUID = Field(..., description="ID of the PCB work_product in the Digital Twin")
    pcb_file: str = Field(..., min_length=1, description="Path to the KiCad PCB file (.kicad_pcb)")
    severity_filter: str = Field(
        default="all",
        description=(
            "Filter the listed violations by severity: 'all', 'error', or 'warning'. "
            "It does not change `passed`, which always counts every error"
        ),
    )
    rule_set: str | None = Field(
        default=None,
        description=(
            "Optional custom design rules (a .kicad_dru path or its text), passed "
            "to kicad.run_drc, which runs DRC with them beside a copy of the board"
        ),
    )


class RunDrcOutput(BaseModel):
    """Output from the run_drc skill."""

    work_product_id: UUID = Field(..., description="ID of the analyzed work_product")
    pcb_file: str = Field(..., description="Path to the PCB file that was checked")
    violations: list[DrcViolation] = Field(
        default_factory=list, description="List of DRC violations found"
    )
    total_violations: int = Field(..., ge=0, description="Total number of violations found")
    total_errors: int = Field(..., ge=0, description="Number of error-severity violations")
    total_warnings: int = Field(..., ge=0, description="Number of warning-severity violations")
    unconnected_items: int = Field(
        default=0, ge=0, description="Connections KiCad reports as missing (unrouted nets)"
    )
    rule_set_applied: bool = Field(
        default=False, description="Whether KiCad ran with the custom rule_set"
    )
    passed: bool = Field(
        ...,
        description=(
            "Whether the PCB passed DRC: no error-severity violation (before the "
            "severity filter) and nothing unconnected; warnings allowed"
        ),
    )
    summary: str = Field(default="", description="Human-readable summary of DRC results")
