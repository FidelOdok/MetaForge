"""Unit tests for the chain forward-kinematics / Jacobian /
make_repeatability_estimator (FORGE-285, gap G-F9).

The Jacobian test cases use a 3-link planar chain (all joints rotate
about the world z-axis) with hand-derivable expected values: rotating a
revolute joint by a small angle moves every downstream point
tangentially, at a rate equal to the point's perpendicular distance from
the joint's rotation axis (standard lever-arm/angular-velocity relation,
v = omega x r). At zero angles this chain lies along +x, so the
tangential direction for every joint is +y, and the expected Jacobian
magnitude is exactly that perpendicular distance.
"""

from __future__ import annotations

import math

import pytest

from api_gateway.twin.bringup_checklist import AssemblyGraphError
from api_gateway.twin.repeatability import (
    AK80_8_JOINT_NAME,
    AK80_8_OUTPUT_ENCODER_BITS,
    IGUS_REBEL_WHOLE_ARM_REPEATABILITY_MM,
    _encoder_resolution_rad,
    _forward_kinematics,
    _jacobian_column_mm_per_rad,
    make_repeatability_estimator,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


def _joint(
    name: str,
    base: str,
    follower: str,
    anchor: tuple[float, float, float] = (0.0, 0.0, 0.0),
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0),
    joint_type: str = "revolute",
) -> dict:
    return {
        "name": name,
        "type": joint_type,
        "base": base,
        "follower": follower,
        "anchor": anchor,
        "axis": axis,
    }


# 3-link planar chain: root --jointA(anchor 0,0,0)--> link1
#                            --jointB(anchor 10,0,0)--> link2
#                            --jointC(anchor 5,0,0)--> link3
# At zero angles: link1=(0,0,0), link2=(10,0,0), link3=(15,0,0).
_PLANAR_CHAIN = [
    _joint("jointA", "root", "link1", anchor=(0.0, 0.0, 0.0)),
    _joint("jointB", "link1", "link2", anchor=(10.0, 0.0, 0.0)),
    _joint("jointC", "link2", "link3", anchor=(5.0, 0.0, 0.0)),
]


class TestForwardKinematics:
    def test_zero_angles_gives_straight_chain(self) -> None:
        frames = _forward_kinematics(_PLANAR_CHAIN, {})
        assert frames["link1"][1] == pytest.approx((0.0, 0.0, 0.0))
        assert frames["link2"][1] == pytest.approx((10.0, 0.0, 0.0))
        assert frames["link3"][1] == pytest.approx((15.0, 0.0, 0.0))

    def test_root_joint_rotation_moves_everything_downstream(self) -> None:
        frames = _forward_kinematics(_PLANAR_CHAIN, {"jointA": math.pi / 2})
        # 90 degrees about +z: (x,y,0) -> (-y,x,0). link3 was at (15,0,0).
        x, y, z = frames["link3"][1]
        assert x == pytest.approx(0.0, abs=1e-9)
        assert y == pytest.approx(15.0, abs=1e-9)
        assert z == pytest.approx(0.0, abs=1e-9)

    def test_fixed_joint_does_not_rotate(self) -> None:
        chain = [_joint("jf", "root", "link1", anchor=(10.0, 0.0, 0.0), joint_type="fixed")]
        frames = _forward_kinematics(chain, {"jf": math.pi})  # angle ignored for fixed joints
        assert frames["link1"][1] == pytest.approx((10.0, 0.0, 0.0))

    def test_cyclic_graph_raises(self) -> None:
        with pytest.raises(AssemblyGraphError):
            _forward_kinematics([_joint("j1", "A", "B"), _joint("j2", "B", "A")], {})


class TestJacobianColumn:
    def test_root_joint_jacobian_matches_full_lever_arm(self) -> None:
        # jointA's axis passes through the world origin; link3 sits 15mm
        # away in x at zero angles -> tangential sensitivity = 15 mm/rad
        # in +y, 0 elsewhere.
        jac = _jacobian_column_mm_per_rad(_PLANAR_CHAIN, "jointA", "link3", {})
        assert jac[0] == pytest.approx(0.0, abs=1e-6)
        assert jac[1] == pytest.approx(15.0, rel=1e-6)
        assert jac[2] == pytest.approx(0.0, abs=1e-6)

    def test_middle_joint_jacobian_matches_downstream_lever_arm_only(self) -> None:
        # jointB's axis passes through link1 (x=0); link3 is 5mm further
        # out than jointB's own anchor (10 -> 15), so jointB's rotation
        # only sees the 5mm segment beyond it, not the full 15mm to the
        # root -- tangential sensitivity = 5 mm/rad in +y.
        jac = _jacobian_column_mm_per_rad(_PLANAR_CHAIN, "jointB", "link3", {})
        assert jac[0] == pytest.approx(0.0, abs=1e-6)
        assert jac[1] == pytest.approx(5.0, rel=1e-6)
        assert jac[2] == pytest.approx(0.0, abs=1e-6)

    def test_leaf_joints_own_rotation_does_not_move_its_own_follower(self) -> None:
        # jointC's rotation only affects points downstream of link3; link3
        # itself (jointC's own follower) is positioned purely by link2's
        # frame + the anchor offset, so its Jacobian w.r.t. jointC is zero.
        jac = _jacobian_column_mm_per_rad(_PLANAR_CHAIN, "jointC", "link3", {})
        assert jac == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    def test_unreachable_target_raises(self) -> None:
        with pytest.raises(ValueError, match="not reachable"):
            _jacobian_column_mm_per_rad(_PLANAR_CHAIN, "jointA", "nonexistent_part", {})


