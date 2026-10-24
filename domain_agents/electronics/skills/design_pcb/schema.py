"""Input/output schemas for the design_pcb skill (FORGE-683)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from tool_registry.tools.kicad.circuit import Circuit

Verdict = Literal["pass", "fail", "not_established"]


class DesignPcbInput(BaseModel):
    """A circuit (components, nets, and a board to lay it out on)."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the electronics")
    circuit: Circuit
    route: bool = Field(default=True, description="Autoroute the board after placing it")
    passes: int = Field(default=30, ge=1, le=200, description="Freerouting passes")


class DesignPcbOutput(BaseModel):
    work_product_id: UUID
    schematic_verdict: Verdict
    schematic_file: str
    erc_errors: int = 0
    netlist_mismatches: list[dict[str, Any]] = Field(default_factory=list)
    pcb_verdict: Verdict | None = None
    pcb_file: str | None = None
    unplaced: list[str] = Field(default_factory=list)
    route_verdict: Verdict | None = None
    routed_pcb_file: str | None = None
    unrouted: int | None = None
    drc_errors: int | None = None
    schematic_parity: list[dict[str, Any]] = Field(default_factory=list)
    tracks: int | None = None
    vias: int | None = None
    reason: str = ""
