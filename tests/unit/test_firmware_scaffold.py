"""Unit tests for _assign_can_ids / make_firmware_scaffold_creator /
twin.create_firmware_scaffold (FORGE-276)."""

from __future__ import annotations

from uuid import UUID

import pytest

from api_gateway.twin.bringup_checklist import AssemblyGraphError
from api_gateway.twin.document_recorder import make_document_recorder
from api_gateway.twin.firmware_scaffold import (
    _assign_can_ids,
    _render_firmware_header_c,
    _render_pinmap_csv,
    make_firmware_scaffold_creator,
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
    joint_type: str = "revolute",
    limits: dict | None = None,
) -> dict:
    j = {"name": name, "type": joint_type, "base": base, "follower": follower}
    if limits is not None:
        j["limits"] = limits
    return j


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
    recorder = make_document_recorder(twin)
    return make_firmware_scaffold_creator(twin, document_recorder=recorder)


class TestAssignCanIds:
    def test_single_joint_gets_can_id_one(self) -> None:
        joints = _assign_can_ids([_joint("j1", "link0", "link1")])
        assert len(joints) == 1
        assert joints[0]["can_id"] == 1

    def test_linear_chain_can_ids_follow_build_order(self) -> None:
        joints = _assign_can_ids([_joint("j2", "link1", "link2"), _joint("j1", "link0", "link1")])
        assert [j["name"] for j in joints] == ["j1", "j2"]
        assert [j["can_id"] for j in joints] == [1, 2]

    def test_cyclic_graph_raises(self) -> None:
        with pytest.raises(AssemblyGraphError):
            _assign_can_ids([_joint("j1", "A", "B"), _joint("j2", "B", "A")])


class TestRenderPinmapCsv:
    def test_header_and_rows(self) -> None:
        joints = _assign_can_ids(
            [_joint("joint_1", "link_0", "link_1", limits={"lower": -1.57, "upper": 1.57})]
        )
        csv = _render_pinmap_csv(joints)
        lines = csv.strip().splitlines()
        assert lines[0] == "joint_name,joint_type,can_id,lower_limit,upper_limit"
        assert lines[1] == "joint_1,revolute,1,-1.57,1.57"

    def test_missing_limits_renders_empty_fields(self) -> None:
        joints = _assign_can_ids([_joint("joint_1", "link_0", "link_1")])
        csv = _render_pinmap_csv(joints)
        assert csv.strip().splitlines()[1] == "joint_1,revolute,1,,"


class TestRenderFirmwareHeaderC:
    def test_syntactically_sane_header(self) -> None:
        joints = _assign_can_ids(
            [_joint("joint_1", "link_0", "link_1", limits={"lower": -1.0, "upper": 1.0})]
        )
        header = _render_firmware_header_c("Test Arm", joints)
        assert header.count("#ifndef") == 1
        assert header.count("#endif") == 1
        assert header.startswith("// Test Arm")
        assert "JOINT_JOINT_1_CONFIG" in header
        assert ".can_id = 1" in header
        assert ".lower_limit = -1" in header
        assert ".upper_limit = 1" in header

    def test_joint_name_with_non_alnum_chars_sanitized(self) -> None:
        joints = _assign_can_ids([_joint("joint-1 (hip)", "A", "B")])
        header = _render_firmware_header_c("Arm", joints)
        assert "JOINT_JOINT_1__HIP_CONFIG" in header


class TestMakeFirmwareScaffoldCreator:
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

    async def test_real_joints_produce_two_linked_work_products(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(
            twin,
            joints=[
                _joint("joint_2", "link_1", "link_2", limits={"lower": -1.0, "upper": 1.0}),
                _joint("joint_1", "link_0", "link_1", limits={"lower": -2.0, "upper": 2.0}),
            ],
        )
        create = _creator(twin)
        result = await create(work_product_id=str(wp.id))

        assert len(result["joints"]) == 2
        assert result["joints"][0]["joint_name"] == "joint_1"
        assert result["joints"][0]["can_id"] == 1
        assert result["joints"][1]["can_id"] == 2

        pinmap_wp = await twin.get_work_product(UUID(result["pinmap_node_id"]))
        assert pinmap_wp is not None
        assert pinmap_wp.type == WorkProductType.PINMAP
        assert pinmap_wp.metadata["work_product_id"] == str(wp.id)

        source_wp = await twin.get_work_product(UUID(result["firmware_source_node_id"]))
        assert source_wp is not None
        assert source_wp.type == WorkProductType.FIRMWARE_SOURCE

    async def test_cyclic_graph_propagates_error(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=[_joint("j1", "A", "B"), _joint("j2", "B", "A")])
        create = _creator(twin)
        with pytest.raises(AssemblyGraphError):
            await create(work_product_id=str(wp.id))


class TestCreateFirmwareScaffoldAdapter:
    """twin.create_firmware_scaffold tool -- registration + handler."""

    async def test_tool_registered_and_returns_result(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=[_joint("joint_1", "link_0", "link_1")])
        creator = _creator(twin)
        server = TwinServer(twin=twin, firmware_scaffold_creator=creator)
        assert "twin.create_firmware_scaffold" in server.tool_ids

        out = await server.create_firmware_scaffold({"work_product_id": str(wp.id)})
        assert len(out["joints"]) == 1
        assert "pinmap_node_id" in out
        assert "firmware_source_node_id" in out

    async def test_missing_work_product_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        creator = _creator(twin)
        server = TwinServer(twin=twin, firmware_scaffold_creator=creator)
        with pytest.raises(ValueError, match="work_product_id"):
            await server.create_firmware_scaffold({})

    async def test_tool_not_registered_without_creator(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.create_firmware_scaffold" not in server.tool_ids
