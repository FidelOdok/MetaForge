"""The ergonomic limits seed (FORGE-635).

Design limits read from public-domain standards, each cited to its clause or
figure, for the ``ergonomic_limit`` library kind. Nothing here is a default:
an install registers the seed (``POST /v1/library/ergonomic-limits`` or
``library.register_ergonomic_limit``) and every entry is versioned like any
other library entry.

Today's seed is MIL-STD-1472H (2020). Its criteria are for US military
users, the central 90 percent (95 percent preferred) of males and of
females, which every entry states as its population; ``check_ergonomics``
reports a limit whose population differs from a product's users.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data" / "ergonomic_limits"


def seed_documents() -> list[dict[str, Any]]:
    """Each seed file: its ``source`` (document, url, licence) and ``entries``."""
    return [json.loads(p.read_text()) for p in sorted(DATA_DIR.glob("*.json"))]


def seed_entries() -> list[dict[str, Any]]:
    """Every seed entry, ready for ``register("ergonomic_limit", entry)``."""
    return [entry for doc in seed_documents() for entry in doc["entries"]]
