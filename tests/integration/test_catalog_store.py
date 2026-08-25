"""Integration test for the parametric component catalog store (MET-436).

Mirrors the L1-A1/L1-F3 skip-clean Postgres pattern used by
``tests/integration/test_knowledge_citation_roundtrip.py``:
``pytest.importorskip("asyncpg")`` plus a connectivity probe so the suite
SKIPs cleanly when Postgres isn't reachable instead of erroring.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest

pytest.importorskip("asyncpg")

from digital_twin.catalog.query import (  # noqa: E402
    CatalogQuery,
    ComponentCatalogRow,
    ComponentFilter,
)
from digital_twin.catalog.store import ComponentCatalogStore  # noqa: E402

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql://metaforge:metaforge@localhost:5432/metaforge"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


async def _pg_reachable(dsn: str) -> bool:
    import asyncio

    import asyncpg  # type: ignore[import-untyped]

    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
    except (OSError, TimeoutError, asyncpg.PostgresError):
        return False
    except Exception:
        return False
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()
    return True


@pytest.fixture
async def store() -> AsyncIterator[ComponentCatalogStore]:
    dsn = _dsn()
    if not await _pg_reachable(dsn):
        pytest.skip(f"Postgres not reachable at {dsn} — integration backend unavailable")

    s = ComponentCatalogStore(dsn)
    await s.initialize()
    try:
        yield s
    finally:
        # Clean up rows this test suite wrote (namespaced by manufacturer
        # so runs don't collide/leak into other tests' data).
        try:
            async with s._pool.acquire() as conn:  # noqa: SLF001 — test-only teardown
                await conn.execute(
                    "DELETE FROM component_catalog WHERE manufacturer = $1", "TEST-MET-436"
                )
        finally:
            await s.close()


def _row(mpn: str, category: str = "buck_converter", **overrides: object) -> ComponentCatalogRow:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        mpn=mpn,
        manufacturer="TEST-MET-436",
        category=category,
        purchase_unit="discrete_part",
        cost_usd=0.42,
        lifecycle="active",
        datasheet_url="",
        specs={"v_out": 5.0, "efficiency": 0.92, "package": "QFN-24"},
        extraction_meta={},
        schema_version=1,
        indexed_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return ComponentCatalogRow(**defaults)  # type: ignore[arg-type]


class TestComponentCatalogStore:
    async def test_initialize_creates_table(self, store: ComponentCatalogStore) -> None:
        health = await store.health_check()
        assert health["status"] == "ok"

    async def test_upsert_then_get_round_trips(self, store: ComponentCatalogStore) -> None:
        row = _row("MET436-TEST-1")
        await store.upsert(row)

        fetched = await store.get("MET436-TEST-1", "TEST-MET-436")
        assert fetched is not None
        assert fetched.mpn == "MET436-TEST-1"
        assert fetched.specs["v_out"] == 5.0
        assert fetched.specs["package"] == "QFN-24"

    async def test_upsert_is_idempotent_on_mpn_manufacturer(
        self, store: ComponentCatalogStore
    ) -> None:
        first = _row("MET436-TEST-2", cost_usd=0.50)
        await store.upsert(first)
        second = _row("MET436-TEST-2", cost_usd=0.35)
        await store.upsert(second)

        fetched = await store.get("MET436-TEST-2", "TEST-MET-436")
        assert fetched is not None
        assert fetched.cost_usd == 0.35  # ON CONFLICT DO UPDATE applied

        result = await store.query(
            CatalogQuery(
                category="buck_converter",
                filters=[ComponentFilter("mpn", "==", "MET436-TEST-2")],
            )
        )
        assert len(result.rows) == 1  # no duplicate row

    async def test_get_missing_mpn_returns_none(self, store: ComponentCatalogStore) -> None:
        assert await store.get("DOES-NOT-EXIST", "TEST-MET-436") is None

    async def test_range_query_matches_and_excludes(self, store: ComponentCatalogStore) -> None:
        await store.upsert(
            _row("MET436-TEST-3", specs={"v_out": 5.0, "efficiency": 0.92, "package": "QFN-24"})
        )
        await store.upsert(
            _row("MET436-TEST-4", specs={"v_out": 3.3, "efficiency": 0.80, "package": "SO-8"})
        )

        result = await store.query(
            CatalogQuery(
                category="buck_converter",
                filters=[
                    ComponentFilter("manufacturer", "==", "TEST-MET-436"),
                    ComponentFilter("efficiency", ">", 0.9),
                ],
            )
        )
        mpns = result.mpns()
        assert "MET436-TEST-3" in mpns
        assert "MET436-TEST-4" not in mpns

    async def test_query_with_no_matches_returns_empty(self, store: ComponentCatalogStore) -> None:
        result = await store.query(
            CatalogQuery(
                category="buck_converter",
                filters=[
                    ComponentFilter("manufacturer", "==", "TEST-MET-436"),
                    ComponentFilter("efficiency", ">", 0.999999),
                ],
            )
        )
        assert result.rows == []

    async def test_upsert_returns_a_uuid_id(self, store: ComponentCatalogStore) -> None:
        row = _row("MET436-TEST-5")
        returned = await store.upsert(row)
        assert isinstance(returned.id, UUID)
