"""PostgreSQL-backed store for the parametric component catalog (MET-436).

Follows the ``digital_twin.memory.pgvector_store`` / ``lightrag_service``
precedent: ``digital_twin`` owns its own direct Postgres access via raw
``asyncpg``, independent of ``api_gateway``'s SQLAlchemy ORM tables (the
``digital_twin`` layer may not import ``api_gateway`` — see
``digital_twin/CLAUDE.md``). This is not a Twin graph node type; it's an
independent search index keyed by ``(mpn, manufacturer)``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from digital_twin.catalog.query import (
    CatalogQuery,
    CatalogQueryResult,
    ComponentCatalogRow,
    build_sql,
)
from digital_twin.catalog.taxonomy import CATEGORY_REGISTRY, CategorySpec
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.catalog.store")

# Mirrors taxonomy._VALID_NAME — re-declared locally (not imported) since
# it's a private taxonomy-module invariant, not a shared public contract.
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

_CAST_SQL: dict[str, str] = {
    "float": "::double precision",
    "int": "::bigint",
    "bool": "::boolean",
    "str": "",
    "enum": "",
}


def _sql_literal(value: str) -> str:
    """Single-quote-escape a literal for embedding directly in DDL text.

    Only ever called with category names, which ``taxonomy._register()``
    already validates against ``_VALID_NAME`` at import time — this is
    never used for a user-supplied query value (those always go through
    parameterized placeholders in ``query.build_sql``).
    """
    return "'" + value.replace("'", "''") + "'"


def _index_name(category: str, field_name: str) -> str:
    """Stable, <=63-byte index identifier (Postgres' identifier length cap)."""
    raw = f"idx_cc_{category}_{field_name}"
    if len(raw) <= 63:
        return raw
    digest = hashlib.sha1(raw.encode()).hexdigest()[:10]
    return f"idx_cc_{digest}"


def schema_statements(taxonomy: dict[str, CategorySpec] | None = None) -> list[str]:
    """DDL that provisions ``component_catalog`` plus per-category expression indexes.

    Returned as an ordered list so ``initialize()`` can run them in
    sequence and unit tests can assert the schema shape without a live
    database.
    """
    registry = CATEGORY_REGISTRY if taxonomy is None else taxonomy
    statements = [
        """
        CREATE TABLE IF NOT EXISTS component_catalog (
            id UUID PRIMARY KEY,
            mpn TEXT NOT NULL,
            manufacturer TEXT NOT NULL,
            category TEXT NOT NULL,
            purchase_unit TEXT NOT NULL,
            cost_usd DOUBLE PRECISION,
            lifecycle TEXT NOT NULL DEFAULT 'active',
            datasheet_url TEXT NOT NULL DEFAULT '',
            specs JSONB NOT NULL DEFAULT '{}'::jsonb,
            extraction_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            schema_version INTEGER NOT NULL DEFAULT 1,
            indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (mpn, manufacturer)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_component_catalog_category ON component_catalog (category)",
        "CREATE INDEX IF NOT EXISTS idx_component_catalog_lifecycle "
        "ON component_catalog (lifecycle)",
        "CREATE INDEX IF NOT EXISTS idx_component_catalog_cost ON component_catalog (cost_usd)",
        "CREATE INDEX IF NOT EXISTS idx_component_catalog_manufacturer "
        "ON component_catalog (manufacturer)",
    ]
    for category, spec in registry.items():
        if not _SAFE_IDENTIFIER.match(category):  # pragma: no cover — _register() enforces this
            raise ValueError(f"unsafe category name for DDL: {category!r}")
        for f in spec.queryable_fields():
            if not f.is_indexed:
                continue
            if not _SAFE_IDENTIFIER.match(f.name):  # pragma: no cover — _register() enforces this
                raise ValueError(f"unsafe field name for DDL: {f.name!r}")
            cast = _CAST_SQL.get(f.type, "")
            idx_name = _index_name(category, f.name)
            # Postgres quirk: CREATE INDEX's expression-list parser rejects
            # a cast expression wrapped in only one paren pair — e.g.
            # `((specs->>'x')::double precision)` is a syntax error
            # ("syntax error at or near \"::\"", confirmed against a real
            # server), but the *same* expression wrapped in one more pair
            # — `(((specs->>'x')::double precision))` — parses fine. Most
            # visible with multi-word type names like "double precision".
            # Always double-wrap (harmless when cast is "" too — Postgres
            # accepts arbitrarily nested redundant parens around a single
            # expression) rather than special-casing per type.
            index_expr = f"(specs->>'{f.name}'){cast}"
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON component_catalog "
                f"(({index_expr})) WHERE category = {_sql_literal(category)}"
            )
    return statements


class ComponentCatalogStore:
    """Async Postgres-backed store for the parametric component catalog.

    Use ``await store.initialize()`` once at boot to create the pool and
    the ``component_catalog`` table + indexes. Pair with ``close()`` on
    shutdown.
    """

    def __init__(self, dsn: str, *, pool_size: int = 10) -> None:
        self._dsn = dsn
        self._pool_size = pool_size
        self._pool: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        with tracer.start_as_current_span("component_catalog.initialize") as span:
            try:
                import asyncpg

                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=1, max_size=self._pool_size
                )
                async with self._pool.acquire() as conn:
                    for statement in schema_statements():
                        await conn.execute(statement)
                logger.info("component_catalog_store_initialized")
            except Exception as exc:
                span.record_exception(exc)
                logger.error("component_catalog_store_init_failed", error=str(exc))
                raise

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("component_catalog_store_closed")

    # ------------------------------------------------------------------
    # CRUD / query
    # ------------------------------------------------------------------

    async def upsert(self, row: ComponentCatalogRow) -> ComponentCatalogRow:
        with tracer.start_as_current_span("component_catalog.upsert") as span:
            span.set_attribute("catalog.mpn", row.mpn)
            span.set_attribute("catalog.category", row.category)
            try:
                async with self._pool.acquire() as conn:
                    record = await conn.fetchrow(
                        """
                        INSERT INTO component_catalog
                            (id, mpn, manufacturer, category, purchase_unit, cost_usd,
                             lifecycle, datasheet_url, specs, extraction_meta,
                             schema_version, indexed_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11,
                                now(), now())
                        ON CONFLICT (mpn, manufacturer) DO UPDATE SET
                            category = EXCLUDED.category,
                            purchase_unit = EXCLUDED.purchase_unit,
                            cost_usd = EXCLUDED.cost_usd,
                            lifecycle = EXCLUDED.lifecycle,
                            datasheet_url = EXCLUDED.datasheet_url,
                            specs = EXCLUDED.specs,
                            extraction_meta = EXCLUDED.extraction_meta,
                            schema_version = EXCLUDED.schema_version,
                            updated_at = now()
                        RETURNING id, mpn, manufacturer, category, purchase_unit, cost_usd,
                                  lifecycle, datasheet_url, specs, extraction_meta,
                                  schema_version, indexed_at
                        """,
                        row.id,
                        row.mpn,
                        row.manufacturer,
                        row.category,
                        row.purchase_unit,
                        row.cost_usd,
                        row.lifecycle,
                        row.datasheet_url,
                        json.dumps(row.specs),
                        json.dumps(row.extraction_meta),
                        row.schema_version,
                    )
                logger.info("component_catalog_upserted", mpn=row.mpn, category=row.category)
                assert record is not None  # INSERT ... RETURNING always yields one row
                return _row_from_record(record)
            except Exception as exc:
                span.record_exception(exc)
                logger.error("component_catalog_upsert_failed", error=str(exc), mpn=row.mpn)
                raise

    async def get(self, mpn: str, manufacturer: str) -> ComponentCatalogRow | None:
        with tracer.start_as_current_span("component_catalog.get") as span:
            span.set_attribute("catalog.mpn", mpn)
            try:
                async with self._pool.acquire() as conn:
                    record = await conn.fetchrow(
                        """
                        SELECT id, mpn, manufacturer, category, purchase_unit, cost_usd,
                               lifecycle, datasheet_url, specs, extraction_meta,
                               schema_version, indexed_at
                        FROM component_catalog WHERE mpn = $1 AND manufacturer = $2
                        """,
                        mpn,
                        manufacturer,
                    )
                return _row_from_record(record) if record is not None else None
            except Exception as exc:
                span.record_exception(exc)
                logger.error("component_catalog_get_failed", error=str(exc), mpn=mpn)
                raise

    async def query(self, catalog_query: CatalogQuery) -> CatalogQueryResult:
        with tracer.start_as_current_span("component_catalog.query") as span:
            span.set_attribute("catalog.category", catalog_query.category or "*")
            span.set_attribute("catalog.filter_count", len(catalog_query.filters))
            t0 = time.monotonic()
            try:
                sql, params = build_sql(catalog_query)
                async with self._pool.acquire() as conn:
                    records = await conn.fetch(sql, *params)
                rows = [_row_from_record(r) for r in records]
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                span.set_attribute("catalog.result_count", len(rows))
                logger.info(
                    "component_catalog_query_completed",
                    category=catalog_query.category,
                    result_count=len(rows),
                    duration_ms=round(elapsed_ms, 2),
                )
                return CatalogQueryResult(rows=rows, query_time_ms=round(elapsed_ms, 2))
            except Exception as exc:
                span.record_exception(exc)
                logger.error("component_catalog_query_failed", error=str(exc))
                raise

    async def health_check(self) -> dict[str, Any]:
        try:
            async with self._pool.acquire() as conn:
                count = await conn.fetchval("SELECT count(*) FROM component_catalog")
            return {"status": "ok", "row_count": int(count)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


def _row_from_record(record: Any) -> ComponentCatalogRow:
    specs = record["specs"]
    if isinstance(specs, str):
        specs = json.loads(specs)
    extraction_meta = record["extraction_meta"]
    if isinstance(extraction_meta, str):
        extraction_meta = json.loads(extraction_meta)
    indexed_at = record["indexed_at"]
    if indexed_at is not None and indexed_at.tzinfo is None:
        indexed_at = indexed_at.replace(tzinfo=UTC)
    return ComponentCatalogRow(
        id=record["id"],
        mpn=record["mpn"],
        manufacturer=record["manufacturer"],
        category=record["category"],
        purchase_unit=record["purchase_unit"],
        cost_usd=record["cost_usd"],
        lifecycle=record["lifecycle"],
        datasheet_url=record["datasheet_url"],
        specs=specs or {},
        extraction_meta=extraction_meta or {},
        schema_version=record["schema_version"],
        indexed_at=indexed_at if indexed_at is not None else datetime.now(UTC),
    )


__all__ = ["ComponentCatalogStore", "schema_statements"]
