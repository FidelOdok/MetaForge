"""Which way an edge connector faces, and the plug that has to reach it.

A desk lamp was released with its USB-C receptacle facing along the board
(+Y in the assembly) while the slot cut for it was in the +X wall: a plug
pushed through the slot meets the side of the receptacle. The assembly check
had compared only how far the connector sat from the board edge and the slot.
This module gives it the two things it lacked:

**The mating axis.** The direction a connector's mouth faces, out of the
board, which a plug travels against to mate. KiCad's library footprints for
horizontal USB receptacles put the mouth toward +Y of the footprint (down the
screen, where the board edge line is drawn), so the axis follows from the
footprint, its rotation on the board and the side it is on
(:func:`footprint_mating_axis`). The result is in the board body's frame,
the STEP ``kicad.export_3d`` writes, whose Y is KiCad's Y reversed; a
rotation is KiCad's (counter-clockwise seen from the top). A footprint the
table does not know, or a part on the bottom side, can be given its axis
explicitly.

**The plug envelope.** A keep-out box for the mating plug's overmold
(the moulded grip), by default a USB-C plug's: 12.35 x 6.5 mm and 20 mm
deep, with its metal shell 8.4 x 2.6 mm and 6.65 mm long. When the
receptacle sits behind the outer face of the wall, the overmold has to enter
the slot for the shell to seat; when the slot is smaller than the overmold
the plug stops short. :func:`plug_envelope` gives the envelope for a kind
or an override.

:func:`kicad_footprints` reads each footprint's library name, position,
rotation and side out of a ``.kicad_pcb`` so a connector can be named by its
reference (``J1``).

Pure: no I/O.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from twin_core.dfa.geometry import Vec

#: Kinds of plug this module knows the envelope of.
USB_C = "usb_c"


@dataclass(frozen=True)
class PlugEnvelope:
    """The mating plug: its overmold (the grip) and its shell (what goes in)."""

    kind: str
    #: Overmold width (in the board's plane), height and depth, mm.
    width_mm: float
    height_mm: float
    depth_mm: float
    #: The metal shell that enters the receptacle: width, height, length, mm.
    shell_width_mm: float
    shell_height_mm: float
    shell_length_mm: float
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "overmold_mm": [self.width_mm, self.height_mm, self.depth_mm],
            "shell_mm": [self.shell_width_mm, self.shell_height_mm, self.shell_length_mm],
            "source": self.source,
        }


#: Typical USB-C plug dimensions: the overmold is a common cable's (the USB
#: Type-C specification limits it to 12.35 x 6.5 mm for full insertion), the
#: shell the specification's 8.25 x 2.4 mm nominal with a little allowance.
PLUG_ENVELOPES: dict[str, PlugEnvelope] = {
    USB_C: PlugEnvelope(
        kind=USB_C,
        width_mm=12.35,
        height_mm=6.5,
        depth_mm=20.0,
        shell_width_mm=8.4,
        shell_height_mm=2.6,
        shell_length_mm=6.65,
        source="USB Type-C plug overmold limit 12.35 x 6.5 mm; shell 8.25 x 2.4 mm nominal",
    ),
}


@dataclass(frozen=True)
class _Footprint:
    pattern: re.Pattern[str]
    #: The mouth's direction in the footprint's own KiCad frame (Y down).
    mating_kicad: tuple[float, float]
    plug: str


#: Library footprints whose mating side is known. KiCad's horizontal USB-C
#: receptacles (GCT USB4105, HRO TYPE-C-31-M, Amphenol 12401610 ...) put
#: the pads at -Y and the mouth, at the board edge line, at +Y.
FOOTPRINTS: tuple[_Footprint, ...] = (
    _Footprint(
        re.compile(r"USB_C_Receptacle(?!.*Vertical)", re.IGNORECASE),
        (0.0, 1.0),
        USB_C,
    ),
)


def known_footprint(footprint: str | None) -> _Footprint | None:
    """The table row for a library footprint name, or ``None``."""
    if not footprint:
        return None
    return next((f for f in FOOTPRINTS if f.pattern.search(footprint)), None)


def _side_word(side: str | None) -> str:
    text = str(side or "top").strip().lower()
    if text in ("b.cu", "bottom", "back", "b"):
        return "bottom"
    return "top"


def mating_side_vector(raw: Any) -> tuple[float, float]:
    """A mating side in the footprint's KiCad frame: ``+y``/``-x``/... or ``[x, y]``."""
    if isinstance(raw, str):
        text = raw.strip().lower().replace(" ", "")
        table = {"+x": (1.0, 0.0), "-x": (-1.0, 0.0), "+y": (0.0, 1.0), "-y": (0.0, -1.0)}
        if text in table:
            return table[text]
        raise ValueError(f"mating_side {raw!r} is one of +x, -x, +y, -y")
    if isinstance(raw, list | tuple) and len(raw) == 2:
        x, y = float(raw[0]), float(raw[1])
        n = math.hypot(x, y)
        if n < 1e-9:
            raise ValueError("mating_side must not be zero")
        return (x / n, y / n)
    raise ValueError("mating_side is +x, -x, +y, -y or [x, y] in the footprint's KiCad frame")


