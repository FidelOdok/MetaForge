"""A studio's real parts point at the component library (MetaForge, 2026-11-11).

A part researched in one studio (its datasheet, size, interfaces) was lost to every other
project, so the next one searched again. Each real part in ``context.components.parts`` may
name its library entry in ``library``. On a write the studio says which real parts are not
linked, and which name an entry the library does not have, so the agent registers a part
once (``library.register_component``) and later projects find it with
``library.list_components``.

These are warnings. A registration waits for a person's approval, so a fresh link can
point at an entry that is not there yet; refusing the write would stall the studio on it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: The part field that names a library entry.
FIELD = "library"


def _parts(config: dict[str, Any]) -> list[dict[str, Any]]:
    comps = (config.get("context") or {}).get("components") or {}
    return [p for p in comps.get("parts") or [] if isinstance(p, dict)]


def library_notes(config: dict[str, Any], exists: Callable[[str], bool]) -> list[str]:
    """One note for the real parts with no library link, one per link that finds nothing.

    ``exists`` answers whether the library has a component of that name. When it fails
    (the library cannot be read), the check is skipped and logged, never guessed.
    """
    unlinked: list[str] = []
    notes: list[str] = []
    for p in _parts(config):
        if p.get("standIn"):
            continue
        pid = str(p.get("id") or p.get("name") or "?")
        name = str(p.get(FIELD) or "").strip()
        if not name:
            unlinked.append(pid)
            continue
        try:
            found = exists(name)
        except Exception as exc:
            logger.warning("imagine_library_lookup_failed", part=pid, entry=name, error=str(exc))
            continue
        if not found:
            notes.append(
                f'part {pid} names library entry "{name}", which is not in the library: '
                "it may still be waiting for approval, or the name is wrong "
                "(library.list_components lists them)."
            )
    if unlinked:
        notes.insert(
            0,
            f"{len(unlinked)} real part(s) not in the component library: "
            f"{', '.join(unlinked)}. Register each once with library.register_component "
            '(family module, battery or electronic) and set the part\'s "library" to the '
            "entry name, so later projects reuse it.",
        )
    return notes


def catalog_exists(name: str) -> bool:
    """Whether the gateway's component library has an entry of this name or id."""
    from api_gateway.library.catalog_routes import get_catalog
    from twin_core.library.catalog import CatalogNotFoundError

    try:
        get_catalog().get(name, "component")
    except CatalogNotFoundError:
        return False
    return True
