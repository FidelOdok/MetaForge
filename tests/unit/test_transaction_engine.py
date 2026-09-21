"""Unit tests for TransactionEngine.commit() (FORGE-50, epic FORGE-35)."""

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity
from twin_core.models.patch import Patch, PatchOp, PatchOperation
from twin_core.transactions.engine import TransactionEngine


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def engine(twin):
    return TransactionEngine(twin)


def _constraint_payload(**overrides):
    payload = {
        "name": "payload_mass",
        "expression": "mass <= 3.0",
        "severity": "error",
        "domain": "mechanical",
        "source": "test",
    }
    payload.update(overrides)
    return payload


async def _seed_constraint(twin, **overrides) -> Constraint:
    c = Constraint(
        name=overrides.pop("name", "seed"),
        expression="x < 1",
        severity=ConstraintSeverity.ERROR,
        domain="mechanical",
        source="test",
        **overrides,
    )
    return await twin.create_constraint(c)


async def _seed_entity(twin, **overrides) -> EngineeringEntity:
    e = EngineeringEntity(entity_type="intent", statement="seed statement", **overrides)
    return await twin.create_engineering_entity(e)


class TestAdd:
    async def test_add_constraint_commits_at_revision_one(self, engine):
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.ADD, entity_kind="constraint", entity=_constraint_payload()
                )
            ],
            reason="new requirement",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        assert result.applied[0].new_revision == 1

    async def test_add_invalid_schema_is_rejected_with_zero_writes(self, engine, twin):
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="constraint",
                    entity={"name": "missing_required_fields"},
                )
            ],
            reason="bad payload",
        )
        result = await engine.commit(patch)
        assert result.status == "conflict"
        assert await twin.list_constraints() == []


class TestRevise:
    async def test_revise_increments_revision(self, engine, twin):
        c = await _seed_constraint(twin)
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=c.id,
                    fields={"message": "tightened"},
                    expected_revision=1,
                )
            ],
            reason="tighten tolerance",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        assert result.applied[0].new_revision == 2
        fetched = await twin.get_constraint(c.id)
        assert fetched.message == "tightened"
        assert fetched.revision == 2

    async def test_stale_expected_revision_is_a_conflict_with_zero_writes(self, engine, twin):
        c = await _seed_constraint(twin)
        await twin.update_constraint(c.id, {"message": "first edit"}, expected_revision=1)

        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=c.id,
                    fields={"message": "conflicting edit"},
                    expected_revision=1,  # stale -- it's now revision 2
                )
            ],
            reason="conflicting edit",
        )
        result = await engine.commit(patch)
        assert result.status == "conflict"
        assert "Revision conflict" in result.conflicts[0]
        fetched = await twin.get_constraint(c.id)
        assert fetched.message == "first edit"  # unchanged

    async def test_revise_missing_entity_is_a_conflict(self, engine):
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=uuid4(),
                    fields={"message": "x"},
                )
            ],
            reason="edit ghost",
        )
        result = await engine.commit(patch)
        assert result.status == "conflict"

    async def test_revise_engineering_entity_increments_revision(self, engine, twin):
        e = await _seed_entity(twin)
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="engineering_entity",
                    entity_id=e.id,
                    fields={"statement": "revised statement"},
                    expected_revision=1,
                )
            ],
            reason="clarify intent",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        fetched = await twin.get_engineering_entity(e.id)
        assert fetched.statement == "revised statement"
        assert fetched.revision == 2


class TestLinkUnlink:
    async def test_link_creates_edge(self, engine, twin):
        a = await _seed_constraint(twin, name="a")
        b = await _seed_constraint(twin, name="b")
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.LINK, entity_id=a.id, relation="depends_on", target_id=b.id
                )
            ],
            reason="link",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        edges = await twin.get_edges(a.id, direction="outgoing")
        assert any(edge.target_id == b.id for edge in edges)

    async def test_unknown_relation_is_a_conflict(self, engine):
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.LINK,
                    entity_id=uuid4(),
                    relation="not_a_real_edge_type",
                    target_id=uuid4(),
                )
            ],
            reason="bad relation",
        )
        result = await engine.commit(patch)
        assert result.status == "conflict"


class TestSupersede:
    async def test_supersede_links_and_marks_old_superseded(self, engine, twin):
        old = await _seed_constraint(twin, name="old")
        new = await _seed_constraint(twin, name="new")
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.SUPERSEDE,
                    entity_kind="constraint",
                    entity_id=old.id,
                    target_id=new.id,
                    expected_revision=1,
                )
            ],
            reason="new revision replaces old",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        fetched_old = await twin.get_constraint(old.id)
        assert fetched_old.status == "superseded"
        edges = await twin.get_edges(new.id, direction="outgoing")
        assert any(e.target_id == old.id for e in edges)


class TestDeprecateInvalidate:
    async def test_deprecate_sets_status(self, engine, twin):
        c = await _seed_constraint(twin)
        patch = Patch(
            operations=[
                PatchOperation(op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=c.id)
            ],
            reason="no longer applicable",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        fetched = await twin.get_constraint(c.id)
        assert fetched.status == "deprecated"

    async def test_invalidate_sets_status(self, engine, twin):
        e = await _seed_entity(twin)
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.INVALIDATE, entity_kind="engineering_entity", entity_id=e.id
                )
            ],
            reason="assumption disproven",
        )
        result = await engine.commit(patch)
        assert result.status == "committed"
        fetched = await twin.get_engineering_entity(e.id)
        assert fetched.status == "invalidated"


class TestMultiOperationAtomicity:
    async def test_one_conflicting_operation_blocks_the_whole_patch(self, engine, twin):
        c = await _seed_constraint(twin)
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.ADD, entity_kind="constraint", entity=_constraint_payload()
                ),
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=c.id,
                    fields={"message": "x"},
                    expected_revision=99,  # wrong on purpose
                ),
            ],
            reason="batch with one bad op",
        )
        result = await engine.commit(patch)
        assert result.status == "conflict"
        # the valid ADD must NOT have been applied either -- all or nothing
        assert await twin.list_constraints() == [c]
