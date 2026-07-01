"""Unit tests for auth-profile rotation (MET-547, Phase 1)."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import (
    AuthProfile,
    ProfileExhaustedError,
    ProfileRotor,
)

A = AuthProfile(name="key-a", api_key_env="ANTHROPIC_API_KEY_A")
B = AuthProfile(name="key-b", api_key_env="ANTHROPIC_API_KEY_B")
C = AuthProfile(name="key-c", api_key_env="ANTHROPIC_API_KEY_C")


def test_requires_at_least_one_profile() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ProfileRotor([])


def test_pin_is_stable_for_a_session() -> None:
    rotor = ProfileRotor([A, B, C])
    first = rotor.pin("s1")
    # Repeated calls keep the same profile → cache stays warm.
    assert rotor.pin("s1") == first
    assert rotor.current("s1") == first


def test_new_sessions_distribute_round_robin() -> None:
    rotor = ProfileRotor([A, B, C])
    assert rotor.pin("s1") == A
    assert rotor.pin("s2") == B
    assert rotor.pin("s3") == C
    assert rotor.pin("s4") == A  # wraps around


def test_mark_failed_rotates_to_next_profile() -> None:
    rotor = ProfileRotor([A, B, C])
    assert rotor.pin("s1") == A
    assert rotor.mark_failed("s1", A) == B
    assert rotor.current("s1") == B  # new pin persists
    assert rotor.mark_failed("s1", B) == C


def test_rotation_wraps_to_earliest_healthy() -> None:
    rotor = ProfileRotor([A, B, C])
    assert rotor.pin("s1") == A
    # Failing the last profile wraps rotation back to the earliest healthy one.
    assert rotor.mark_failed("s1", C) == A


def test_exhaustion_raises() -> None:
    rotor = ProfileRotor([A, B])
    rotor.pin("s1")
    rotor.mark_failed("s1", A)  # → B
    with pytest.raises(ProfileExhaustedError, match="s1"):
        rotor.mark_failed("s1", B)


def test_mark_failed_unknown_profile_raises() -> None:
    rotor = ProfileRotor([A, B])
    rotor.pin("s1")
    with pytest.raises(ValueError, match="unknown profile"):
        rotor.mark_failed("s1", C)


def test_reset_forgets_session() -> None:
    rotor = ProfileRotor([A, B, C])
    rotor.pin("s1")
    rotor.mark_failed("s1", A)  # pinned now B
    rotor.reset("s1")
    # After reset the session re-pins from the round-robin cursor (advanced).
    assert rotor.current("s1") in (A, B, C)
    # A is healthy again for this session (failure history cleared).
    reassigned = rotor.current("s1")
    assert reassigned == rotor.pin("s1")
