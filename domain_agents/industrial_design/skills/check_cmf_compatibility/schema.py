"""Input/output schemas for the check_cmf_compatibility skill (FORGE-633)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["pass", "fail", "unknown"]


class PartCmf(BaseModel):
    """A part, what it is made of, and the appearance chosen for it."""

    name: str = Field(..., min_length=1)
    material: str | None = Field(default=None, description="Library material name.")
    appearance: str | None = Field(default=None, description="Library appearance name.")


class AppearanceRecord(BaseModel):
    """The fields of a library appearance this check reads.

    Pass what ``library.get_appearance`` returned; ``materials`` is the list
    of library material names the appearance was recorded as suitable for.
    """

    name: str = Field(..., min_length=1)
    finish: str = ""
    materials: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    colour: dict[str, object] = Field(default_factory=dict)
    properties: dict[str, dict[str, object]] = Field(default_factory=dict)


class CheckCmfCompatibilityInput(BaseModel):
    parts: list[PartCmf] = Field(..., min_length=1)
    appearances: list[AppearanceRecord] = Field(default_factory=list)


class CmfFinding(BaseModel):
    part: str
    appearance: str | None
    material: str | None
    status: Status
    message: str


class CheckCmfCompatibilityOutput(BaseModel):
    verdict: Literal["compatible", "incompatible", "incomplete"]
    findings: list[CmfFinding]
    passed: int
    failed: int
    unknown: int
    finishing_processes: list[str] = Field(
        default_factory=list, description="Finishing steps the chosen appearances need."
    )
    needs_review: list[str] = Field(
        default_factory=list, description="Assumed values in the chosen appearances."
    )
