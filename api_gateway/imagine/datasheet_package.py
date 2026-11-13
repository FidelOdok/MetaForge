"""The datasheet rung: a part's size from its package outline text.

The agent reads the package outline section of a datasheet and passes that text. A
rule-based parser (``digital_twin.datasheets.package_spec``) reads it. It understands
standard IC outlines in the TI/JEDEC millimetre format (SOIC, SOP, SSOP, TSSOP, MSOP,
QFP, QFN, DIP and similar), and it says so when it does not recognise the text.

What this returns is a box that encloses the package and its leads, sized from the
drawing, so its size is read. It is labelled an approximation, because a box is not the
package: ``generated`` is kept for a model a CAD generator builds from the same numbers.
The parser can misread an unusual layout, so the basis names it as a rule-based reading
and the agent is told to check it against the drawing.

Nothing here fetches anything. The gateway never opens a datasheet or a path an agent
names: the text arrives in the call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from digital_twin.datasheets.package_spec import extract_ic_package_spec

#: The longest outline text accepted, in characters. An outline page is a few hundred.
MAX_TEXT = 30_000

_COVERS = "standard IC outlines in the TI/JEDEC millimetre format (SOIC, QFP, QFN, DIP and similar)"


def package_geometry(part: Mapping[str, Any], text: Any) -> tuple[dict[str, Any] | None, str]:
    """Return ``(geometry, note)``: the part's geometry from the outline, or ``None`` and why."""
    if not isinstance(text, str):
        return None, "package_text must be text, the package outline section of the datasheet"
    if not text.strip():
        return None, "package_text is empty"
    if len(text) > MAX_TEXT:
        return None, f"package_text is too long ({len(text)} characters; the limit is {MAX_TEXT})"
    try:
        spec = extract_ic_package_spec(text)
    except Exception:  # the parser is rule-based; unusual text must not break a write
        spec = None
    if spec is None:
        return None, f"the text is not an outline this parser reads: it covers {_COVERS}"
    height = spec.get("height")
    if not height:
        return None, "the outline gave no maximum height, so the size is not claimed as read"

    length = float(spec["body_length"])
    across = max(float(spec["body_width"]), float(spec["lead_span"]))
    size = [round(length, 3), round(across, 3), round(float(height), 3)]
    family = spec["package_type"]
    leads = spec["lead_count"]
    basis = (
        f"size read from the datasheet's package outline by a rule-based parser: {family}-{leads}, "
        f"body {spec['body_length']:g} x {spec['body_width']:g} mm, leads span "
        f"{spec['lead_span']:g} mm, maximum height {height:g} mm; drawn as a box enclosing the "
        "leads. Check it against the drawing"
    )
    out = dict(part.get("geometry") or {})
    out["envelope"] = size
    out["body"] = [{"shape": "box", "at": [0, 0, size[2] / 2], "size": list(size)}]
    out["fidelity"] = "approximation"
    out["fidelity_basis"] = basis
    out["size_basis"] = "read"
    out["source"] = f"datasheet package outline ({family}-{leads})"
    return out, ""
