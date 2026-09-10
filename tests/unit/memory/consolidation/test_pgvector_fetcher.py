"""Unit tests for ``PgVectorEventFetcher`` (MET-567).

The defect this class closes: consolidation was always wired with
``InMemoryEventFetcher``, whose snapshot reads ``_experiences`` — an attribute
only ``InMemoryExperienceStore`` has. Pointed at a pgvector store it returned
an empty batch forever, so every pass synthesised nothing and
``memory.list_insights`` stayed empty in production. The last test here pins
that behaviour so the two fetchers can never be confused again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.consolidation.fetcher import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_MIN_IMPORTANCE,
    InMemoryEventFetcher,
    PgVectorEventFetcher,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory


def _exp(*, importance: float = 0.6) -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech",
        task_type="stress",
        success=True,
        result_summary="bracket held at 1.4x",
        timestamp=datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC),
        importance=importance,
        confidence=ConfidenceTier.VERBATIM,
    )


class FakeWindowStore:
    """Records the kwargs ``list_window`` was called with."""

    def __init__(self, rows: list[ExperienceMemory] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[dict[str, Any]] = []

    async def list_window(self, **kwargs: Any) -> list[ExperienceMemory]:
        self.calls.append(kwargs)
        return list(self.rows)


class PgVectorLikeStore:
    """Shaped like ``PgVectorExperienceStore``: no ``_experiences`` dict."""

    def __init__(self) -> None:
        self._pool = object()


@pytest.mark.asyncio
async def test_fetch_returns_the_stores_rows():
    rows = [_exp(), _exp()]
    store = FakeWindowStore(rows)

    out = await PgVectorEventFetcher(store).fetch()

    assert out == rows


@pytest.mark.asyncio
async def test_fetch_pushes_every_filter_down_into_the_store():
    # The whole point of the pgvector fetcher is that filtering, ordering and
    # the limit happen in SQL, not over a Python snapshot — so each argument
    # must reach the store rather than being applied after the fact.
    store = FakeWindowStore()
    since = datetime.now(UTC) - timedelta(hours=2)
    until = datetime.now(UTC)
    project_id = UUID("33333333-3333-3333-3333-333333333333")

    await PgVectorEventFetcher(store).fetch(
        since=since,
        until=until,
        project_id=project_id,
        min_importance=0.42,
        limit=17,
    )

    assert store.calls == [
        {
            "since": since,
            "until": until,
            "project_id": project_id,
            "min_importance": 0.42,
            "limit": 17,
        }
    ]


@pytest.mark.asyncio
async def test_fetch_applies_the_shared_defaults():
    store = FakeWindowStore()

    await PgVectorEventFetcher(store).fetch()

    call = store.calls[0]
    assert call["min_importance"] == DEFAULT_MIN_IMPORTANCE
    assert call["limit"] == DEFAULT_FETCH_LIMIT


@pytest.mark.asyncio
async def test_in_memory_fetcher_is_empty_against_a_pgvector_shaped_store():
    # Regression guard for the original defect: this is what production ran.
    out = await InMemoryEventFetcher(PgVectorLikeStore()).fetch()  # type: ignore[arg-type]

    assert out == []
