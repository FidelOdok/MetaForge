"""Unit tests for the consolidation EventArchiver (MET-463 stage 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.archiver import (
    EventArchiver,
    InMemoryExperienceArchive,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


def _experience(*, run_id: str = "run-1") -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id=run_id,
        step_id="s",
        agent_code="mechanical",
        task_type="validate",
        success=True,
        result_summary="did a thing",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.6,
        confidence=ConfidenceTier.VERBATIM,
        embedding=[0.1] * 8,
    )


async def _seed(store: InMemoryExperienceStore, count: int) -> list[ExperienceMemory]:
    out = []
    for i in range(count):
        exp = _experience(run_id=f"run-{i}")
        await store.store(exp)
        out.append(exp)
    return out


@pytest.mark.asyncio
async def test_archive_moves_to_cold_and_clears_hot():
    store = InMemoryExperienceStore()
    archive = InMemoryExperienceArchive()
    experiences = await _seed(store, 3)
    archiver = EventArchiver(store, archive)

    result = await archiver.archive_experiences(experiences)

    assert result.archived_count == 3
    assert result.deleted_count == 3
    # Cold storage has them; hot store no longer does.
    assert len(archive.archived) == 3
    for exp in experiences:
        assert exp.id in archive
        assert await store.get(exp.id) is None


@pytest.mark.asyncio
async def test_archive_empty_is_noop():
    store = InMemoryExperienceStore()
    archive = InMemoryExperienceArchive()
    archiver = EventArchiver(store, archive)

    result = await archiver.archive_experiences([])

    assert result.archived_count == 0
    assert result.deleted_count == 0
    assert archive.archived == []


@pytest.mark.asyncio
async def test_archive_failure_preserves_hot_store():
    class _BoomArchive(InMemoryExperienceArchive):
        async def archive(self, experiences):
            raise RuntimeError("cold storage down")

    store = InMemoryExperienceStore()
    experiences = await _seed(store, 2)
    archiver = EventArchiver(store, _BoomArchive())

    result = await archiver.archive_experiences(experiences)

    # Integrity: nothing archived → nothing deleted from hot memory.
    assert result.archived_count == 0
    assert result.deleted_count == 0
    for exp in experiences:
        assert await store.get(exp.id) is not None


@pytest.mark.asyncio
async def test_delete_failure_after_archive_is_tracked_not_fatal():
    class _BoomDeleteStore(InMemoryExperienceStore):
        async def delete(self, experience_id):
            raise RuntimeError("delete failed")

    store = _BoomDeleteStore()
    archive = InMemoryExperienceArchive()
    experiences = await _seed(store, 2)
    archiver = EventArchiver(store, archive)

    result = await archiver.archive_experiences(experiences)

    # Archived safely even though hot-store cleanup failed.
    assert result.archived_count == 2
    assert result.deleted_count == 0
    assert len(result.failed_delete_ids) == 2
    assert len(archive.archived) == 2
