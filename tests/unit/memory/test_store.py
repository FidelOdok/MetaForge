"""Unit tests for ``digital_twin.memory.store``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


_DEFAULT_EMBEDDING: list[float] = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _experience(
    *,
    agent_code: str = "mechanical",
    success: bool = True,
    run_id: str = "run-1",
    embedding: list[float] | None = None,
    project_id: UUID | None = None,
) -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id=run_id,
        step_id="step-1",
        agent_code=agent_code,
        task_type="validate",
        success=success,
        duration_seconds=1.0,
        result_summary=f"agent={agent_code} success={success}",
        error=None if success else "boom",
        project_id=project_id,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.7,
        confidence=ConfidenceTier.VERBATIM,
        embedding=list(_DEFAULT_EMBEDDING) if embedding is None else embedding,
    )


@pytest.mark.asyncio
async def test_store_and_get_roundtrip():
    store = InMemoryExperienceStore()
    exp = _experience()
    stored = await store.store(exp)
    fetched = await store.get(stored.id)
    assert fetched is not None
    assert fetched.id == exp.id
    assert fetched.agent_code == "mechanical"


@pytest.mark.asyncio
async def test_search_ranks_by_cosine_similarity():
    store = InMemoryExperienceStore()
    near = _experience(agent_code="near", embedding=[1.0] + [0.0] * 7)
    middle = _experience(agent_code="middle", embedding=[0.5, 0.5] + [0.0] * 6)
    far = _experience(agent_code="far", embedding=[0.0] * 7 + [1.0])
    for exp in (far, near, middle):
        await store.store(exp)

    hits = await store.search([1.0] + [0.0] * 7, limit=3)
    assert [h.experience.agent_code for h in hits] == ["near", "middle", "far"]
    assert hits[0].similarity > hits[1].similarity > hits[2].similarity
    assert [h.rank for h in hits] == [0, 1, 2]


@pytest.mark.asyncio
async def test_search_filters_by_project(fixed_uuid):
    store = InMemoryExperienceStore()
    other_project = UUID("00000000-0000-0000-0000-000000000002")
    in_project = _experience(agent_code="in", project_id=fixed_uuid)
    out_project = _experience(agent_code="out", project_id=other_project)
    await store.store(in_project)
    await store.store(out_project)

    hits = await store.search([1.0] + [0.0] * 7, project_id=fixed_uuid)
    assert len(hits) == 1
    assert hits[0].experience.agent_code == "in"


@pytest.mark.asyncio
async def test_search_filters_by_agent_and_success():
    store = InMemoryExperienceStore()
    await store.store(_experience(agent_code="mech", success=True))
    await store.store(_experience(agent_code="mech", success=False))
    await store.store(_experience(agent_code="elec", success=True))

    only_mech = await store.search([1.0] + [0.0] * 7, agent_code="mech")
    assert {h.experience.agent_code for h in only_mech} == {"mech"}

    only_success = await store.search([1.0] + [0.0] * 7, only_success=True)
    assert all(h.experience.success for h in only_success)


@pytest.mark.asyncio
async def test_delete_by_run_removes_only_matching_records():
    store = InMemoryExperienceStore()
    await store.store(_experience(run_id="keep", agent_code="a"))
    await store.store(_experience(run_id="drop", agent_code="b"))
    await store.store(_experience(run_id="drop", agent_code="c"))

    removed = await store.delete_by_run("drop")
    assert removed == 2
    remaining = await store.search([1.0] + [0.0] * 7)
    assert [h.experience.run_id for h in remaining] == ["keep"]


@pytest.mark.asyncio
async def test_search_ignores_records_with_no_embedding():
    store = InMemoryExperienceStore()
    await store.store(_experience(agent_code="empty", embedding=[]))
    await store.store(_experience(agent_code="real"))

    hits = await store.search([1.0] + [0.0] * 7)
    assert {h.experience.agent_code for h in hits} == {"real"}
