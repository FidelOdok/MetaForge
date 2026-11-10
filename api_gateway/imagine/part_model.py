"""The approximation rung: the closest 3D we can justify for a real part.

Imagine wants every real part to exist in 3D. When the maker's own model or the
datasheet's package drawing cannot be had, this module draws the nearest shape it can
defend and says, in words, what that shape rests on. It is deterministic, needs no
network and never claims more than it has:

1. A size already in the record (``geometry.envelope``): drawn as a box of that size.
2. A package name that carries its size, such as ``LGA-8_2x2.5mm``, or a chip code such
   as ``0603``: the footprint from the name, with a typical height for the family
   that is stated as not read.
3. A named class of part (a tactile button, a USB-C receptacle): a typical size for
   the class, stated as not this part's own.
4. Nothing usable: a generic block, called a placeholder.

The result is never ``exact`` or ``generated``. A part that already has either is
returned untouched, so a better model is never replaced by a worse one.

The class and family sizes below are rough typical figures, not values from any one
datasheet. That is why every basis says so.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

#: Fidelity words that mean the part already has a better model than this module draws.
BETTER = ("exact", "generated")

PLACEHOLDER_SIZE = [10.0, 10.0, 5.0]

# A size written into a package or footprint name, in millimetres: "2x2.5mm".
_NAME_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)

# Imperial chip codes and their standard footprint and a typical height, in mm.
_CHIP = {
    "0201": ([0.6, 0.3], 0.25),
    "0402": ([1.0, 0.5], 0.35),
    "0603": ([1.6, 0.8], 0.45),
    "0805": ([2.0, 1.25], 0.5),
    "1206": ([3.2, 1.6], 0.55),
}
_CHIP_CODE = re.compile(r"(?<!\d)(0201|0402|0603|0805|1206)(?!\d)")

# A typical height for a package family, in mm. Rough, never read from a drawing.
_FAMILY_HEIGHT = {
    "LGA": 1.0,
    "QFN": 0.9,
    "DFN": 0.9,
    "BGA": 1.2,
    "SOT": 1.1,
    "TSSOP": 1.2,
    "SSOP": 1.75,
    "SOIC": 1.75,
    "SOP": 1.75,
    "QFP": 1.6,
    "DIP": 3.3,
}

# A typical size for a class of part, in mm. Rough, and stated as not the real part.
_CLASS = {
    "e_btn": ("tactile button", [6.0, 6.0, 4.3]),
    "e_led": ("SMD status LED", [1.6, 0.8, 0.6]),
    "e_usbc": ("USB-C receptacle", [8.9, 7.4, 3.3]),
    "e_buzz": ("small buzzer", [12.0, 12.0, 9.0]),
    "e_th": ("small sensor package", [3.0, 3.0, 1.0]),
    "e_imu": ("small sensor package", [3.0, 3.0, 1.0]),
    "e_gnss": ("GNSS module", [16.0, 12.0, 2.5]),
    "e_lte": ("cellular module", [30.0, 30.0, 2.5]),
    "e_lora": ("LoRa radio module", [15.0, 15.0, 2.5]),
    "e_ext": ("radio or interface module", [15.0, 15.0, 2.5]),
    "e_ldrv": ("LED driver module", [20.0, 10.0, 5.0]),
    "battery": ("coin cell", [20.0, 20.0, 3.2]),
    "board": ("small microcontroller board", [25.0, 18.0, 3.0]),
}
# Words in a name that pick a class when the role does not.
_CLASS_WORDS = (
    ("usb-c", "e_usbc"),
    ("usb c", "e_usbc"),
    ("tactile", "e_btn"),
    ("button", "e_btn"),
    ("buzzer", "e_buzz"),
    ("coin cell", "battery"),
    ("cr2032", "battery"),
    ("led", "e_led"),
)


#: How a size was obtained: read (a record, a drawing or a library model), typical (a
#: class or family guess) or generic (a placeholder block).
SIZE_BASIS = ("read", "typical", "generic")


def size_is_read(geometry: Mapping[str, Any]) -> bool:
    """Whether a part's geometry has an envelope that counts as one.

    A size the agent wrote, or one read from a record or a library model, counts. A
    typical or generic guess does not: it draws a body but is not a sized part.
    """
    if not isinstance(geometry, Mapping) or not geometry.get("envelope"):
        return False
    return geometry.get("size_basis") in (None, "read")


def approximate_geometry(part: Mapping[str, Any]) -> dict[str, Any]:
    """Return the part's ``geometry`` with an approximation drawn, or untouched if it
    already has an exact or generated model. The input is never changed."""
    geometry = copy.deepcopy(dict(part.get("geometry") or {}))
    if geometry.get("fidelity") in BETTER:
        return geometry

    envelope = geometry.get("envelope")
    if isinstance(envelope, list) and _valid_size(envelope):
        size = [float(x) for x in envelope]
        source = str(geometry.get("source") or "").strip()
        where = source if source else "source not stated"
        basis = f"size {_fmt(size)} mm given in the part record ({where}); drawn as a box"
        return _finish(geometry, size, "approximation", basis, "read")

    text = " ".join(
        str(x)
        for x in (
            geometry.get("footprint"),
            (part.get("specs") or {}).get("package"),
            part.get("package"),
            part.get("name"),
        )
        if x
    )
    named = _from_name(text)
    if named:
        size, basis = named
        return _finish(geometry, size, "approximation", basis, "typical")

    kind = _class_of(part)
    if kind:
        label, size = _CLASS[kind]
        basis = (
            f"typical size for a {label} ({_fmt(size)} mm); this is the class, "
            "not this part, and its real size was not read"
        )
        return _finish(geometry, list(size), "approximation", basis, "typical")

    basis = "nothing usable was found about its size, so a generic block stands in"
    return _finish(geometry, list(PLACEHOLDER_SIZE), "placeholder", basis, "generic")


def _from_name(text: str) -> tuple[list[float], str] | None:
    size_match = _NAME_SIZE.search(text)
    family = next((f for f in _FAMILY_HEIGHT if re.search(rf"(?<![A-Za-z]){f}", text)), None)
    if size_match and family:
        length, width = float(size_match.group(1)), float(size_match.group(2))
        height = _FAMILY_HEIGHT[family]
        basis = (
            f"footprint {_fmt([length, width])} mm from the package name ({family}); "
            f"height {height} mm is typical for {family} and was not read"
        )
        return [length, width, height], basis
    chip = _CHIP_CODE.search(text)
    if chip:
        footprint, height = _CHIP[chip.group(1)]
        basis = (
            f"standard {chip.group(1)} chip footprint {_fmt(footprint)} mm; "
            f"height {height} mm is typical and was not read"
        )
        return [*footprint, height], basis
    if size_match:
        length, width = float(size_match.group(1)), float(size_match.group(2))
        height = PLACEHOLDER_SIZE[2]
        basis = (
            f"footprint {_fmt([length, width])} mm from the package name; "
            f"height {height} mm is a generic guess and was not read"
        )
        return [length, width, height], basis
    return None


def _class_of(part: Mapping[str, Any]) -> str | None:
    role = str(part.get("role") or "")
    if role.startswith("fn:") and role[3:] in _CLASS:
        return role[3:]
    if role in _CLASS:
        return role
    name = str(part.get("name") or "").lower()
    for word, kind in _CLASS_WORDS:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", name):
            return kind
    return None


def _finish(
    geometry: dict[str, Any], size: list[float], fidelity: str, basis: str, size_basis: str
) -> dict[str, Any]:
    out = dict(geometry)
    out["envelope"] = size
    out["body"] = [{"shape": "box", "at": [0, 0, size[2] / 2], "size": list(size)}]
    out["fidelity"] = fidelity
    out["fidelity_basis"] = basis
    out["size_basis"] = size_basis
    if not out.get("source"):
        out["source"] = "closest approximation, see fidelity_basis"
    return out


def _valid_size(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in value)
    )


def _fmt(size: list[float]) -> str:
    return " x ".join(f"{x:g}" for x in size)
