"""Extract an IC package spec from datasheet package-drawing text (MET-542).

Turns the package-outline section of a datasheet into the parameters that the
FreeCAD ``generate_ic_package`` skill consumes — no human reads the drawing.

This is a deterministic parser for the common TI/JEDEC bracketed-millimetre
outline format, where every dimension appears as ``[lo-hi]`` or ``[val]`` mm
(controlling dims in inches alongside), with context tokens like ``MAX``,
``TYP`` and ``NX`` (an N-times multiplier). It covers SOIC/SOP/SSOP/TSSOP/QFP/
QFN/DIP outlines, which dominate TI and JEDEC-registered parts. An LLM-backed
extractor (``digital_twin.knowledge``) would generalise to arbitrary vendor
formats; this needs no model and runs anywhere.

Pipeline: PDF text → ``find_package_page`` → ``extract_ic_package_spec`` →
``spec_to_generator_params`` → ``freecad.generate_ic_package``.
"""

from __future__ import annotations

import re
from typing import Any

# Package families, most specific first so e.g. TSSOP wins over SOP.
_FAMILIES = ["TSSOP", "SSOP", "MSOP", "SOIC", "SOP", "LQFP", "TQFP", "MQFP", "QFP", "QFN", "DIP"]
# Standard lead pitches (mm) used to disambiguate the pitch dimension
# (2.54 = 0.1in through-hole DIP … 0.3 fine-pitch QFP).
_STD_PITCHES = (2.54, 1.27, 1.00, 0.80, 0.65, 0.50, 0.40, 0.30)
# A bracketed millimetre dimension: ``[5.80-6.19]`` or ``[1.75]``.
_BRACKET = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*\]")
# TI package code embeds the pin count, e.g. ``D0008A`` (SOIC, 8 pins). The
# extracted text often glues it to the prior word ("heightD0008A"), so we don't
# require a leading word boundary — just letter, 4 digits, trailing letter.
_PKG_CODE = re.compile(r"[A-Z](\d{4})[A-Z](?![A-Z0-9])")


def find_package_page(pages_text: list[str], target_family: str | None = None) -> str | None:
    """Pick the package-outline page, preferring one matching ``target_family``.

    Datasheets often document several packages; when a target family is given
    (e.g. ``"SOIC"``), return the outline page that actually yields a spec for
    it (highest lead count wins ties). Falls back to the first page that yields
    any complete spec.
    """
    outline = [t for t in pages_text if "PACKAGE OUTLINE" in t.upper() and _BRACKET.search(t)]
    if not outline:
        outline = [
            t for t in pages_text if any(f in t.upper() for f in _FAMILIES) and _BRACKET.search(t)
        ]

    if target_family:
        tf = target_family.upper()
        matches = []
        for t in outline:
            spec = extract_ic_package_spec(t)
            if spec and spec["package_type"] == tf:
                matches.append((spec.get("lead_count") or 0, t))
        if matches:
            return max(matches, key=lambda m: m[0])[1]

    for t in outline:
        if extract_ic_package_spec(t):
            return t
    return None


def _family(text: str) -> str | None:
    up = text.upper()
    for fam in _FAMILIES:
        if fam in up:
            return fam
    return None


def _lead_count(text: str) -> int | None:
    for m in _PKG_CODE.finditer(text):
        n = int(m.group(1))
        if 0 < n <= 512:
            return n
    m2 = re.search(r"\b(\d{1,3})\s*PINS?\b", text.upper())
    return int(m2.group(1)) if m2 else None


def _items(text: str) -> list[dict[str, Any]]:
    """All bracketed mm dims, each tied to its own line + the immediately
    preceding line (upper-cased). Line-scoped context avoids labels like ``MAX``
    bleeding from one short dimension line into the next.
    """
    out: list[dict[str, Any]] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        prev = lines[idx - 1] if idx > 0 else ""
        for m in _BRACKET.finditer(line):
            lo = float(m.group(1))
            hi = float(m.group(2)) if m.group(2) else lo
            ctx = (prev + " " + line[: m.start()]).upper()
            out.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "mid": round((lo + hi) / 2.0, 3),
                    "range": m.group(2) is not None,
                    "ctx": ctx,
                }
            )
    return out


def _has_mult(ctx: str) -> bool:
    """Context carries an N-times multiplier (``6X``, ``8X``) → a lead dimension."""
    return bool(re.search(r"\d\s*X", ctx))


def extract_ic_package_spec(text: str) -> dict[str, Any] | None:
    """Parse package-outline text into a typed spec, or None if not an IC outline."""
    fam = _family(text)
    if fam is None:
        return None
    items = _items(text)
    if not items:
        return None

    # Pitch: a single-valued, multiplier-tagged dim nearest a standard pitch.
    pitch = None
    pitch_cands = [
        it for it in items if not it["range"] and _has_mult(it["ctx"]) and 0.25 <= it["mid"] <= 2.6
    ]
    if pitch_cands:
        pitch = min(
            min((abs(it["mid"] - p), it["mid"]) for p in _STD_PITCHES) for it in pitch_cands
        )[1]

    # Lead width: a small multiplier-tagged range (the ``NX`` lead dim).
    lw_cands = [it for it in items if it["range"] and _has_mult(it["ctx"]) and it["mid"] < 1.0]
    lead_width = min((it["mid"] for it in lw_cands), default=None)

    # Height: a single MAX value in a plausible body-height band.
    h_cands = [it for it in items if "MAX" in it["ctx"] and 0.4 <= it["mid"] <= 6.0]
    height = min((it["mid"] for it in h_cands), default=None)

    used = {id(it) for it in lw_cands}
    # Lead span: the widest range (prefer a TYP-tagged one), not a lead/height dim.
    span_cands = [
        it for it in items if it["range"] and id(it) not in used and not _has_mult(it["ctx"])
    ]
    typ = [it for it in span_cands if "TYP" in it["ctx"]]
    span_item = max(typ or span_cands, key=lambda it: it["mid"], default=None)
    lead_span = span_item["mid"] if span_item else None

    # Body length/width: the two largest remaining body-scale ranges.
    body_cands = sorted(
        (it for it in span_cands if it is not span_item and 2.0 <= it["mid"] <= (lead_span or 1e9)),
        key=lambda it: it["mid"],
        reverse=True,
    )
    body_length = body_cands[0]["mid"] if body_cands else None
    body_width = body_cands[1]["mid"] if len(body_cands) > 1 else body_length

    spec = {
        "package_type": fam,
        "lead_count": _lead_count(text),
        "pitch": pitch,
        "lead_span": lead_span,
        "body_length": body_length,
        "body_width": body_width,
        "height": height,
        "lead_width": lead_width,
    }
    # Require the fields the generator can't sensibly default.
    required = ("package_type", "lead_count", "pitch", "lead_span", "body_length", "body_width")
    if any(spec[k] is None for k in required):
        return None
    return spec


def spec_to_generator_params(spec: dict[str, Any]) -> dict[str, Any]:
    """Map an extracted spec to ``generate_ic_package`` params (mm)."""
    standoff = 0.1
    height = spec.get("height")
    body_height = round(max(0.3, height - standoff), 3) if height else 1.4
    return {
        "body_length": spec["body_length"],
        "body_width": spec["body_width"],
        "body_height": body_height,
        "lead_count": int(spec["lead_count"]),
        "pitch": spec["pitch"],
        "lead_span": spec["lead_span"],
        "lead_width": spec.get("lead_width") or 0.4,
        "lead_thickness": 0.2,
        "standoff": standoff,
    }
