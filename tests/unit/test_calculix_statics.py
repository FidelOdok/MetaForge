"""Unit tests for tool_registry.tools.calculix.statics (FORGE-283)."""

from __future__ import annotations

import pytest

from tool_registry.tools.calculix.statics import (
    ChainJoint,
    ChainLink,
    compute_joint_loads,
    worst_joint_load,
)

_G = 9.80665


class TestComputeJointLoadsSingleLink:
    """Hand-verifiable single-link cantilever cases."""

    def test_horizontal_cantilever_moment_equals_force_times_lever_arm(self):
        """A single 2 kg link, CoM 100 mm out along +x from its joint at the
        origin: classic cantilever hand calc, moment = weight * lever arm."""
        joint = ChainJoint(name="j0", position_world_mm=(0.0, 0.0, 0.0))
        link = ChainLink(name="link0", com_world_mm=(100.0, 0.0, 0.0), mass_kg=2.0)

        loads = compute_joint_loads(links=[link], joints=[joint])

        assert len(loads) == 1
        load = loads[0]
        expected_weight_n = 2.0 * _G
        assert load.supported_mass_kg == pytest.approx(2.0)
        assert load.reaction_force_n == pytest.approx((0.0, 0.0, expected_weight_n))
        # moment = force * lever arm (mm) = expected_weight_n * 100
        expected_moment_n_mm = expected_weight_n * 100.0
        assert load.reaction_moment_n_mm[1] == pytest.approx(-expected_moment_n_mm)
        assert load.reaction_moment_n_mm[0] == pytest.approx(0.0)
        assert load.reaction_moment_n_mm[2] == pytest.approx(0.0)

    def test_link_directly_above_joint_has_zero_moment(self):
        """No horizontal offset -> no lever arm -> zero bending moment."""
        joint = ChainJoint(name="j0", position_world_mm=(0.0, 0.0, 0.0))
        link = ChainLink(name="link0", com_world_mm=(0.0, 0.0, 50.0), mass_kg=3.0)

        loads = compute_joint_loads(links=[link], joints=[joint])

        assert loads[0].reaction_moment_n_mm == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
        assert loads[0].reaction_force_n == pytest.approx((0.0, 0.0, 3.0 * _G))


class TestComputeJointLoadsMultiLink:
    """Serial chains -- outboard summation and payload handling."""

    def test_base_joint_supports_full_chain_mass(self):
        joint0 = ChainJoint(name="base", position_world_mm=(0.0, 0.0, 0.0))
        joint1 = ChainJoint(name="elbow", position_world_mm=(200.0, 0.0, 0.0))
        link0 = ChainLink(name="upper_arm", com_world_mm=(100.0, 0.0, 0.0), mass_kg=1.5)
        link1 = ChainLink(name="forearm", com_world_mm=(300.0, 0.0, 0.0), mass_kg=1.0)

        loads = compute_joint_loads(links=[link0, link1], joints=[joint0, joint1])

        base_load, elbow_load = loads
        assert base_load.supported_mass_kg == pytest.approx(2.5)
        assert elbow_load.supported_mass_kg == pytest.approx(1.0)
        # Base joint carries a bigger moment than the elbow (longer lever arms).
        base_moment_mag = abs(base_load.reaction_moment_n_mm[1])
        elbow_moment_mag = abs(elbow_load.reaction_moment_n_mm[1])
        assert base_moment_mag > elbow_moment_mag

    def test_payload_adds_to_supported_mass_and_moment(self):
        joint = ChainJoint(name="base", position_world_mm=(0.0, 0.0, 0.0))
        link = ChainLink(name="link0", com_world_mm=(100.0, 0.0, 0.0), mass_kg=1.0)

        no_payload = compute_joint_loads(links=[link], joints=[joint])[0]
        with_payload = compute_joint_loads(
            links=[link],
            joints=[joint],
            payload_mass_kg=5.0,
            payload_position_world_mm=(200.0, 0.0, 0.0),
        )[0]

        assert with_payload.supported_mass_kg == pytest.approx(6.0)
        assert no_payload.supported_mass_kg == pytest.approx(1.0)
        assert abs(with_payload.reaction_moment_n_mm[1]) > abs(no_payload.reaction_moment_n_mm[1])

    def test_payload_without_position_raises(self):
        joint = ChainJoint(name="base", position_world_mm=(0.0, 0.0, 0.0))
        link = ChainLink(name="link0", com_world_mm=(100.0, 0.0, 0.0), mass_kg=1.0)

        with pytest.raises(ValueError, match="payload_position_world_mm"):
            compute_joint_loads(links=[link], joints=[joint], payload_mass_kg=5.0)


class TestComputeJointLoadsValidation:
    def test_mismatched_lengths_raise(self):
        joint = ChainJoint(name="base", position_world_mm=(0.0, 0.0, 0.0))
        link0 = ChainLink(name="link0", com_world_mm=(100.0, 0.0, 0.0), mass_kg=1.0)
        link1 = ChainLink(name="link1", com_world_mm=(200.0, 0.0, 0.0), mass_kg=1.0)

        with pytest.raises(ValueError, match="same length"):
            compute_joint_loads(links=[link0, link1], joints=[joint])

    def test_empty_chain_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            compute_joint_loads(links=[], joints=[])


class TestWorstJointLoad:
    def test_returns_largest_moment_magnitude(self):
        joint0 = ChainJoint(name="base", position_world_mm=(0.0, 0.0, 0.0))
        joint1 = ChainJoint(name="elbow", position_world_mm=(200.0, 0.0, 0.0))
        link0 = ChainLink(name="upper_arm", com_world_mm=(100.0, 0.0, 0.0), mass_kg=1.5)
        link1 = ChainLink(name="forearm", com_world_mm=(300.0, 0.0, 0.0), mass_kg=1.0)

        loads = compute_joint_loads(links=[link0, link1], joints=[joint0, joint1])
        worst = worst_joint_load(loads)

        assert worst.joint_name == "base"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            worst_joint_load([])
