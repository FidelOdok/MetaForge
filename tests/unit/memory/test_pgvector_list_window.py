"""SQL-shape tests for ``PgVectorExperienceStore.list_window`` (MET-567).

No live database: a fake pool captures the query and parameters, which is
enough to pin the parts that matter — that every filter becomes a bound
parameter (never string interpolation), that the ordering is newest-first so a
``limit`` cut keeps recent events, and that the limit is always the last
parameter. Live coverage stays in
``tests/integration/test_pgvector_experience_store.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from digital_twin.memory.pgvector_store import PgVectorExperienceStore


class FakeConn:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.query: str = ""
        self.params: tuple[Any, ...] = ()

    async def fetch(self, query: str, *params: Any) -> list[Any]:
        self.query = query
        self.params = params
        return self.rows

    async def __aenter__(self) -> FakeConn:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> FakeConn:
        return self._conn


def _store(conn: FakeConn) -> PgVectorExperienceStore:
    store = PgVectorExperienceStore(dsn="postgresql://unused/none")
    store._pool = FakePool(conn)  # noqa: SLF001
    return store


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "run_id": "r",
        "step_id": "s",
        "agent_code": "chat-harness",
        "task_type": "chat_turn",
        "success": True,
        "duration_seconds": 2.0,
        "result_summary": "built a bracket",
        "error": None,
        "project_id": None,
        "timestamp": datetime(2026, 9, 1, 9, 0, 0, tzinfo=UTC),
        "importance": 0.7,
        "confidence": "verbatim",
        "embedding": "[0.1,0.2]",
        "metadata": {},
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_list_window_returns_experiences():
    conn = FakeConn([_row()])

    out = await _store(conn).list_window()

    assert len(out) == 1
    assert out[0].result_summary == "built a bracket"


@pytest.mark.asyncio
async def test_list_window_orders_newest_first_and_limits_last():
    conn = FakeConn()

    await _store(conn).list_window(limit=25)

    assert "ORDER BY timestamp DESC" in conn.query
    assert conn.params[-1] == 25


@pytest.mark.asyncio
async def test_list_window_binds_every_filter_as_a_parameter():
    conn = FakeConn()
    since = datetime.now(UTC) - timedelta(days=1)
    until = datetime.now(UTC)
    project_id = UUID("22222222-2222-2222-2222-222222222222")

    await _store(conn).list_window(
        since=since,
        until=until,
        project_id=project_id,
        agent_code="chat-harness",
        min_importance=0.3,
        limit=50,
    )

    assert "timestamp >= $1" in conn.query
    assert "timestamp <= $2" in conn.query
    assert "project_id = $3" in conn.query
    assert "agent_code = $4" in conn.query
    assert "importance >= $5" in conn.query
    assert conn.params == (since, until, project_id, "chat-harness", 0.3, 50)


@pytest.mark.asyncio
async def test_list_window_without_filters_emits_no_where_clause():
    conn = FakeConn()

    await _store(conn).list_window()

    assert "WHERE" not in conn.query
    assert conn.params == (500,)


@pytest.mark.asyncio
async def test_list_window_treats_a_zero_importance_floor_as_no_filter():
    # 0.0 is "everything", so adding `importance >= 0` would only cost a scan
    # predicate; the fetcher's own default floor is what does the real gating.
    conn = FakeConn()

    await _store(conn).list_window(min_importance=0.0)

    assert "importance" not in conn.query.split("FROM")[1]
