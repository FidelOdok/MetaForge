"""The materials and components seed (FORGE-754).

Sheet goods by thickness, with the stock sizes they are sold in, and the
standard parts that join them, so a laser-cut or CNC route has real sheets
and fasteners to work with on a new install. Each file is one source (a
manufacturer's datasheet or a standard) with the date it was read; every
value cites it, and a joint strength is only seeded where a manufacturer or
standard publishes one. Nothing is a price.

Entries are catalog ``material`` and ``component`` records, registered
through the catalog's own checks. Loading is idempotent by name: an entry
the catalog already holds, seeded or entered by a person, is left as it is.

Layer: stdlib and structlog (twin_core may import nothing above it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

from twin_core.library.machine_seed import SEED_ENV

logger = structlog.get_logger(__name__)

__all__ = ["DATA_DIR", "SEED_AUTHOR", "seed_catalog", "seed_documents"]

DATA_DIR = Path(__file__).parent / "data" / "materials"
#: Recorded as the author of every seeded version.
SEED_AUTHOR = "seed:materials"


def seed_documents() -> list[dict[str, Any]]:
    """Each seed file: ``checked``, ``source`` and its ``entries`` (kind + body)."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA_DIR.glob("*.json"))]


def seed_catalog(catalog: Any) -> dict[str, list[str]]:
    """Add the seed's entries the catalog lacks. Returns what was added, skipped, failed."""
    from twin_core.library.catalog import CatalogNotFoundError

    if (os.environ.get(SEED_ENV) or "").strip().lower() in {"off", "0", "false"}:
        return {"added": [], "skipped": [], "failed": []}
    added: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for doc in seed_documents():
        for entry in doc.get("entries") or []:
            kind, body = entry["kind"], entry["body"]
            name = str(body.get("name") or "")
            try:
                catalog.get(name, kind)
            except CatalogNotFoundError:
                pass
            else:
                skipped.append(name)
                continue
            try:
                catalog.register(kind, body, by=SEED_AUTHOR)
            except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the rest
                logger.warning("material_seed_entry_failed", entry=name, error=str(exc))
                failed.append(name)
                continue
            added.append(name)
    logger.info("material_seed_loaded", added=len(added), skipped=len(skipped), failed=len(failed))
    return {"added": added, "skipped": skipped, "failed": failed}
