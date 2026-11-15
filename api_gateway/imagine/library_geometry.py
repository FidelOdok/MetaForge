"""A linked part's size, from its component library entry (FORGE-877, 2026-11-15).

A real part that names its library entry (``library``) took its price from the entry's
offers (``library_cost``) but never its size: the agent typed an envelope in, often one the
library already held, and a part the agent left unsized failed the handover's ``parts``
item although its entry had ``length_mm``, ``width_mm`` and ``height_mm``.

On each studio write, before any other rung draws a body, a linked part gets its envelope
from the entry's typed size: ``[length, width, height]`` in millimetres, ``size_basis``
``read``, ``derived_from: "library"``, with the entry, its version and the spec's source.

- An exact or generated model on the part (a measured STEP, a KiCad body) is better than an
  outline and is kept.
- Any other size on a linked part is replaced, and the note names the typed value and the
  library's, so a disagreement is visible rather than silent.
- An entry with only part of a size (a footprint and no height) is not used: the note says
  which field the library lacks, and the next rung (KiCad, a datasheet, an approximation)
  draws the part. A size derived from the library earlier is dropped if the entry no
  longer has one, so a withdrawn value leaves the studio.

The body is the entry's bounding box; a part whose entry has a drawn model (``form_code``)
is still drawn with that model by ``component_link`` and ``library_models``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import structlog

from api_gateway.imagine.part_model import BETTER
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.library_geometry")

MARK = "library"
#: The entry's typed size fields, in the order of a part's ``envelope``.
SIZE_FIELDS = ("length_mm", "width_mm", "height_mm")

Lookup = Callable[[str], "dict[str, Any] | None"]


def library_size(entry: dict[str, Any]) -> tuple[list[float] | None, list[str], str]:
    """The entry's ``[length, width, height]``, the fields it lacks, and where they came from."""
    raw = entry.get("specs")
    specs: dict[str, Any] = raw if isinstance(raw, dict) else {}
    size: list[float] = []
    missing: list[str] = []
    refs: list[str] = []
    for field in SIZE_FIELDS:
        spec = specs.get(field)
        if not isinstance(spec, dict):
            missing.append(field)
            continue
        value = spec.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            size.append(float(value))
            ref = str(spec.get("source_ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        else:
            missing.append(field)
    if missing:
        return None, missing, ""
    source = ", ".join(refs) or str(entry.get("datasheet") or "").strip() or "source not stated"
    return size, [], source


def _fmt(size: Any) -> str:
    if not isinstance(size, list):
        return "?"
    return " x ".join(f"{float(x):g}" for x in size if isinstance(x, (int, float)))


def _geometry(name: str, entry: dict[str, Any], size: list[float], source: str) -> dict[str, Any]:
    version = entry.get("version")
    where = f"library {name} v{version}" if version is not None else f"library {name}"
    return {
        "envelope": size,
        "body": [{"shape": "box", "at": [0, 0, size[2] / 2], "size": list(size)}],
        "fidelity": "approximation",
        "fidelity_basis": (
            f"outline {_fmt(size)} mm from {where} (the maker's stated dimensions); "
            "drawn as its bounding box"
        ),
        "size_basis": "read",
        "source": f"{where}: {source}",
        "derived_from": MARK,
    }


def derive_part_geometry(
    config: dict[str, Any], lookup: Lookup
) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with each linked part's envelope taken from the library, and notes."""
    out = copy.deepcopy(config)
    comps = (out.get("context") or {}).get("components") if isinstance(out, dict) else None
    parts = comps.get("parts") if isinstance(comps, dict) else None
    if not isinstance(parts, list):
        return out, []
    notes: list[str] = []
    with tracer.start_as_current_span("imagine.library_geometry") as span:
        derived = 0
        for part in parts:
            if not isinstance(part, dict) or part.get("standIn") or part.get("excluded"):
                continue
            name = str(part.get(MARK) or "").strip()
            if not name:
                continue
            try:
                entry = lookup(name)
            except Exception as exc:  # noqa: BLE001 - a library read never fails the write
                span.record_exception(exc)
                logger.warning("imagine_library_geometry_lookup_failed", entry=name, error=str(exc))
                continue
            if entry is None:
                continue  # the library link check says so
            pid = str(part.get("id") or name)
            own = part.get("geometry") if isinstance(part.get("geometry"), dict) else None
            size, missing, source = library_size(entry)
            if size is None:
                if own is not None and own.get("derived_from") == MARK:
                    part.pop("geometry", None)  # the entry no longer has the size it gave
                    own = None
                partial = len(missing) < len(SIZE_FIELDS)
                if own is None or not own.get("envelope") or partial:
                    notes.append(
                        f'part "{pid}": library entry {name} has no {", ".join(missing)}, so '
                        "its size is not taken from the library; add it to the entry (sourced) "
                        "so every studio that links it gets it"
                    )
                continue
            if (
                own is not None
                and own.get("fidelity") in BETTER
                and own.get("derived_from") != MARK
            ):
                continue  # a measured or generated model beats an outline
            fresh = _geometry(name, entry, size, source)
            if own is not None and own.get("envelope") and own.get("derived_from") != MARK:
                notes.append(
                    f'part "{pid}": its typed size {_fmt(own.get("envelope"))} mm was replaced by '
                    f"{_fmt(size)} mm from library entry {name}"
                )
            elif own is None or own.get("envelope") != size:
                notes.append(f'part "{pid}": size {_fmt(size)} mm from library entry {name}')
            part["geometry"] = fresh
            derived += 1
        span.set_attribute("imagine.library_geometry.derived", derived)
        if derived:
            logger.info("imagine_library_geometry_derived", parts=derived)
    return out, notes