class TestEncoderResolutionRad:
    def test_15_bit_matches_known_value(self) -> None:
        # 2*pi / 32768 -- a real, independently-computable check, not a
        # magic-number comparison against the implementation's own formula.
        assert _encoder_resolution_rad(15) == pytest.approx(2.0 * math.pi / 32768.0)

    def test_higher_bit_count_is_finer_resolution(self) -> None:
        assert _encoder_resolution_rad(16) < _encoder_resolution_rad(15)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


# A minimal 2-joint real-shaped chain: joint_2 (AK80-8, real encoder data)
# plus joint_3 (an igus-actuated joint, no per-joint encoder data) -- just
# enough to exercise both contribution paths without needing all 6 real
# AR4 joints.
_AR4_LIKE_CHAIN = [
    _joint(
        AK80_8_JOINT_NAME,
        "link_1",
        "link_2",
        anchor=(0.0, -64.2, 169.77),
        axis=(1.0, 0.0, 0.0),
    ),
    _joint("joint_3", "link_2", "link_3", anchor=(-6.999, 0.0, 305.0), axis=(1.0, 0.0, 0.0)),
]


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


class TestMakeRepeatabilityEstimator:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        get = make_repeatability_estimator(twin)
        with pytest.raises(ValueError, match="no work_product"):
            await get(work_product_id="11111111-1111-1111-1111-111111111111")

    async def test_no_assembly_metadata_raises(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        get = make_repeatability_estimator(twin)
        with pytest.raises(ValueError, match="no assembly.joints metadata"):
            await get(work_product_id=str(wp.id))

    async def test_combines_ak80_jacobian_with_igus_whole_arm_bound(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(twin, joints=_AR4_LIKE_CHAIN)
        get = make_repeatability_estimator(twin)
        result = await get(work_product_id=str(wp.id))

        assert result["target_part"] == "link_3"
        # AK80-8 contribution = |jacobian| * resolution_rad -- both real,
        # independently recomputable from the same inputs this test seeds.
        ak80_entry = next(
            c for c in result["contributions"] if c["joint_name"] == AK80_8_JOINT_NAME
        )
        # Compared loosely: the response rounds resolution_rad to 8 decimal
        # places for display, so an exact-precision comparison would fail
        # on rounding alone, not on the underlying math being wrong.
        assert ak80_entry["resolution_rad"] == pytest.approx(
            _encoder_resolution_rad(AK80_8_OUTPUT_ENCODER_BITS), abs=1e-7
        )
        expected_total = (
            ak80_entry["jacobian_mm_per_rad"] * ak80_entry["resolution_rad"]
            + IGUS_REBEL_WHOLE_ARM_REPEATABILITY_MM
        )
        assert result["worst_case_repeatability_mm"] == pytest.approx(expected_total, rel=1e-4)

    async def test_passes_flag_respects_requirement(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=_AR4_LIKE_CHAIN)
        get = make_repeatability_estimator(twin)

        strict = await get(work_product_id=str(wp.id), requirement_mm=0.0001)
        assert strict["passes"] is False

        lenient = await get(work_product_id=str(wp.id), requirement_mm=1000.0)
        assert lenient["passes"] is True

    async def test_missing_joint_1_produces_a_warning(self, twin: InMemoryTwinAPI) -> None:
        joints_with_j1 = [
            _joint("joint_1", "base_link", "link_1", anchor=(0.0, 0.0, 0.0)),
            *_AR4_LIKE_CHAIN,
        ]
        wp = await _seed_wp(twin, joints=joints_with_j1)
        get = make_repeatability_estimator(twin)
        result = await get(work_product_id=str(wp.id))
        assert any("joint_1" in w for w in result["warnings"])

    async def test_no_joint_1_means_no_warning(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=_AR4_LIKE_CHAIN)
        get = make_repeatability_estimator(twin)
        result = await get(work_product_id=str(wp.id))
        assert result["warnings"] == []

    async def test_explicit_end_effector_part_is_respected(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin, joints=_AR4_LIKE_CHAIN)
        get = make_repeatability_estimator(twin)
        result = await get(work_product_id=str(wp.id), end_effector_part="link_2")
        assert result["target_part"] == "link_2"
