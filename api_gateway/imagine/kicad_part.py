"""The exact rung through KiCad: a part's size from the library's own 3D model.

Given a part, this finds its symbol in the installed KiCad libraries, follows the symbol
to its footprint and the footprint to its 3D model, and reads the model's size. It is
cautious about what it claims:

* A part is matched by an explicit ``kicad.symbol`` or by its order code equal to a
  symbol name, ignoring case. Never a near match, and never when two libraries carry
  the same name.
* A size measured by a CAD kernel is ``exact``. A size estimated from the model file's
  coordinates is only an ``approximation``, because the estimate can be a little off (it
  gave 0.8 mm for a model the kernel measured at 0.78 mm).
* With no model file, or one that cannot be measured, the footprint's own name carries
  the size as an approximation.
* Nothing is guessed: with no match, it returns ``None`` and a note saying why.

The shape drawn is the model's bounding box. The basis says so, and names the model by
its path inside the library, its checksum and the licence the file states, never a
local path. Pins and interfaces are not read here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from api_gateway.imagine.part_model import approximate_geometry
from tool_registry.tools.kicad.library import describe

_STEP_SUFFIXES = (".step", ".stp")
_SYMBOL_NAME = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)
_POINT = re.compile(
    r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*,"
    r"\s*([-+0-9.eE]+)\s*\)\s*\)"
)
_LICENCE = re.compile(r"Creative Commons (CC-[A-Z\-]+ [0-9.]+)")


@dataclass(frozen=True)
class StepSize:
    """A model's bounding-box size in mm and how it was obtained."""

    size: list[float]
    method: Literal["cad_kernel", "text_estimate"]


Measure = Callable[[Path], StepSize | None]


def estimate_step_size(path: Path) -> StepSize | None:
    """Estimate a STEP file's size from the extent of its coordinates.

    It needs no CAD kernel, but it also counts the model's placement points and ignores
    transforms and arcs, so it is only ever reported as an estimate.
    """
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    points = [(float(a), float(b), float(c)) for a, b, c in _POINT.findall(text)]
    if len(points) < 4:
        return None
    size = [round(max(p[i] for p in points) - min(p[i] for p in points), 3) for i in range(3)]
    if not all(s > 0 for s in size):
        return None
    return StepSize(size=size, method="text_estimate")


def kicad_geometry(
    part: dict[str, Any], config: Any, *, measure: Measure = estimate_step_size
) -> tuple[dict[str, Any] | None, str]:
    """Return ``(geometry, note)``: the part's geometry from KiCad, or ``None`` and why."""
    symbol_id, why = _symbol_for(part, config)
    if symbol_id is None:
        return None, why
    try:
        described = describe(config, symbol_id)
    except (ValueError, LookupError, OSError) as exc:
        return None, f"KiCad could not describe {symbol_id!r}: {exc}"
    footprint = described.get("footprint") or ""
    if not footprint:
        return None, f"{symbol_id} has no footprint, so there is nothing to draw"

    base = dict(part.get("geometry") or {})
    model = next(
        (
            m
            for m in described.get("models", [])
            if m.get("available") and str(m.get("path", "")).lower().endswith(_STEP_SUFFIXES)
        ),
        None,
    )
    measured = measure(Path(model["path"])) if model else None
    if model and measured:
        return _from_model(base, symbol_id, Path(model["path"]), config, measured), ""

    reason = (
        "its 3D model could not be measured"
        if model
        else f"KiCad's footprint {footprint} has no 3D model file here"
    )
    guess = approximate_geometry({**part, "geometry": {**base, "footprint": footprint}})
    guess["fidelity_basis"] = f"{reason}; {guess['fidelity_basis']}"
    guess["source"] = f"KiCad library {symbol_id}, footprint {footprint}"
    return guess, ""


def _from_model(
    base: dict[str, Any], symbol_id: str, path: Path, config: Any, measured: StepSize
) -> dict[str, Any]:
    root = Path(config.model_dir)
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    licence = _LICENCE.search(path.read_text(errors="ignore")[:4000])
    licence_text = (
        f"licence {licence.group(1)}, as the file states"
        if licence
        else ("licence not stated in the file")
    )
    size = [round(float(x), 3) for x in measured.size]
    if measured.method == "cad_kernel":
        fidelity = "exact"
        how = f"size measured by a CAD kernel from the KiCad library model {relative}"
    else:
        fidelity = "approximation"
        how = (
            f"size estimated from the model's coordinates, not measured by a CAD kernel, "
            f"for the KiCad library model {relative}"
        )
    basis = f"{how} (sha256 {digest}; {licence_text}); drawn as its bounding box"
    out = dict(base)
    out["envelope"] = size
    out["body"] = [{"shape": "box", "at": [0, 0, size[2] / 2], "size": list(size)}]
    out["fidelity"] = fidelity
    out["fidelity_basis"] = basis
    out["source"] = f"KiCad library {symbol_id}"
    return out


def _symbol_for(part: dict[str, Any], config: Any) -> tuple[str | None, str]:
    given = str((part.get("kicad") or {}).get("symbol") or "").strip()
    if given:
        if ":" not in given or ".." in given:
            return None, f"{given!r} is not a library:symbol id"
        return given, ""
    mpn = str(part.get("mpn") or "").strip()
    if not mpn:
        return None, "no kicad.symbol and no order code to match a symbol by"
    root = Path(config.symbol_dir)
    if not root.is_dir():
        return None, "the KiCad symbol libraries are not installed"
    found = [
        f"{source.stem}:{name}"
        for source in sorted(root.glob("*.kicad_sym"))
        for name in _SYMBOL_NAME.findall(source.read_text(errors="ignore"))
        if name.lower() == mpn.lower()
    ]
    if not found:
        return None, f"no symbol named {mpn!r} in the KiCad libraries"
    if len(found) > 1:
        return None, f"{mpn!r} is in more than one library ({', '.join(found)}); not guessing"
    return found[0], ""
