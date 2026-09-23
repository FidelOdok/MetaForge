"""twin.approve_engineering_entity -- a real authority approval step,
distinct from creation (FORGE-73, waiver/release model, epic FORGE-35)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.twin.engineering_entity_approval import make_engineering_entity_approver
from twin_core.api import InMemoryTwinAPI, RevisionConflictError
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState

PROJECT_ID = uuid4()


def _waiver() -> EngineeringEntity:
    return EngineeringEntity(entity_type="waiver", statement="waived", project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_approves_a_proposed_waiver() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    out = await approve(entity_id=str(created.id))

    assert out["authority"] == "approved"
    assert out["revision"] == created.revision + 1
    stored = await twin.get_engineering_entity(created.id)
    assert stored.authority == AuthorityState.APPROVED


@pytest.mark.asyncio
async def test_default_target_state_is_approved() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    out = await approve(entity_id=str(created.id))

    assert out["authority"] == "approved"


@pytest.mark.asyncio
async def test_can_advance_to_reviewed_explicitly() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    out = await approve(entity_id=str(created.id), target_state="reviewed")

    assert out["authority"] == "reviewed"


@pytest.mark.asyncio
async def test_records_approved_by_in_metadata() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    await approve(entity_id=str(created.id), approved_by="safety-lead")

    stored = await twin.get_engineering_entity(created.id)
    assert stored.metadata["approved_by"] == "safety-lead"


@pytest.mark.asyncio
async def test_preserves_existing_metadata_when_recording_approved_by() -> None:
    twin = InMemoryTwinAPI.create()
    entity = EngineeringEntity(
        entity_type="waiver",
        statement="waived",
        project_id=PROJECT_ID,
        metadata={"reason": "supply-chain shortage"},
    )
    created = await twin.create_engineering_entity(entity)
    approve = make_engineering_entity_approver(twin)

    await approve(entity_id=str(created.id), approved_by="safety-lead")

    stored = await twin.get_engineering_entity(created.id)
    assert stored.metadata["reason"] == "supply-chain shortage"
    assert stored.metadata["approved_by"] == "safety-lead"


@pytest.mark.asyncio
async def test_invalid_target_state_rejected() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    with pytest.raises(ValueError, match="target_state"):
        await approve(entity_id=str(created.id), target_state="not_a_real_state")


@pytest.mark.asyncio
async def test_cannot_approve_straight_to_baselined() -> None:
    """Only a real Baseline may set BASELINED (twin_core.transactions.
    baseline.create_baseline) -- this tool must not offer a bypass."""
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    with pytest.raises(ValueError, match="target_state"):
        await approve(entity_id=str(created.id), target_state="baselined")


@pytest.mark.asyncio
async def test_unknown_entity_rejected() -> None:
    twin = InMemoryTwinAPI.create()
    approve = make_engineering_entity_approver(twin)

    with pytest.raises(ValueError, match="not found"):
        await approve(entity_id=str(uuid4()))


@pytest.mark.asyncio
async def test_stale_expected_revision_raises_conflict() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    with pytest.raises(RevisionConflictError):
        await approve(entity_id=str(created.id), expected_revision=created.revision + 1)


@pytest.mark.asyncio
async def test_matching_expected_revision_succeeds() -> None:
    twin = InMemoryTwinAPI.create()
    created = await twin.create_engineering_entity(_waiver())
    approve = make_engineering_entity_approver(twin)

    out = await approve(entity_id=str(created.id), expected_revision=created.revision)

    assert out["authority"] == "approved"
