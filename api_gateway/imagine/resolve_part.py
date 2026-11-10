"""One call that gives a real part a 3D body, and says where the body came from.

Imagine wants every part to exist in 3D, and never to look better than it is. The rungs,
best first:

1. ``existing``: the part already carries an exact or generated model. Kept as it is.
2. ``kicad``: the KiCad library's own 3D model, sized by a CAD kernel when one is
   available (exact) or estimated from the file (approximation).
3. ``datasheet_package``: a model built from the datasheet's package drawing. Not built
   yet, and listed as such so nobody assumes it was tried.
4. ``approximation``: the closest shape that can be defended, or a placeholder block when
   nothing is known. Always labelled, with its basis.

The answer lists every rung and what happened at it, so an agent can say exactly why a
part is not exact. Nothing is written anywhere: the agent copies ``geometry`` into the
part itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from api_gateway.imagine.kicad_part import Measure, kicad_geometry, measure_step
from api_gateway.imagine.part_model import BETTER, approximate_geometry
from tool_registry.tools.kicad.config import KicadConfig

LIBRARY_ENV = "METAFORGE_KICAD_LIBRARY_DIR"


def kicad_config_from_env(env: Mapping[str, str] | None = None) -> KicadConfig | None:
    """The KiCad folders, from one variable naming the folder that holds ``symbols``,
    ``footprints`` and ``3dmodels``. ``None`` when it is unset or the folder is missing."""
    source = os.environ if env is None else env
    root = str(source.get(LIBRARY_ENV, "")).strip()
    if not root or not (Path(root) / "symbols").is_dir():
        return None
    return KicadConfig(
        symbol_dir=str(Path(root) / "symbols"),
        footprint_dir=str(Path(root) / "footprints"),
        model_dir=str(Path(root) / "3dmodels"),
    )


def resolve_part(
    part: Any, *, kicad_config: KicadConfig | None = None, measure: Measure | None = None
) -> dict[str, Any]:
    """Give ``part`` a 3D body from the best rung available. Never raises on bad input."""
    safe = _clean(part)
    tried: list[dict[str, str]] = []

    existing = safe.get("geometry") or {}
    if existing.get("fidelity") in BETTER and _has_size(existing):
        tried.append(
            {
                "rung": "existing",
                "outcome": "used",
                "why": f"the part already has a {existing['fidelity']} model; kept as it is",
            }
        )
        return _answer(safe, "existing", existing, tried)

    geometry: dict[str, Any] | None = None
    if kicad_config is None:
        tried.append(
            {
                "rung": "kicad",
                "outcome": "skipped",
                "why": (
                    "the KiCad libraries are not installed where this runs "
                    f"(set {LIBRARY_ENV} to the folder that holds symbols, footprints and 3dmodels)"
                ),
            }
        )
    else:
        geometry, note = kicad_geometry(safe, kicad_config, measure=measure or measure_step)
        if geometry is None:
            tried.append({"rung": "kicad", "outcome": "skipped", "why": note})
        else:
            tried.append(
                {
                    "rung": "kicad",
                    "outcome": "used",
                    "why": f"found in the KiCad library; its fidelity is {geometry['fidelity']}",
                }
            )

    tried.append(
        {
            "rung": "datasheet_package",
            "outcome": "not_built",
            "why": "building a model from a datasheet's package drawing is not available yet",
        }
    )

    if geometry is not None:
        tried.append({"rung": "approximation", "outcome": "not_needed", "why": "KiCad gave a body"})
        return _answer(safe, geometry["fidelity"], geometry, tried)

    geometry = approximate_geometry(safe)
    tried.append({"rung": "approximation", "outcome": "used", "why": geometry["fidelity_basis"]})
    return _answer(safe, geometry["fidelity"], geometry, tried)


_MESSAGES = {
    "existing": "The part already has a better model. Nothing to change.",
    "exact": (
        "Exact: the size is from the library's own 3D model, drawn as its bounding box. "
        "Copy `geometry` into the part."
    ),
    "approximation": (
        "An approximation, not the real part. Copy `geometry` into the part and say in "
        "your report that it is an approximation, with its basis."
    ),
    "placeholder": (
        "A placeholder: nothing was known about this part's size, so a generic block "
        "stands in. Copy `geometry` into the part and say in your report that it is generic."
    ),
}


def _answer(
    part: dict[str, Any], rung: str, geometry: dict[str, Any], tried: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "part_id": part.get("id", ""),
        "rung": rung,
        "geometry": geometry,
        "tried": tried,
        "message": _MESSAGES.get(rung, ""),
    }


def _clean(part: Any) -> dict[str, Any]:
    """Keep only the fields this module reads, and only when they have the right type."""
    if not isinstance(part, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in ("id", "role", "name", "mpn", "package"):
        if isinstance(part.get(key), str):
            out[key] = part[key]
    for key in ("geometry", "specs", "kicad"):
        if isinstance(part.get(key), Mapping):
            out[key] = dict(part[key])
    return out


def _has_size(geometry: Mapping[str, Any]) -> bool:
    env = geometry.get("envelope")
    return (
        isinstance(env, list)
        and len(env) == 3
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in env)
    )
