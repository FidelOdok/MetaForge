"""Database migration helpers for MetaForge Gateway.

:data:`MIGRATIONS` is the ordered list of numbered migrations that
:func:`api_gateway.db.schema.ensure_schema` applies once each and records in
``schema_migrations`` (FORGE-856).

Most schema changes do **not** belong here. A new table is a new model in
``api_gateway/db/models.py``; a new nullable or defaulted column is a model
change plus a line in ``ADDITIVE_COLUMNS``. Both are applied idempotently on
every start.

Add a migration only for what cannot be additive: dropping or renaming a
column or table, changing a type, or back-filling data. Rules:

* versions only increase, and a released migration is never edited;
* a drop or rename ships at least one release after the code stopped using
  the thing it removes (expand, then contract), so rolling back one release
  still works;
* keep semicolons to statement ends -- statements are split on them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """One numbered schema change, applied once."""

    version: int
    name: str
    sql: str


#: Applied in version order. Empty until a change cannot be additive.
MIGRATIONS: tuple[Migration, ...] = ()