def footprint_mating_axis(
    footprint: str | None,
    rotation_deg: float = 0.0,
    side: str | None = "top",
    mating_side: Any = None,
) -> Vec | None:
    """The mouth's direction in the board body's frame, or ``None`` when unknown.

    ``mating_side`` (in the footprint's KiCad frame) overrides the table. The
    footprint frame is turned to the board body's (KiCad's Y reversed), a
    bottom-side part mirrored in X as KiCad flips it, then rotated by
    ``rotation_deg`` counter-clockwise seen from the top.
    """
    if mating_side is not None:
        lx, ly = mating_side_vector(mating_side)
    else:
        row = known_footprint(footprint)
        if row is None:
            return None
        lx, ly = row.mating_kicad
    x, y = lx, -ly  # KiCad's Y points down the screen; the board body's points up
    if _side_word(side) == "bottom":
        x = -x
    a = math.radians(float(rotation_deg or 0.0))
    c, s = math.cos(a), math.sin(a)
    out = (x * c - y * s, x * s + y * c, 0.0)
    return (round(out[0], 12) + 0.0, round(out[1], 12) + 0.0, 0.0)


def plug_envelope(kind: str | None = None, override: Any = None, shell: Any = None) -> PlugEnvelope:
    """The envelope for a plug ``kind`` (default USB-C), with any dimension overridden.

    ``override`` is ``[width, height, depth]`` of the overmold, ``shell``
    ``[width, height, length]`` of the shell, in mm.
    """
    base = PLUG_ENVELOPES.get(str(kind or USB_C).lower())
    if base is None:
        raise ValueError(
            f"plug {kind!r} has no default envelope; give plug_envelope_mm "
            f"(known: {', '.join(PLUG_ENVELOPES)})"
        )
    w, h, d = base.width_mm, base.height_mm, base.depth_mm
    sw, sh, sl = base.shell_width_mm, base.shell_height_mm, base.shell_length_mm
    source = base.source
    if override is not None:
        w, h, d = _three(override, "plug_envelope_mm")
        source = "given"
    if shell is not None:
        sw, sh, sl = _three(shell, "plug_shell_mm")
        source = "given"
    return PlugEnvelope(base.kind, w, h, d, sw, sh, sl, source)


def _three(raw: Any, what: str) -> tuple[float, float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise ValueError(f"{what} must be [width, height, depth] in mm")
    try:
        a, b, c = (float(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be three numbers") from exc
    if min(a, b, c) <= 0:
        raise ValueError(f"{what} must be positive")
    return a, b, c


# ---------------------------------------------------------------------------
# Footprints out of a .kicad_pcb
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardFootprint:
    reference: str
    footprint: str
    #: Position on the board in KiCad's coordinates (mm, Y down).
    x: float
    y: float
    rotation_deg: float
    side: str


_FP_START = re.compile(r'\(footprint\s+"([^"]+)"')
_LAYER = re.compile(r'\(layer\s+"?([^"\s)]+)"?\)')
_AT = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)")
_REF = re.compile(r'\(property\s+"Reference"\s+"([^"]+)"|\(fp_text\s+reference\s+"?([^"\s)]+)"?')


def kicad_footprints(text: str) -> dict[str, BoardFootprint]:
    """``reference -> footprint`` for every footprint in a ``.kicad_pcb``'s text."""
    out: dict[str, BoardFootprint] = {}
    starts = [m for m in _FP_START.finditer(text)]
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[m.start() : end]
        ref = _REF.search(block)
        at = _AT.search(block)
        layer = _LAYER.search(block)
        if ref is None or at is None:
            continue
        reference = ref.group(1) or ref.group(2)
        out[reference] = BoardFootprint(
            reference=reference,
            footprint=m.group(1),
            x=float(at.group(1)),
            y=float(at.group(2)),
            rotation_deg=float(at.group(3) or 0.0),
            side="bottom" if layer is not None and layer.group(1).startswith("B.") else "top",
        )
    return out


def edge_connectors(footprints: dict[str, BoardFootprint]) -> list[BoardFootprint]:
    """The footprints whose mating side is known: the board's edge connectors."""
    return [f for f in footprints.values() if known_footprint(f.footprint) is not None]


__all__ = [
    "FOOTPRINTS",
    "PLUG_ENVELOPES",
    "USB_C",
    "BoardFootprint",
    "PlugEnvelope",
    "edge_connectors",
    "footprint_mating_axis",
    "kicad_footprints",
    "known_footprint",
    "mating_side_vector",
    "plug_envelope",
]
