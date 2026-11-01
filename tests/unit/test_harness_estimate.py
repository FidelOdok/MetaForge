"""Unit tests for _cumulative_harness_lengths / make_harness_estimate_getter /
twin.get_harness_estimate (FORGE-275)."""

from __future__ import annotations

import pytest

from api_gateway.twin.bringup_checklist import AssemblyGraphError
from api_gateway.twin.harness_estimate import (
    _cumulative_harness_lengths,
    _segment_length_mm,
    make_harness_estimate_getter,
)
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


def _joint(
    name: str,
    base: str,
    follower: str,
    anchor: tuple[float, float, float] = (0.0, 0.0, 0.0),
    joint_type: str = "revolute",
) -> dict:
    return {"name": name, "type": joint_type, "base": base, "follower": follower, "anchor": anchor}


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


def _getter(twin: InMemoryTwinAPI):
    return make_harness_estimate_getter(twin)


class TestSegmentLengthMm:
    def test_known_3_4_5_triangle(self) -> None:
        assert _segment_length_mm((3.0, 4.0, 0.0)) == pytest.approx(5.0)

    def test_zero_anchor_is_zero(self) -> None:
        assert _segment_length_mm((0.0, 0.0, 0.0)) == 0.0

    def test_missing_anchor_is_zero(self) -> None:
        assert _segment_length_mm(None) == 0.0


class TestCumulativeHarnessLengths:
    def test_single_joint_length_is_its_own_segment(self) -> None:
        joints = _cumulative_harness_lengths(
            [_joint("j1", "base", "link1", anchor=(3.0, 4.0, 0.0))]
        )
        assert len(joints) == 1
        assert joints[0]["segment_length_mm"] == pytest.approx(5.0)
        assert joints[0]["cable_length_estimate_mm"] == pytest.approx(5.0)

    def test_linear_chain_accumulates(self) -> None:
        # j1: base->link1, segment 5mm (3,4,0). j2: link1->link2, segment 12mm (0,0,12).
        # j2's cable length should be 5 + 12 = 17mm, not 12mm alone.
        joints = _cumulative_harness_lengths(
            [
                _joint("j2", "link1", "link2", anchor=(0.0, 0.0, 12.0)),
                _joint("j1", "base", "link1", anchor=(3.0, 4.0, 0.0)),
            ]
        )
        by_name = {j["joint_name"]: j for j in joints}
        assert by_name["j1"]["cable_length_estimate_mm"] == pytest.approx(5.0)
        assert by_name["j2"]["segment_length_mm"] == pytest.approx(12.0)
        assert by_name["j2"]["cable_length_estimate_mm"] == pytest.approx(17.0)

    def test_independent_subassemblies_each_start_at_zero(self) -> None:
        # Two separate root parts (rootA, rootB) -- neither is a follower anywhere.
        joints = _cumulative_harness_lengths(
            [
                _joint("jA", "rootA", "linkA", anchor=(10.0, 0.0, 0.0)),
                _joint("jB", "rootB", "linkB", anchor=(0.0, 0.0, 0.0)),
            ]
        )
        by_name = {j["joint_name"]: j for j in joints}
        assert by_name["jA"]["cable_length_estimate_mm"] == pytest.approx(10.0)
        assert by_name["jB"]["cable_length_estimate_mm"] == pytest.approx(0.0)

    def test_cyclic_graph_raises(self) -> None:
        with pytest.raises(AssemblyGraphError):
            _cumulative_harness_lengths([_joint("j1", "A", "B"), _joint("j2", "B", "A")])


class TestMakeHarnessEstimateGetter:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        get = _getter(twin)
        with pytest.raises(ValueError, match="no work_product"):
            await get(work_product_id="11111111-1111-1111-1111-111111111111")

    async def test_work_product_with_no_assembly_metadata_raises(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(twin)
        get = _getter(twin)
        with pytest.raises(ValueError, match="no assembly.joints metadata"):
            await get(work_product_id=str(wp.id))

    async def test_real_joints_produce_cumulative_estimates(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(
            twin,
            joints=[
                _joint("joint_2", "link_1", "link_2", anchor=(0.0, 0.0, 10.0)),
                _joint("joint_1", "link_0", "link_1", anchor=(0.0, 0.0, 5.0)),
            ],
        )
        get = _getter(twin)
        result = await get(work_product_id=str(wp.id))

        assert result["work_product_id"] == str(wp.id)
        joints = result["joints"]
        assert len(joints) == 2
        assert joints[0]["joint_name"] == "joint_1"
        assert joints[0]["cable_length_estimate_mm"] == pytest.approx(5.0)
        assert joints[1]["joint_name"] == "joint_2"
        assert joints[1]["cable_length_estimate_mm"] == pytest.approx(15.0)


class TestGetHarnessEstimateAdapter:
    """twin.get_harness_estimate tool -- registration + handler."""

    async def test_tool_registered_and_returns_result(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=[_joint("joint_1", "link_0", "link_1")])
        getter = _getter(twin)
        server = TwinServer(twin=twin, harness_estimate_getter=getter)
        assert "twin.get_harness_estimate" in server.tool_ids

        out = await server.get_harness_estimate({"work_product_id": str(wp.id)})
        assert len(out["joints"]) == 1

    async def test_missing_work_product_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        getter = _getter(twin)
        server = TwinServer(twin=twin, harness_estimate_getter=getter)
        with pytest.raises(ValueError, match="work_product_id"):
            await server.get_harness_estimate({})

    async def test_tool_not_registered_without_getter(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.get_harness_estimate" not in server.tool_ids
