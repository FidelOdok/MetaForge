"""A linked part's identity, from its component library entry (FORGE-896, 2026-11-17).

A studio part that names its library entry (``library``) already takes its size, model and
price from the entry on each write. Its maker, order code, datasheet and title it did not:
the agent had to read the entry (``library.get_component``, a large result paged through
``read_tool_result``) and copy them across before the studio's own checks would accept the
part. On the sandbox that reading used most of a 40-step Imagine design turn, and the turn
ended before the studio was written.

On each write, a linked part's empty ``manufacturer``, ``mpn``, ``datasheet``, ``name`` and
``retrieved`` are filled from the entry, and the part records ``facts_from`` naming the
entry and its version. A value the agent wrote is kept, never replaced: the agent may know
better (a different order code of the same part). Interfaces are not copied: the library's
pin names are not always valid studio interface ids, and the studio's wiring needs its own.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.library_facts")

MARK = "library"
#: Part field <- library entry field, filled only when the part leaves it empty.
FIELDS: tuple[tuple[str, str], ...] = (
    ("manufacturer", "manufacturer"),
    ("mpn", "mpn"),
    ("datasheet", "datasheet"),
    ("name", "title"),
)

Lookup = Callable[[str], dict[str, Any] | None]


def derive_part_facts(config: dict[str, Any], lookup: Lookup) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with each linked part's empty identity fields filled, and notes."""
    out = copy.deepcopy(config)
    comps = (out.get("context") or {}).get("components") if isinstance(out, dict) else None
    parts = comps.get("parts") if isinstance(comps, dict) else None
    if not isinstance(parts, list):
        return out, []
    filled: list[str] = []
    with tracer.start_as_current_span("imagine.library_facts") as span:
        for part in parts:
            if not isinstance(part, dict):
                continue
            name = str(part.get(MARK) or "").strip()
            if not name:
                continue
            try:
                entry = lookup(name)
            except Exception as exc:  # noqa: BLE001 - the link check reports a missing entry
                logger.warning("imagine_library_facts_lookup_failed", entry=name, error=str(exc))
                continue
            if not entry:
                continue
            took = []
            for field, source in FIELDS:
                value = entry.get(source)
                if (
                    not str(part.get(field) or "").strip()
                    and isinstance(value, str)
                    and value.strip()
                ):
                    part[field] = value.strip()
                    took.append(field)
            if not str(part.get("retrieved") or "").strip() and entry.get("updated_at"):
                part["retrieved"] = time.strftime(
                    "%Y-%m-%d", time.gmtime(float(entry["updated_at"]))
                )
                took.append("retrieved")
            if took:
                part["facts_from"] = f"library {name} v{entry.get('version')}: {', '.join(took)}"
                filled.append(f"{part.get('id') or name} ({', '.join(took)})")
        span.set_attribute("library_facts.parts", len(filled))
    notes = []
    if filled:
        notes.append(
            f"{len(filled)} linked part(s) took their maker, order code, datasheet or title from "
            f"the library: {'; '.join(filled)}."
        )
        logger.info("imagine_library_facts_filled", parts=len(filled))
    return out, notes
