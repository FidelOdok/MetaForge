"""Unit tests for _topological_steps / make_bringup_checklist_creator /
make_bringup_checklist_lister / twin.create_bringup_checklist (FORGE-295)."""

from __future__ import annotations

import pytest

from api_gateway.twin.bringup_checklist import (
    AssemblyGraphError,
    _topological_steps,
    make_bringup_checklist_creator,
    make_bringup_checklist_lister,
)
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


def _joint(name: str, base: str, follower: str, joint_type: str = "revolute") -> dict:
    return {"name": name, "type": joint_type, "base": base, "follower": follower}


async def _seed_wp(twin: InMemoryTwinAPI, *, joints: list[dict] | None = None) -> WorkProduct:
    metadata = {"assembly": {"parts": [], "joints": joints}} if joints is not None else {}
    return await twin.create_work_product(
        WorkProduct(
            name="arm_assembly",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            metadata=metadata,
        )
    )


def _creator(twin: InMemoryTwinAPI):
    recorder = make_engineering_entity_recorder(twin)
    return make_bringup_checklist_creator(twin, engineering_entity_recorder=recorder)


class TestTopologicalSteps:
    def test_empty_joints_returns_empty(self) -> None:
        assert _topological_steps([]) == []

    def test_single_joint_hand_verified(self) -> None:
        steps = _topological_steps([_joint("j1", "link0", "link1")])
        assert len(steps) == 1
        assert steps[0]["step_number"] == 1
        assert steps[0]["base"] == "link0"
        assert steps[0]["follower"] == "link1"

    def test_linear_chain_orders_by_dependency(self) -> None:
        # link0 -> link1 -> link2: j2 depends on j1's follower becoming a
        # base, so j1 must come first regardless of input order.
        steps = _topological_steps([_joint("j2", "link1", "link2"), _joint("j1", "link0", "link1")])
        assert [s["name"] for s in steps] == ["j1", "j2"]
        assert [s["step_number"] for s in steps] == [1, 2]

    def test_independent_subassemblies_both_included_no_error(self) -> None:
        # Two disjoint two-part chains sharing no parts -- disconnected but
        # acyclic, a valid multi-root graph, not an error.
        steps = _topological_steps([_joint("j1", "A", "B"), _joint("j2", "X", "Y")])
        assert {s["name"] for s in steps} == {"j1", "j2"}
        # Deterministic tie-break: both buildable in wave one, sorted by name.
        assert [s["name"] for s in steps] == ["j1", "j2"]

    def test_deterministic_tie_break_among_same_wave_joints(self) -> None:
        steps = _topological_steps([_joint("zeta", "root", "A"), _joint("alpha", "root", "B")])
        assert [s["name"] for s in steps] == ["alpha", "zeta"]

    def test_two_cycle_raises_no_root_part(self) -> None:
        # Every part is some joint's follower -- no valid starting point.
        with pytest.raises(AssemblyGraphError, match="no root part"):
            _topological_steps([_joint("j1", "A", "B"), _joint("j2", "B", "A")])

    def test_unreachable_subgraph_raises_cyclic_naming_joints(self) -> None:
        # j1 resolves fine from the real root; j2/j3 form a 2-cycle between
        # X and Y that never becomes reachable.
        with pytest.raises(AssemblyGraphError, match="cyclic"):
            _topological_steps(
                [
                    _joint("j1", "root", "A"),
                    _joint("j2", "X", "Y"),
                    _joint("j3", "Y", "X"),
                ]
            )


class TestMakeBringupChecklistCreator:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        create = _creator(twin)
        with pytest.raises(ValueError, match="no work_product"):
            await create(work_product_id="11111111-1111-1111-1111-111111111111")

    async def test_work_product_with_no_assembly_metadata_raises(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(twin)
        create = _creator(twin)
        with pytest.raises(ValueError, match="no assembly.joints metadata"):
            await create(work_product_id=str(wp.id))

    async def test_real_joints_produce_correct_ordered_instructions(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(
            twin,
            joints=[
                _joint("joint_2", "link_1", "link_2"),
                _joint("joint_1", "link_0", "link_1"),
            ],
        )
        create = _creator(twin)
        result = await create(work_product_id=str(wp.id))
        assert len(result["steps"]) == 2
        assert result["steps"][0]["instruction"] == (
            "Step 1: attach link_1 to link_0 via joint_1 (revolute joint)"
        )
        assert result["steps"][1]["instruction"] == (
            "Step 2: attach link_2 to link_1 via joint_2 (revolute joint)"
        )

    async def test_recorded_entity_is_real_bringup_checklist_with_metadata(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(twin, joints=[_joint("joint_1", "link_0", "link_1")])
        create = _creator(twin)
        result = await create(work_product_id=str(wp.id))
        entities = await twin.list_engineering_entities(entity_type="bringup_checklist")
        assert len(entities) == 1
        entity = entities[0]
        assert str(entity.id) == result["node_id"]
        assert entity.metadata["work_product_id"] == str(wp.id)
        assert len(entity.metadata["steps"]) == 1

    async def test_cyclic_graph_propagates_error(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=[_joint("j1", "A", "B"), _joint("j2", "B", "A")])
        create = _creator(twin)
        with pytest.raises(AssemblyGraphError):
            await create(work_product_id=str(wp.id))


class TestMakeBringupChecklistLister:
    async def test_lists_generated_checklists_oldest_first(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=[_joint("joint_1", "link_0", "link_1")])
        create = _creator(twin)
        await create(work_product_id=str(wp.id))

        list_checklists = make_bringup_checklist_lister(twin)
        entries = await list_checklists(work_product_id=str(wp.id))
        assert len(entries) == 1
        assert entries[0]["steps"][0]["instruction"].startswith("Step 1:")

    async def test_unknown_work_product_lists_nothing(self, twin: InMemoryTwinAPI) -> None:
        list_checklists = make_bringup_checklist_lister(twin)
        entries = await list_checklists(work_product_id="11111111-1111-1111-1111-111111111111")
        assert entries == []

    async def test_filters_by_work_product_id(self, twin: InMemoryTwinAPI) -> None:
        wp1 = await _seed_wp(twin, joints=[_joint("j1", "A", "B")])
        wp2 = await _seed_wp(twin, joints=[_joint("j2", "X", "Y")])
        create = _creator(twin)
        await create(work_product_id=str(wp1.id))
        await create(work_product_id=str(wp2.id))

        list_checklists = make_bringup_checklist_lister(twin)
        entries = await list_checklists(work_product_id=str(wp1.id))
        assert len(entries) == 1
        assert entries[0]["steps"][0]["base"] == "A"


class TestCreateBringupChecklistAdapter:
    """twin.create_bringup_checklist tool -- registration + handler."""

    async def test_tool_registered_and_returns_result(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=[_joint("joint_1", "link_0", "link_1")])
        creator = _creator(twin)
        server = TwinServer(twin=twin, bringup_checklist_creator=creator)
        assert "twin.create_bringup_checklist" in server.tool_ids

        out = await server.create_bringup_checklist({"work_product_id": str(wp.id)})
        assert len(out["steps"]) == 1
        assert "node_id" in out

    async def test_missing_work_product_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        creator = _creator(twin)
        server = TwinServer(twin=twin, bringup_checklist_creator=creator)
        with pytest.raises(ValueError, match="work_product_id"):
            await server.create_bringup_checklist({})

    async def test_tool_not_registered_without_creator(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.create_bringup_checklist" not in server.tool_ids
