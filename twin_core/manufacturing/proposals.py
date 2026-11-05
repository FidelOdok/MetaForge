"""Candidate processes and materials when nobody has said how it will be made (1A-2).

A first-time user can make a concept without naming a process (3D-RC-015),
but the concept still needs something to be informed by. This proposes
candidates from what is known (the intended use and product text, the
envelope, the quantity) as **assumptions**: always at least two, so no single
method is locked in silently, each with the reason it was proposed and the
material classes it usually takes.

Deterministic keyword and size rules, no model call and no I/O. Nothing here
states a cost, a lead time or a machine capability (DFM-014): those stay
unknown until a person or a verified integration supplies them. The gateway
then checks the candidates against the machine library and the
``rank_manufacturing_processes`` skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FDM = "FDM 3D printing"
SLA = "SLA resin printing"
CNC = "CNC machining"
SHEET = "Sheet metal fabrication"
MOULD = "Injection moulding"
LASER = "Laser cutting"
PCB = "PCB fabrication and assembly"

#: Material classes each process usually takes. Classes, not grades: a grade
#: comes from the material library or from the person.
TYPICAL_MATERIALS: dict[str, tuple[str, ...]] = {
    FDM: ("PLA", "PETG"),
    SLA: ("Standard photopolymer resin",),
    CNC: ("Aluminium 6061", "POM (acetal)"),
    SHEET: ("Mild steel", "Aluminium 5052"),
    MOULD: ("ABS", "Polypropylene"),
    LASER: ("Acrylic", "Plywood"),
    PCB: ("FR-4",),
}

_ELECTRONICS = re.compile(
    r"\b(pcb|circuit|board|sensor|led|leds|microcontroller|mcu|esp32|arduino|usb|"
    r"battery|bluetooth|wifi|electronic|electronics|smart|charger|driver)\b"
)
_ENCLOSURE = re.compile(
    r"\b(enclosure|case|housing|cover|lid|shell|holder|mount|stand|clip|knob|bracket|"
    r"lamp|shade|organiser|organizer|hook|toy|figurine|cap|handle|box)\b"
)
_STRUCTURAL = re.compile(
    r"\b(metal|aluminium|aluminum|steel|load|loads|structural|frame|chassis|heavy|"
    r"machined|precision|heat|outdoor|bike|wheel|axle|shaft|gear)\b"
)
_SHEET = re.compile(r"\b(sheet|panel|plate|bent|bend|folded|chassis|rack|tray)\b")
_FLAT = re.compile(r"\b(flat|sign|stencil|panel|template|coaster|laser)\b")
_FINE = re.compile(r"\b(fine|detailed|miniature|jewellery|jewelry|smooth|small|tiny|figurine)\b")

_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class ProcessCandidate:
    """One proposed process: an assumption, never a decision."""

    process: str
    why: str
    materials: tuple[str, ...]
    #: ``in_house`` or ``vendor`` when the process suggests one, else ``None``.
    route_hint: str | None = None


def parse_envelope_mm(value: object) -> list[float] | None:
    """Three sizes in mm from ``[w, d, h]`` or text such as ``"20 x 15 x 8 cm"``."""
    if isinstance(value, list | tuple) and len(value) == 3:
        try:
            out = [float(v) for v in value]
        except (TypeError, ValueError):
            return None
        return out if all(v > 0 for v in out) else None
    if not isinstance(value, str):
        return None
    numbers = [float(n) for n in _NUMBER.findall(value)]
    if len(numbers) < 3:
        return None
    text = value.lower()
    scale = 1.0
    if re.search(r"\bcm\b", text):
        scale = 10.0
    elif re.search(r"\bm\b", text) and not re.search(r"\bmm\b", text):
        scale = 1000.0
    elif re.search(r"\b(in|inch|inches)\b", text) or '"' in text:
        scale = 25.4
    out = [n * scale for n in numbers[:3]]
    return out if all(v > 0 for v in out) else None


def propose_candidates(
    *,
    text: str,
    envelope_mm: list[float] | None = None,
    quantity: int | None = None,
) -> list[ProcessCandidate]:
    """At least two candidate processes for ``text``, most likely first, at most four."""
    words = (text or "").lower()
    largest = max(envelope_mm) if envelope_mm else None
    picks: list[ProcessCandidate] = []

    def add(process: str, why: str, route: str | None = None) -> None:
        if any(p.process == process for p in picks):
            return
        picks.append(ProcessCandidate(process, why, TYPICAL_MATERIALS[process], route))

    many = quantity is not None and quantity >= 1000
    some = quantity is not None and quantity >= 100
    big = largest is not None and largest > 300

    if _ELECTRONICS.search(words):
        add(PCB, "it has electronics, so a board is made and assembled", "vendor")
    if _SHEET.search(words) and not _FINE.search(words):
        add(SHEET, "flat or folded parts suit sheet metal", "vendor")
    if _STRUCTURAL.search(words):
        add(CNC, "a structural or metal part suits machining", "vendor")
    if many and (_ENCLOSURE.search(words) or not _STRUCTURAL.search(words)):
        add(MOULD, f"{quantity} parts is a moulding quantity", "vendor")
    if _FLAT.search(words):
        add(LASER, "a flat part can be cut from sheet", None)
    if _FINE.search(words) and not big:
        add(SLA, "fine detail and a smooth finish suit resin printing", None)
    if not many:
        why = "a one-off or small batch prints without tooling"
        if big:
            why += "; at this size it may need splitting or a large-format printer"
        add(FDM, why, "in_house")
    if some and not many:
        add(CNC, f"{quantity} parts in a durable material suit machining", "vendor")
    # Never one method silently: always at least two to choose between.
    for process, why in (
        (FDM, "a fast way to try the shape"),
        (CNC, "an alternative in metal or engineering plastic"),
        (MOULD, "the route if it goes to volume"),
    ):
        if len(picks) >= 2:
            break
        add(process, why, "in_house" if process == FDM else "vendor")
    return picks[:4]
