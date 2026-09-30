"""Unit tests for make_release_package_creator / make_release_package_lister
/ twin.create_release_package (FORGE-299)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from api_gateway.twin.release_package import (
    make_release_package_creator,
    make_release_package_lister,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.baseline import Baseline
from twin_core.models.bom_item import BOMItem
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState, WorkProductType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _full_coverage_accessor(_pid):
    return SimpleNamespace(verification_to_evidence=100.0)


async def _make_gate_ready(twin: InMemoryTwinAPI, project_id) -> None:
    """Seeds exactly what evaluate_g8_release needs to return PASSED: a
    baseline, one current-staleness evidence entity, and one approved
    release_approval entity. Waivers stay at zero (a real vacuous PASS).
    Verification-complete is satisfied separately, by injecting
    ``_full_coverage_accessor`` into the creator under test -- not seeded
    here."""
    await twin.create_baseline(
        Baseline(name="v1", includes=[], project_id=project_id, reason="release candidate")
    )
    await twin.create_engineering_entity(
        EngineeringEntity(
            entity_type="evidence",
            statement="sim result",
            project_id=project_id,
            metadata={"staleness": "current"},
        )
    )
    await twin.create_engineering_entity(
        EngineeringEntity(
            entity_type="release_approval",
            statement="approved for release",
            project_id=project_id,
            authority=AuthorityState.APPROVED,
        )
    )


def _creator(twin: InMemoryTwinAPI, *, with_coverage: bool = True):
    recorder = make_engineering_entity_recorder(twin)
    return make_release_package_creator(
        twin,
        engineering_entity_recorder=recorder,
        traceability_coverage=_full_coverage_accessor if with_coverage else None,
    )


class TestGateNotPassedBlocksCreation:
    async def test_empty_project_raises_naming_failing_checks(self, twin: InMemoryTwinAPI):
        create = _creator(twin)
        project_id = uuid4()
        with pytest.raises(ValueError, match="G8 release gate"):
            await create(project_id=str(project_id))

    async def test_missing_verification_accessor_blocks_even_with_everything_else_ready(
        self, twin: InMemoryTwinAPI
    ):
        """READY_FOR_REVIEW (a NOT_EVALUATED check, no injected coverage
        accessor) is not PASSED -- create() must refuse it exactly like a
        real FAIL, not treat NOT_EVALUATED as good enough."""
        project_id = uuid4()
        await _make_gate_ready(twin, project_id)
        create = _creator(twin, with_coverage=False)
        with pytest.raises(ValueError, match="G8 release gate"):
            await create(project_id=str(project_id))

    async def test_unapproved_release_approval_blocks(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await twin.create_baseline(Baseline(name="v1", includes=[], project_id=project_id))
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="evidence",
                statement="x",
                project_id=project_id,
                metadata={"staleness": "current"},
            )
        )
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="release_approval",
                statement="pending",
                project_id=project_id,
                authority=AuthorityState.PROPOSED,
            )
        )
        create = _creator(twin)
        with pytest.raises(ValueError, match="release_approved|release gate"):
            await create(project_id=str(project_id))


class TestGatePassedCreatesRealSnapshot:
    async def test_first_package_has_no_prior_diff_and_real_counts(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_gate_ready(twin, project_id)
        await twin.create_hierarchy_node(
            HierarchyNode(kind="product", name="Arm", project_id=project_id)
        )
        await twin.add_bom_item(
            BOMItem(
                part_number="MG996R",
                manufacturer="TowerPro",
                project_id=project_id,
            )
        )
        await twin.create_work_product(
            WorkProduct(
                name="chose steel",
                type=WorkProductType.DESIGN_DECISION,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="json",
                created_by="test",
                project_id=project_id,
            )
        )

        create = _creator(twin)
        result = await create(project_id=str(project_id))

        assert result["gate_status"] == "passed"
        assert len(result["snapshot"]["hierarchy_node_ids"]) == 1
        assert len(result["snapshot"]["bom_item_ids"]) == 1
        # Two evidence entities: the one _make_gate_ready seeded for the
        # stale-evidence check, plus none added here -- exactly 1.
        assert len(result["snapshot"]["evidence_ids"]) == 1
        assert len(result["snapshot"]["decision_ids"]) == 1
        assert result["snapshot"]["drawing_ids"] == []
        assert result["diff_from_previous"] == {
            "compared_to": None,
            "hierarchy_delta": 1,
            "bom_delta": 1,
            "evidence_delta": 1,
            "decision_delta": 1,
        }
        assert result["node_id"]
        assert result["title"]
        assert result["created_at"]

    async def test_second_package_diffs_against_the_first(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_gate_ready(twin, project_id)
        create = _creator(twin)

        first = await create(project_id=str(project_id))
        assert first["diff_from_previous"]["compared_to"] is None

        # Real state change between releases: one more BOM item, one more
        # piece of evidence.
        await twin.add_bom_item(
            BOMItem(part_number="DS3218MG", manufacturer="DSServo", project_id=project_id)
        )
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="evidence",
                statement="second sim",
                project_id=project_id,
                metadata={"staleness": "current"},
            )
        )

        second = await create(project_id=str(project_id))

        assert second["diff_from_previous"]["compared_to"] == first["node_id"]
        assert second["diff_from_previous"]["bom_delta"] == 1
        assert second["diff_from_previous"]["evidence_delta"] == 1
        assert second["diff_from_previous"]["hierarchy_delta"] == 0
        assert second["diff_from_previous"]["decision_delta"] == 0

    async def test_notes_becomes_the_title(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_gate_ready(twin, project_id)
        create = _creator(twin)
        result = await create(project_id=str(project_id), notes="v1.0 release candidate")
        assert result["title"] == "v1.0 release candidate"

    async def test_released_entity_is_project_linked_and_queryable(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_gate_ready(twin, project_id)
        create = _creator(twin)
        result = await create(project_id=str(project_id))
        assert result["project_linked"] is False  # no project_backend injected in this test
        packages = await twin.list_engineering_entities(
            project_id=project_id, entity_type="release_package"
        )
        assert len(packages) == 1
        assert str(packages[0].id) == result["node_id"]


class TestReleasePackageLister:
    async def test_empty_project_returns_empty_list(self, twin: InMemoryTwinAPI):
        lister = make_release_package_lister(twin)
        assert await lister(project_id=str(uuid4())) == []

    async def test_lists_oldest_first_with_real_diffs(self, twin: InMemoryTwinAPI):
        project_id = uuid4()
        await _make_gate_ready(twin, project_id)
        create = _creator(twin)
        lister = make_release_package_lister(twin)

        first = await create(project_id=str(project_id), notes="first")
        await twin.add_bom_item(BOMItem(part_number="X", manufacturer="Y", project_id=project_id))
        second = await create(project_id=str(project_id), notes="second")

        packages = await lister(project_id=str(project_id))

        assert [p["node_id"] for p in packages] == [first["node_id"], second["node_id"]]
        assert packages[0]["title"] == "first"
        assert packages[0]["diff_from_previous"]["compared_to"] is None
        assert packages[1]["title"] == "second"
        assert packages[1]["diff_from_previous"]["compared_to"] == first["node_id"]
        assert packages[1]["diff_from_previous"]["bom_delta"] == 1
        assert packages[1]["snapshot"]["drawing_ids"] == []

    async def test_other_project_packages_are_excluded(self, twin: InMemoryTwinAPI):
        project_a = uuid4()
        project_b = uuid4()
        await _make_gate_ready(twin, project_a)
        await _make_gate_ready(twin, project_b)
        create = _creator(twin)
        lister = make_release_package_lister(twin)

        await create(project_id=str(project_a))
        await create(project_id=str(project_b))

        packages_a = await lister(project_id=str(project_a))
        assert len(packages_a) == 1
