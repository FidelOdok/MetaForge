"""The circuit description the Phase 2 KiCad tools generate from (FORGE-683).

One input drives schematic generation, board generation and routing: the
components (each a KiCad library symbol and footprint), the nets that join
their pins, and the board. The electronics agent builds it from the component
selection; nothing in it is drawn freehand, every symbol and footprint must
exist in the libraries the adapter carries.

A part with no KiCad symbol of its own is drawn with a stand-in (a BMI270 with
``Sensor_Motion:BMI160``). :func:`symbol_substitutions` finds those by
comparing each symbol's name with the part number, so the substitution is
reported instead of passing silently (FORGE-699).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = [
    "Board",
    "Circuit",
    "Component",
    "EdgeMount",
    "MountingHole",
    "Net",
    "Placement",
    "Position",
    "symbol_matches_part",
    "symbol_substitutions",
]

#: A board edge, in KiCad's board frame: x to the right, y DOWN, so ``-y`` is
#: the top edge as the board is drawn and ``+y`` the bottom one.
Edge = Literal["+x", "-x", "+y", "-y"]

_LIB_ID = re.compile(r"^[A-Za-z0-9_.+\-]+:[^:\s][^:]*$")
_REF = re.compile(r"^[A-Za-z#]+[A-Za-z0-9_]*$")
#: Libraries of generic symbols (a resistor, a header): their names say nothing
#: about the part number, so they are never a substitution.
GENERIC_SYMBOL_LIBS = frozenset(
    {
        "Device",
        "power",
        "Connector",
        "Connector_Generic",
        "Connector_Generic_MountingPin",
        "Connector_Generic_Shielded",
        "Jumper",
        "Switch",
        "Mechanical",
        "Graphic",
    }
)
_MPN_KEYS = frozenset({"mpn", "manufacturer_part_number", "mfr_pn", "part_number"})
_PART_NUMBER = re.compile(r"^(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9_.+/\-]{3,}$")


class Position(BaseModel):
    """A point on the board in mm, from the board's top-left corner (x right, y down)."""

    x_mm: float
    y_mm: float


class EdgeMount(BaseModel):
    """Put a connector on a board edge with its mating side facing out."""

    edge: Edge = Field(
        ...,
        description=(
            "The board edge the connector's mouth faces: +x right, -x left, +y bottom, "
            "-y top (KiCad's y runs down)"
        ),
    )
    overhang_mm: float = Field(
        default=0.0,
        ge=-20,
        le=20,
        description=(
            "How far the mouth sits beyond the board edge: 0 flush, positive proud, "
            "negative set back"
        ),
    )


class Placement(BaseModel):
    """Where a footprint goes. Anything left out is chosen by the auto-placer."""

    position: Position | None = Field(
        default=None,
        description=(
            "The footprint's anchor in mm from the board's top-left corner (x right, y "
            "down). With edge_mount, only the coordinate along that edge is used"
        ),
    )
    rotation_deg: float | None = Field(
        default=None,
        description=(
            "KiCad orientation in degrees, counter-clockwise as drawn. With edge_mount it "
            "overrides the rotation worked out from the footprint"
        ),
    )
    side: Literal["top", "bottom"] = "top"
    edge_mount: EdgeMount | None = None


