"""``scale.resolve``: a photo reading's pixels into millimetres, from ranked sources (R4).

Plain code, no model. A client reads a photo (the partscope skill) and measures in
pixels; this sets each side's size from the best source it has and checks it against
the rest, or says what is missing. It never guesses.

Sources, best first, per side (length, width and height are resolved separately):

1. **printed**: a size stated in the input text, kept only when its quote is found in
   that text and holds the number (an invented "99 mm" is dropped);
2. **package_nominal**: a body size read from a KiCad footprint name
   (``SOIC-8_3.9x4.9mm_P1.27mm``, ``R_0603_1608Metric``), never from memory;
3. **similar_part**: a similar part's sourced size (``component.find_similar``);
4. **photo**: a scale object in the photo (a header's 2.54 mm pitch, a JST connector),
   from the sourced table in ``data/scale_objects.json`` or one the caller cites.

The first source a side has sets it; every other one is compared, and a disagreement
over :data:`DISAGREE` warns and lowers the confidence. A side with no source of its own
reuses the other side's photo scale, flagged, and in an angled view flagged as probably
too small. A measured pitch snaps to a standard pitch only within :data:`SNAP`. Every
value is an estimate: ``{value, unit, source_kind: estimate, method, basis, image_ref,
confidence}``. With no source for a side, the reply says what would give one (``needs``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AXES = ("length", "width", "height")
PLANE = ("length", "width")
#: Two sources further apart than this (a fraction of the chosen value) disagree.
DISAGREE = 0.15
#: A measured pitch snaps to a standard one only when within this fraction of it.
SNAP = 0.12

_BASE_CONFIDENCE = {"printed": 0.95, "package_nominal": 0.85, "similar_part": 0.75, "photo": 0.65}
_RANK = ("printed", "package_nominal", "similar_part", "photo")

_DATA = Path(__file__).parent / "data" / "scale_objects.json"


class ScaleError(ValueError):
    """The request is not a reading this can resolve; the message says why."""


def load_scale_objects() -> tuple[dict[str, dict[str, Any]], list[float], str]:
    """The sourced scale objects by kind, the standard pitches and their source."""
    data = json.loads(_DATA.read_text())
    objects = {o["kind"]: o for o in data["objects"]}
    pitches = data["standard_pitches_mm"]
    return objects, [float(p) for p in pitches["values"]], str(pitches["source_ref"])


@dataclass
class Candidate:
    method: str
    value: float
    basis: str
    image_ref: str | None = None
    px_per_mm: float | None = None


@dataclass
class Resolved:
    sizes: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    rejected_quotes: list[dict[str, Any]] = field(default_factory=list)
    pitches: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "resolved" if not self.needs else ("partial" if self.sizes else "needs"),
            "sizes": self.sizes,
            "warnings": self.warnings,
            "needs": self.needs,
            "rejected_quotes": self.rejected_quotes,
            "pitches": self.pitches,
        }


def _norm(text: str) -> str:
    return " ".join(str(text).lower().replace(",", ".").split())


def _number_in(value: float, text: str) -> bool:
    for found in re.findall(r"\d+(?:\.\d+)?", _norm(text)):
        if abs(float(found) - value) <= max(1e-6, abs(value) * 1e-3):
            return True
    return False


def printed_candidates(
    dims: list[dict[str, Any]], text: str, out: Resolved
) -> dict[str, list[Candidate]]:
    """Sizes stated in the input text, each kept only if its quote is really there."""
    found: dict[str, list[Candidate]] = {}
    hay = _norm(text)
    for dim in dims or []:
        axis = str(dim.get("field") or "")
        quote = str(dim.get("quote") or "")
        try:
            value = float(dim.get("value_mm"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            out.rejected_quotes.append({**dim, "why": "value_mm is not a number"})
            continue
        if axis not in AXES:
            out.rejected_quotes.append({**dim, "why": f"field is one of {', '.join(AXES)}"})
            continue
        if not quote.strip() or _norm(quote) not in hay:
            out.rejected_quotes.append({**dim, "why": "the quote is not in the input text"})
            continue
        if not _number_in(value, quote):
            out.rejected_quotes.append({**dim, "why": "the quote does not hold that number"})
            continue
        found.setdefault(axis, []).append(Candidate("printed", value, f'quoted: "{quote}"'))
    return found


def package_candidates(code: str) -> dict[str, list[Candidate]]:
    """A body size from a KiCad footprint name; nothing for a name that states none."""
    if not code:
        return {}
    basis = f"KiCad footprint name {code}"
    metric = re.search(r"_(\d{2})(\d{2})Metric", code)
    sizes: list[float]
    if metric:
        sizes = [int(metric.group(1)) / 10, int(metric.group(2)) / 10]
    else:
        dims = re.search(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?mm", code)
        if not dims:
            return {}
        sizes = [float(g) for g in dims.groups() if g is not None]
    plane = sorted(sizes[:2], reverse=True)
    out = {
        "length": [Candidate("package_nominal", plane[0], basis)],
        "width": [Candidate("package_nominal", plane[1], basis)],
    }
    if len(sizes) > 2:
        out["height"] = [Candidate("package_nominal", sizes[2], basis)]
    return out


def similar_candidates(similar: dict[str, Any] | None) -> dict[str, list[Candidate]]:
    if not similar:
        return {}
    ref = str(similar.get("ref") or "a similar part")
    source = str(similar.get("source_ref") or "")
    out: dict[str, list[Candidate]] = {}
    for axis in AXES:
        value = similar.get(f"{axis}_mm")
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            basis = f"similar part {ref}" + (f" ({source})" if source else "")
            out[axis] = [Candidate("similar_part", float(value), basis)]
    return out


def photo_candidates(
    body_px: dict[str, Any],
    references: list[dict[str, Any]],
    objects: dict[str, dict[str, Any]],
    image_ref: str | None,
    out: Resolved,
) -> dict[str, list[Candidate]]:
    """Each side's size from a scale object seen along it in the photo."""
    found: dict[str, list[Candidate]] = {}
    for ref in references or []:
        axis = str(ref.get("axis") or "")
        kind = str(ref.get("kind") or "")
        obj = objects.get(kind)
        if axis not in AXES:
            out.warnings.append(f"a {kind or 'scale'} reference names no side (axis); skipped")
            continue
        if obj is None:
            out.warnings.append(
                f"no sourced scale object '{kind}'; known: {', '.join(sorted(objects))}"
            )
            continue
        span = ref.get("span_px")
        count = ref.get("count", 1)
        body = (body_px.get(axis) or {}).get("px") if isinstance(body_px.get(axis), dict) else None
        if not _positive(span) or not _positive(count) or not _positive(body):
            out.warnings.append(
                f"the {kind} reference along {axis} needs span_px, count and a body size"
            )
            continue
        px_per_mm = float(span) / (float(count) * float(obj["size_mm"]))  # type: ignore[arg-type]
        value = float(body) / px_per_mm  # type: ignore[arg-type]
        basis = (
            f"{obj['title']}: {count} x {obj['size_mm']} mm over {span} px ({obj['source_ref']})"
        )
        img = (body_px.get(axis) or {}).get("image_ref") or image_ref
        found.setdefault(axis, []).append(Candidate("photo", value, basis, img, px_per_mm))
    return found


