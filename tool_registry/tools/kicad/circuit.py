"""The circuit description the Phase 2 KiCad tools generate from (FORGE-683).

One input drives schematic generation, board generation and routing: the
components (each a KiCad library symbol and footprint), the nets that join
their pins, and the board. The electronics agent builds it from the component
selection; nothing in it is drawn freehand, every symbol and footprint must
exist in the libraries the adapter carries.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = ["Board", "Circuit", "Component", "Net"]

_LIB_ID = re.compile(r"^[A-Za-z0-9_.+\-]+:[^:\s][^:]*$")
_REF = re.compile(r"^[A-Za-z#]+[A-Za-z0-9_]*$")


class Component(BaseModel):
    ref: str = Field(..., description="Reference designator, e.g. U1")
    symbol: str = Field(..., description="KiCad symbol lib_id, e.g. Device:R")
    footprint: str = Field(
        ..., description="KiCad footprint lib_id, e.g. Resistor_SMD:R_0603_1608Metric"
    )
    value: str = Field(..., min_length=1)
    fields: dict[str, str] = Field(default_factory=dict, description="Extra fields, e.g. MPN")
    model: str | None = Field(
        default=None,
        description=(
            "3D model (STEP) to use instead of the footprint's own, for a library "
            "footprint whose model reference is wrong or missing, e.g. "
            "${KICAD9_3DMODEL_DIR}/Package_DFN_QFN.3dshapes/<name>.step"
        ),
    )

    @field_validator("ref")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not _REF.match(v):
            raise ValueError(f"invalid reference {v!r}")
        return v

    @field_validator("symbol", "footprint")
    @classmethod
    def _lib_id(cls, v: str) -> str:
        if not _LIB_ID.match(v):
            raise ValueError(f"{v!r} is not a 'Library:Name' id")
        return v


class Net(BaseModel):
    name: str = Field(..., min_length=1)
    nodes: list[str] = Field(..., min_length=1, description="Pins as REF.PIN, e.g. U1.3")

    @field_validator("nodes")
    @classmethod
    def _nodes(cls, v: list[str]) -> list[str]:
        for node in v:
            if "." not in node:
                raise ValueError(f"node {node!r} is not REF.PIN")
        return v


class Board(BaseModel):
    width_mm: float = Field(..., gt=0, le=500)
    height_mm: float = Field(..., gt=0, le=500)
    layers: int = Field(default=2, description="Copper layers: 2 or 4")
    clearance_mm: float = Field(default=0.2, gt=0)
    track_mm: float = Field(default=0.25, gt=0)
    power_track_mm: float | None = Field(
        default=None, gt=0, description="Track width for the nets in power_nets"
    )
    power_nets: list[str] = Field(default_factory=list)
    via_diameter_mm: float = Field(default=0.6, gt=0)
    via_drill_mm: float = Field(default=0.3, gt=0)
    hole_clearance_mm: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Copper-to-hole clearance; KiCad's default (0.25) when omitted. Lower it to "
            "what the fab allows when a vendor land pattern sits closer to its own holes"
        ),
    )
    ground_pour: str | None = Field(
        default=None, description="Net to pour on the outer layers, e.g. GND"
    )
    mounting_holes_mm: float | None = Field(
        default=None, gt=0, description="Add four corner mounting holes of this drill"
    )
    inner_planes: list[str] = Field(
        default_factory=list,
        description=(
            "4-layer boards: the nets for solid planes on In1.Cu and In2.Cu, in that "
            "order, e.g. [GND, +3V3]. The router reaches a plane net through vias "
            "instead of routing it as tracks"
        ),
    )

    @field_validator("layers")
    @classmethod
    def _layers(cls, v: int) -> int:
        if v not in (2, 4):
            raise ValueError("layers must be 2 or 4")
        return v

    @model_validator(mode="after")
    def _planes(self) -> Board:
        if self.inner_planes and self.layers != 4:
            raise ValueError("inner_planes needs a 4-layer board")
        if len(self.inner_planes) > 2:
            raise ValueError("inner_planes: at most two (In1.Cu, In2.Cu)")
        return self


class Circuit(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_\-]+$")
    components: list[Component] = Field(..., min_length=1)
    nets: list[Net] = Field(default_factory=list)
    board: Board | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Circuit:
        refs = [c.ref for c in self.components]
        dup = sorted({r for r in refs if refs.count(r) > 1})
        if dup:
            raise ValueError(f"duplicate references: {', '.join(dup)}")
        names = [n.name for n in self.nets]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate nets: {', '.join(dup)}")
        known = set(refs)
        seen: dict[str, str] = {}
        for net in self.nets:
            for node in net.nodes:
                ref = node.split(".", 1)[0]
                if ref not in known:
                    raise ValueError(f"net {net.name}: {node} names unknown component {ref}")
                if node in seen:
                    raise ValueError(f"{node} is on both {seen[node]} and {net.name}")
                seen[node] = net.name
        return self

    def net_of(self) -> dict[str, str]:
        """``"REF.PIN" -> net name``."""
        return {node: net.name for net in self.nets for node in net.nodes}
