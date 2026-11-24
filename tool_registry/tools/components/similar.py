"""``component.find_similar``: parts like this one whose sizes have a source (FORGE-927, R5).

A photo with no printed size and no scale object can still be sized from a similar
part: another board of the same form factor (Feather, STEMMA QT), another part in the
same package (QFN-56, SOIC-8) or of the same maker series. This finds such parts,
the library first, then the distributors, and keeps only those whose length and width
have a real source (not an estimate, not an assumption): a similar part with guessed
sizes would only pass the guess on.

Each candidate says why it counts as similar (which of family, package, pin count,
series and form factor matched) and carries ``estimated_from_similar``, the reference
an estimate built from it records as its basis (``source_ref`` with method
``similar_part``), so a later correction to the similar part can find the estimates
that used it.
"""

from __future__ import annotations

import re
from typing import Any

#: A size the similar part holds must have one of these sources to count.
_REAL_SOURCES = frozenset(
    {"measurement", "standard", "datasheet", "manufacturer_spec", "supplier", "user"}
)

_DIM = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|in|\")?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|in|\")?", re.I)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def why_similar(
    entry: dict[str, Any],
    *,
    family: str = "",
    package: str = "",
    pin_count: int | None = None,
    series: str = "",
    form_factor: str = "",
) -> list[str]:
    """What this entry shares with the part being sized, in words."""
    hay = " ".join(
        str(entry.get(k) or "") for k in ("name", "title", "size", "mpn", "manufacturer", "notes")
    )
    specs = entry.get("specs") or {}
    words = _words(hay)
    reasons: list[str] = []
    if family and str(entry.get("family") or "").lower() == family.lower():
        reasons.append(f"same family ({family})")
    if package and _words(package) and _words(package) <= words:
        reasons.append(f"same package ({package})")
    pins = (
        (specs.get("pin_count") or {}).get("value")
        if isinstance(specs.get("pin_count"), dict)
        else None
    )
    if pin_count is not None and pins is not None and int(pins) == int(pin_count):
        reasons.append(f"same pin count ({pin_count})")
    if series and _words(series) and _words(series) <= words:
        reasons.append(f"same series ({series})")
    if form_factor and _words(form_factor) and _words(form_factor) <= words:
        reasons.append(f"same form factor ({form_factor})")
    return reasons


def sourced_size(specs: dict[str, Any]) -> dict[str, Any] | None:
    """``{length_mm, width_mm, height_mm?, sources}`` when length and width are sourced."""
    out: dict[str, Any] = {"sources": {}}
    for axis in ("length", "width", "height"):
        spec = specs.get(f"{axis}_mm")
        if not isinstance(spec, dict) or spec.get("source_kind") not in _REAL_SOURCES:
            continue
        value = spec.get("value")
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            out[f"{axis}_mm"] = float(value)
            out["sources"][axis] = (
                f"{spec['source_kind']}: {spec.get('source_ref') or 'no reference'}"
            )
    if "length_mm" not in out or "width_mm" not in out:
        return None
    return out


def distributor_size(specs: dict[str, Any]) -> dict[str, float] | None:
    """A size from a distributor's parameters ("Size / Dimension": "25.4mm x 17.8mm")."""
    for key, value in (specs or {}).items():
        if not re.search(r"size|dimension", str(key), re.I):
            continue
        found = _DIM.search(str(value))
        if not found:
            continue
        a, unit_a, b, unit_b = found.groups()
        unit = (unit_b or unit_a or "mm").lower()
        factor = 25.4 if unit in ("in", '"') else 1.0
        sizes = sorted([float(a) * factor, float(b) * factor], reverse=True)
        return {"length_mm": round(sizes[0], 3), "width_mm": round(sizes[1], 3)}
    return None
