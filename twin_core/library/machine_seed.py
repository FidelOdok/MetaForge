"""The machine models seed (FORGE-753).

Common machines' published specifications, so the library is not empty on a
new install: a route or DFM phase can compare a design with a real laser
cutter, printer or router instead of generic process rules. Each file is one
category with the date its pages were read, and every capability is a
``manufacturer_spec`` citing the official page it came from. Nothing is
invented and nothing is a price.

Only *models* are seeded: what a make and model can do. A person's own
machine is an *instance* they register, pointing at a model and overriding
what they measured. Loading is idempotent by make and model: a model the
library already holds, seeded or entered by a person, is left exactly as it
is.

Layer: stdlib and structlog (twin_core may import nothing above it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["DATA_DIR", "SEED_AUTHOR", "SEED_ENV", "seed_documents", "seed_machine_library"]

DATA_DIR = Path(__file__).parent / "data" / "machine_models"
#: Recorded as the author of every seeded version, so seeded entries can be
#: told from a person's and refreshed later.
SEED_AUTHOR = "seed:machine_models"
#: ``off`` stops the gateway seeding at start.
SEED_ENV = "METAFORGE_LIBRARY_SEED"


def seed_documents() -> list[dict[str, Any]]:
    """Each seed file: ``checked`` (date), ``category`` and its ``models``."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA_DIR.glob("*.json"))]


def _register_body(model: dict[str, Any]) -> dict[str, Any]:
    """A seed model as ``SqliteMachineLibrary.register`` takes it."""
    capabilities = {
        key: {"value": cap["value"], "source_kind": "manufacturer_spec", "source_ref": cap["url"]}
        for key, cap in (model.get("capabilities") or {}).items()
    }
    return {
        "layer": "model",
        "kind": model["kind"],
        "make": model["make"],
        "model": model["model"],
        "processes": list(model.get("processes") or []),
        "capabilities": capabilities,
    }


def _key(make: str, model: str) -> tuple[str, str]:
    return make.strip().lower(), model.strip().lower()


def seed_machine_library(library: Any) -> dict[str, list[str]]:
    """Add the seed's models the library lacks. Returns what was added and skipped.

    A seed model that fails the library's own checks is skipped and logged,
    never half-written: the rest still load.
    """
    if (os.environ.get(SEED_ENV) or "").strip().lower() in {"off", "0", "false"}:
        return {"added": [], "skipped": [], "failed": []}
    held = {_key(m.make, m.model) for m in library.list(layer="model")}
    added: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for doc in seed_documents():
        for model in doc.get("models") or []:
            name = f"{model['make']} {model['model']}"
            if _key(model["make"], model["model"]) in held:
                skipped.append(name)
                continue
            try:
                library.register(_register_body(model), by=SEED_AUTHOR)
            except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the rest
                logger.warning("machine_seed_entry_failed", model=name, error=str(exc))
                failed.append(name)
                continue
            held.add(_key(model["make"], model["model"]))
            added.append(name)
    logger.info("machine_seed_loaded", added=len(added), skipped=len(skipped), failed=len(failed))
    return {"added": added, "skipped": skipped, "failed": failed}