def _positive(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _entry(chosen: Candidate, confidence: float, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "value": round(chosen.value, 3),
        "unit": "mm",
        "source_kind": "estimate",
        "method": chosen.method,
        "basis": chosen.basis,
        "image_ref": chosen.image_ref,
        "confidence": round(max(0.05, confidence), 2),
        "checks": checks,
    }


def resolve(request: dict[str, Any]) -> dict[str, Any]:
    """Resolve one reading; see the module docstring for the rules."""
    objects, standard_pitches, pitch_source = load_scale_objects()
    for extra in request.get("scale_objects") or []:
        if not all(extra.get(k) for k in ("kind", "size_mm", "source_ref")):
            raise ScaleError("a scale object the caller adds needs kind, size_mm and source_ref")
        objects[str(extra["kind"])] = {
            "title": str(extra.get("title") or extra["kind"]),
            "measure": str(extra.get("measure") or "length"),
            "size_mm": float(extra["size_mm"]),
            "source_kind": str(extra.get("source_kind") or "user"),
            "source_ref": str(extra["source_ref"]),
        }
    view = str(request.get("view") or "unclear")
    image_ref = request.get("image_ref")
    body_px = request.get("body_px") or {}
    out = Resolved()

    by_axis: dict[str, list[Candidate]] = {axis: [] for axis in AXES}
    for source in (
        printed_candidates(
            request.get("metadata_dims") or [], str(request.get("metadata_text") or ""), out
        ),
        package_candidates(str(request.get("package_code") or "")),
        similar_candidates(request.get("similar_part")),
        photo_candidates(body_px, request.get("references") or [], objects, image_ref, out),
    ):
        for axis, found in source.items():
            by_axis[axis].extend(found)

    scales: dict[str, float] = {}
    for axis in AXES:
        ranked = sorted(by_axis[axis], key=lambda c: _RANK.index(c.method))
        if not ranked:
            continue
        chosen = ranked[0]
        confidence = _BASE_CONFIDENCE[chosen.method]
        if chosen.method == "photo" and view == "angled":
            confidence *= 0.8
        checks = []
        for other in ranked[1:]:
            gap = abs(other.value - chosen.value) / chosen.value
            agrees = gap <= DISAGREE
            checks.append(
                {
                    "method": other.method,
                    "value": round(other.value, 3),
                    "difference": round(gap, 3),
                    "agrees": agrees,
                }
            )
            if not agrees:
                confidence -= 0.15
                out.warnings.append(
                    f"{axis}: {other.method} gives {other.value:.2f} mm, {gap:.0%} from the "
                    f"{chosen.method} {chosen.value:.2f} mm"
                )
        out.sizes[axis] = _entry(chosen, confidence, checks)
        photo = next((c for c in ranked if c.px_per_mm), None)
        if photo is not None:
            scales[axis] = photo.px_per_mm or 0.0

    # A side with no source of its own reuses the other side's photo scale.
    for axis in PLANE:
        side = "width" if axis == "length" else "length"
        body = (body_px.get(axis) or {}).get("px") if isinstance(body_px.get(axis), dict) else None
        if axis in out.sizes or side not in scales or not _positive(body):
            continue
        value = float(body) / scales[side]  # type: ignore[arg-type]
        reused = Candidate(
            "photo",
            value,
            f"reuses the {side} side's photo scale ({scales[side]:.2f} px/mm)",
            (body_px.get(axis) or {}).get("image_ref") or image_ref,
            scales[side],
        )
        out.sizes[axis] = _entry(reused, 0.45, [])
        out.sizes[axis]["reused_scale"] = True
        if view == "angled":
            out.warnings.append(
                f"{axis}: measured with the {side} side's scale in an angled view, so it is "
                "probably too small (foreshortened); a scale along it or a top view fixes it"
            )

    # Pin pitches, snapped to a standard pitch only when close.
    for pitch in request.get("pitches_px") or []:
        axis = str(pitch.get("axis") or "")
        px = pitch.get("px")
        count = pitch.get("count", 1)
        if axis not in scales or not _positive(px) or not _positive(count):
            out.pitches.append({**pitch, "status": "unscaled: no photo scale along that side"})
            continue
        measured = float(px) / float(count) / scales[axis]
        nearest = min(standard_pitches, key=lambda p: abs(p - measured))
        gap = abs(nearest - measured) / nearest
        snapped = gap <= SNAP
        out.pitches.append(
            {
                "axis": axis,
                "measured_mm": round(measured, 3),
                "nearest_standard_mm": nearest,
                "snapped": snapped,
                "value_mm": nearest if snapped else round(measured, 3),
                "difference": round(gap, 3),
                "source_ref": pitch_source if snapped else "",
            }
        )

    if "length" not in out.sizes and "width" not in out.sizes:
        out.needs.append(
            "a size source: a printed size with its quote, a package code, a similar part's "
            "sourced size, or a scale object (ruler, pin header, JST connector) in the photo"
        )
    elif "length" not in out.sizes or "width" not in out.sizes:
        missing = "length" if "length" not in out.sizes else "width"
        out.needs.append(f"a {missing}: a printed {missing}, a similar part or a scale along it")
    if "height" not in out.sizes:
        out.needs.append(
            "a height: a side photo with a scale, a printed height, a package code or a "
            "similar part"
        )
    return out.as_dict()
