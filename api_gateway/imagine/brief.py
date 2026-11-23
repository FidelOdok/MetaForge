"""A brief studio: a size limit in place of a drawn form (FORGE-917, FORGE-918).

A product led by its electronics or function does not need its form drawn in Imagine:
Develop designs the real one. A brief studio writes ``form: {template: "brief",
envelope_mm: [L, W, H]}`` and keeps every other part of the config (ledger, assumptions,
scenarios, requirements, parts, interfaces, manufacturing, wiring).

Before a config is checked or stored, :func:`expand` turns the brief form into an
authored form with one generated part, the size limit, so the formgraph checks, the
engine page, the look and the verification service need no second path. The stored form
keeps ``brief: {envelope_mm}``, and every write expands again from it, so the box can
never drift from the limit the studio states.
"""

from __future__ import annotations

import copy
import math
from typing import Any

TEMPLATE = "brief"
PART_ID = "size-limit"
PART_NAME = "Size limit"

#: A size limit outside these bounds is a typo, not a product.
_MIN_MM = 0.1
_MAX_MM = 100_000.0

_CODE = "return [g.box(p.env_l, p.env_h, p.env_w).at(0, p.env_h / 2, 0)];"


class BriefError(ValueError):
    """A brief form that cannot be expanded."""


def is_brief(config: dict[str, Any] | None) -> bool:
    """Whether ``config`` (raw or expanded) is a brief studio."""
    form = (config or {}).get("form")
    if not isinstance(form, dict):
        return False
    return form.get("template") == TEMPLATE or isinstance(form.get("brief"), dict)


def envelope_of(config: dict[str, Any] | None) -> list[float] | None:
    """The size limit ``[L, W, H]`` mm of a brief studio (CAD axes, Z up), or ``None``."""
    form = (config or {}).get("form")
    if not isinstance(form, dict):
        return None
    if form.get("template") == TEMPLATE:
        raw = form.get("envelope_mm")
    else:
        raw = (
            (form.get("brief") or {}).get("envelope_mm")
            if isinstance(form.get("brief"), dict)
            else None
        )
    try:
        return _envelope(raw)
    except BriefError:
        return None


def _envelope(raw: Any) -> list[float]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise BriefError("form.envelope_mm must be [length, width, height] in millimetres.")
    out = []
    for name, value in zip(("length", "width", "height"), raw, strict=True):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise BriefError(f"form.envelope_mm {name} must be a number.")
        if not _MIN_MM <= float(value) <= _MAX_MM:
            raise BriefError(
                f"form.envelope_mm {name} must be between {_MIN_MM} and {_MAX_MM:g} mm."
            )
        out.append(float(value))
    return out


def expand(config: dict[str, Any]) -> dict[str, Any]:
    """``config`` with a brief form expanded to its size-limit part; others unchanged.

    Raises :class:`BriefError` for a brief form without a usable ``envelope_mm``.
    """
    if not is_brief(config):
        return config
    out = copy.deepcopy(config)
    form = out["form"]
    if form.get("template") == TEMPLATE:
        raw = form.get("envelope_mm")
    else:
        raw = (form.get("brief") or {}).get("envelope_mm")
    length, width, height = _envelope(raw)
    form.pop("envelope_mm", None)
    form["template"] = "authored"
    form["brief"] = {"envelope_mm": [length, width, height]}
    params = form.get("params") if isinstance(form.get("params"), dict) else {}
    params.update({"env_l": length, "env_w": width, "env_h": height})
    form["params"] = params
    authored = form.get("authored") if isinstance(form.get("authored"), dict) else {}
    keep = [
        p
        for p in authored.get("parts") or []
        if isinstance(p, dict) and str(p.get("id")) != PART_ID
    ]
    previous = next(
        (p for p in authored.get("parts") or [] if isinstance(p, dict) and p.get("id") == PART_ID),
        {},
    )
    part = {
        "id": PART_ID,
        "name": PART_NAME,
        "kind": "generated",
        "uses": ["env_l", "env_w", "env_h"],
        "code": _CODE,
    }
    # What the studio took from Develop (FORGE-915) stays on the part.
    if isinstance(previous.get("develop_form"), dict):
        part["develop_form"] = previous["develop_form"]
    authored["parts"] = [part, *keep]
    form["authored"] = authored
    return out