class MountingHole(BaseModel):
    """A mounting hole at a given place, instead of the four corner holes."""

    position: Position
    drill_mm: float | None = Field(
        default=None, gt=0, description="Drill; the board's mounting_holes_mm, else 3.2"
    )


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
    symbol_pinout_checked: str | None = Field(
        default=None,
        description=(
            "When `symbol` is a stand-in for a part that has no KiCad symbol of its "
            "own (e.g. Sensor_Motion:BMI160 for a BMI270): how its pin numbers were "
            "checked against the part's datasheet. Without it the substitution is "
            "reported as unconfirmed"
        ),
    )
    placement: Placement | None = Field(
        default=None,
        description=(
            "Board placement hints for kicad.generate_pcb: position, rotation_deg, side, "
            "edge_mount. A part without one is auto-placed"
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

    def part_number(self) -> str | None:
        """The MPN field, or the value when it reads as a part number."""
        for key, val in self.fields.items():
            if key.strip().lower().replace(" ", "_") in _MPN_KEYS and val.strip():
                return val.strip()
        return self.value.strip() if _PART_NUMBER.match(self.value.strip()) else None


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
    mounting_holes: list[MountingHole] = Field(
        default_factory=list,
        description=(
            "Mounting holes at given positions (H1, H2, ... in this order). When given, "
            "they replace the four corner holes"
        ),
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
        if self.board is not None:
            w, h = self.board.width_mm, self.board.height_mm
            for comp in self.components:
                pos = comp.placement.position if comp.placement else None
                if pos is not None and not (0 <= pos.x_mm <= w and 0 <= pos.y_mm <= h):
                    raise ValueError(
                        f"{comp.ref}: position ({pos.x_mm}, {pos.y_mm}) is outside the "
                        f"{w} x {h} mm board"
                    )
            for i, hole in enumerate(self.board.mounting_holes, start=1):
                p = hole.position
                if not (0 <= p.x_mm <= w and 0 <= p.y_mm <= h):
                    raise ValueError(f"mounting hole {i}: ({p.x_mm}, {p.y_mm}) is off the board")
        return self

    def net_of(self) -> dict[str, str]:
        """``"REF.PIN" -> net name``."""
        return {node: net.name for net in self.nets for node in net.nodes}


def _stem(text: str) -> str:
    """The first alphanumeric run: ``BMI270`` of ``BMI270``, ``MCP73831`` of ``MCP73831-2-OT``."""
    return re.split(r"[^A-Za-z0-9]", text.strip(), maxsplit=1)[0]


def symbol_matches_part(symbol: str, part: str) -> bool:
    """Whether the symbol's name names this part number.

    KiCad writes a lowercase ``x`` for any character (``STM32F407VGTx``). The
    part number may carry a suffix the symbol does not (``LPS22HBTR`` for
    ``LPS22HB``), or the symbol a package code the part number leaves out.
    """
    name = _stem(symbol.split(":", 1)[-1])
    pn = _stem(part).upper()
    if not name or not pn:
        return True
    if re.match(_pattern(name), pn):
        return True
    return len(pn) >= 5 and re.fullmatch(_pattern(name[: len(pn)]), pn) is not None


def _pattern(name: str) -> str:
    return "".join("[A-Z0-9]" if ch == "x" else re.escape(ch.upper()) for ch in name)


def symbol_substitutions(circuit: Circuit) -> list[dict[str, Any]]:
    """Parts drawn with a symbol whose name is not their part number.

    Each entry names the reference, the part number, the symbol used, whether
    the pinout check is recorded (``symbol_pinout_checked``) and, while it is
    not, a warning to check the pins against the part's datasheet.
    """
    out: list[dict[str, Any]] = []
    for comp in circuit.components:
        lib = comp.symbol.split(":", 1)[0]
        part = comp.part_number()
        if lib in GENERIC_SYMBOL_LIBS or part is None or symbol_matches_part(comp.symbol, part):
            continue
        confirmed = bool(comp.symbol_pinout_checked and comp.symbol_pinout_checked.strip())
        entry: dict[str, Any] = {
            "ref": comp.ref,
            "part_number": part,
            "symbol": comp.symbol,
            "confirmed": confirmed,
            "pinout_check": comp.symbol_pinout_checked if confirmed else None,
        }
        if not confirmed:
            entry["warning"] = (
                f"{comp.ref} ({part}) is drawn with the {comp.symbol} symbol. Check every "
                f"pin number against the {part} datasheet, then record how in "
                "symbol_pinout_checked, before the board is ordered"
            )
        out.append(entry)
    return out
