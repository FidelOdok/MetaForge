"""SQLite implementation of the existing typed component query contract."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import structlog

from digital_twin.catalog.query import (
    CatalogQuery,
    CatalogQueryResult,
    ComponentCatalogRow,
    build_sql,
)
from digital_twin.catalog.taxonomy import CATEGORY_REGISTRY

logger = structlog.get_logger(__name__)


class CatalogStore(Protocol):
    async def query(self, catalog_query: CatalogQuery) -> CatalogQueryResult: ...
    async def get(self, mpn: str, manufacturer: str) -> ComponentCatalogRow | None: ...
    async def upsert(self, row: ComponentCatalogRow) -> ComponentCatalogRow: ...


class SqliteComponentCatalogStore:
    """Durable local index. Unknown fields/operators use the same validation as PG."""

    def __init__(self, path: str | None = None) -> None:
        target = (
            path
            or os.getenv("METAFORGE_COMPONENT_CATALOG_PATH")
            or str(Path.home() / ".metaforge/component_catalog.db")
        )
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(target)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS component_index "
            "(mpn TEXT, manufacturer TEXT, category TEXT, body TEXT NOT NULL, "
            "PRIMARY KEY(mpn,manufacturer))"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS component_index_category ON component_index(category)"
        )
        self._conn.commit()

    async def initialize(self) -> None:
        """Match the Postgres lifecycle contract; construction provisions SQLite."""

    async def close(self) -> None:
        self._conn.close()

    async def upsert(self, row: ComponentCatalogRow) -> ComponentCatalogRow:
        category = CATEGORY_REGISTRY.get(row.category)
        if category is None:
            raise ValueError(f"unknown category {row.category!r}")
        for key, value in row.specs.items():
            field = category.field(key)
            if field is None:
                raise ValueError(f"unknown specification {key!r}")
            if value is not None and field.type in {"float", "int"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be numeric in canonical units")
                if not math.isfinite(value):
                    raise ValueError(f"{key} must be finite")
        existing = await self.get(row.mpn, row.manufacturer)
        if existing:
            row = replace(row, id=existing.id, indexed_at=existing.indexed_at)
        self._conn.execute(
            "INSERT INTO component_index VALUES (?,?,?,?) ON CONFLICT(mpn,manufacturer) "
            "DO UPDATE SET category=excluded.category,body=excluded.body",
            (
                row.mpn,
                row.manufacturer,
                row.category,
                json.dumps(asdict(row), default=str, allow_nan=False),
            ),
        )
        self._conn.commit()
        return row

    @staticmethod
    def _decode(body: str) -> ComponentCatalogRow:
        data = json.loads(body)
        data["id"] = UUID(data["id"])
        data["indexed_at"] = datetime.fromisoformat(data["indexed_at"])
        return ComponentCatalogRow(**data)

    async def get(self, mpn: str, manufacturer: str) -> ComponentCatalogRow | None:
        found = self._conn.execute(
            "SELECT body FROM component_index WHERE mpn=? AND manufacturer=?", (mpn, manufacturer)
        ).fetchone()
        return self._decode(found[0]) if found else None

    async def query(self, catalog_query: CatalogQuery) -> CatalogQueryResult:
        started = time.monotonic()
        q = catalog_query
        build_sql(q)  # Canonical field/operator/category validation before compiling SQLite.
        values: list[Any] = []
        clauses: list[str] = []

        def expr(name: str) -> str:
            spec = CATEGORY_REGISTRY.get(q.category or "")
            field = spec.field(name) if spec else None
            path = f"$.specs.{name}" if field else f"$.{name}"
            return f"json_extract(body, '{path}')"

        if q.category:
            clauses.append("category=?")
            values.append(q.category)
        filters = [(f.property, f.op, f.value) for f in q.filters]
        if q.lifecycle_in:
            filters.append(("lifecycle", "in", q.lifecycle_in))
        if q.max_cost_usd is not None:
            filters.append(("cost_usd", "<=", q.max_cost_usd))
        for prop, op, value in filters:
            spec = CATEGORY_REGISTRY.get(q.category or "")
            field = spec.field(prop) if spec else None
            if field and field.type in {"float", "int"}:
                candidates = value if op in {"in", "not_in"} else [value]
                for candidate in candidates:
                    if candidate is not None and (
                        isinstance(candidate, bool)
                        or not isinstance(candidate, (int, float))
                        or not math.isfinite(candidate)
                    ):
                        raise ValueError(f"{prop} requires finite numeric query values")
            term = expr(prop)
            if op in {"in", "not_in"}:
                # NULL never satisfies an engineering constraint, even NOT IN [].
                if not value:
                    clauses.append("0" if op == "in" else f"{term} IS NOT NULL")
                else:
                    clauses.append(
                        f"{term} {'NOT IN' if op == 'not_in' else 'IN'} "
                        f"({','.join('?' for _ in value)})"
                    )
                    values.extend(value)
            else:
                clauses.append(f"{term} {'=' if op == '==' else '<>' if op == '!=' else op} ?")
                values.append(value)
            spec = CATEGORY_REGISTRY.get(q.category or "")
            if q.min_confidence is not None and spec and spec.field(prop):
                clauses.append(f"json_extract(body, '$.extraction_meta.{prop}.confidence') >= ?")
                values.append(q.min_confidence)
        sql = "SELECT body FROM component_index"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if q.order_by:
            direction = "DESC" if q.order_desc else "ASC"
            nulls = "FIRST" if q.order_desc else "LAST"
            sql += f" ORDER BY {expr(q.order_by)} {direction} NULLS {nulls},mpn,manufacturer"
        else:
            sql += " ORDER BY mpn,manufacturer"
        sql += " LIMIT ? OFFSET ?"
        values.extend([max(0, q.limit), max(0, q.offset)])
        rows = [self._decode(r[0]) for r in self._conn.execute(sql, values)]
        elapsed = (time.monotonic() - started) * 1000
        logger.info("local_component_query", rows=len(rows), query_time_ms=elapsed)
        return CatalogQueryResult(rows, elapsed)
