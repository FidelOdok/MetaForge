"""Rotation strategies + transient cooldown via store_backed_invoke (MET-551)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.harness.providers import (
    AuthProfile,
    Credential,
    CredentialStore,
    ProfileRotor,
    ProviderSpec,
    RotationStrategy,
    store_backed_invoke,
)
from orchestrator.harness.providers.pipeline import ProviderError

SPEC = ProviderSpec(name="openrouter", model="x")


def _store(tmp_path: Path) -> CredentialStore:
    s = CredentialStore(tmp_path / "c.json")
    s.add(Credential(provider="openrouter", name="a", api_key_env="KEY_A"))
    s.add(Credential(provider="openrouter", name="b", api_key_env="KEY_B"))
    return s


def _profiles() -> list[AuthProfile]:
    return [AuthProfile(name="a", api_key_env="KEY_A"), AuthProfile(name="b", api_key_env="KEY_B")]


def test_fill_first_always_starts_at_first() -> None:
    rotor = ProfileRotor(_profiles(), strategy=RotationStrategy.FILL_FIRST)
    assert rotor.pin("s1").name == "a"
    assert rotor.pin("s2").name == "a"  # no round-robin spread


def test_round_robin_spreads_new_sessions() -> None:
    rotor = ProfileRotor(_profiles(), strategy=RotationStrategy.ROUND_ROBIN)
    assert rotor.pin("s1").name == "a"
    assert rotor.pin("s2").name == "b"
    assert rotor.pin("s3").name == "a"


def test_least_used_picks_lowest_usage() -> None:
    usage = {"a": 5, "b": 2}
    rotor = ProfileRotor(
        _profiles(),
        strategy=RotationStrategy.LEAST_USED,
        usage=lambda p: usage[p.name],
    )
    assert rotor.pin("s1").name == "b"  # b has lower usage


@pytest.mark.asyncio
async def test_rate_limit_sets_escalating_cooldown_not_dead(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ticks = iter([1000.0, 1000.0, 1000.0, 1000.0])

    async def base(spec: ProviderSpec, request: object) -> str:
        if spec.api_key_env == "KEY_A":
            raise ProviderError("slow down", status_code=429)  # transient
        return "ok"

    invoke = store_backed_invoke(base, store, "openrouter", "s1", now=lambda: next(ticks))
    assert await invoke(SPEC, {}) == "ok"
    a = store.get("openrouter", "a")
    assert a is not None
    assert a.dead is False  # 429 is not terminal
    assert a.cooldown_until == 1030.0  # 1st failure → 30s cooldown
    assert a.failure_count == 1
    # 'b' succeeded → usage counted
    assert store.get("openrouter", "b").usage_count == 1


@pytest.mark.asyncio
async def test_cooled_down_credential_excluded_from_new_rotor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_cooldown("openrouter", "a", now=1000.0)  # a cools down until 1030

    async def base(spec: ProviderSpec, request: object) -> str:
        return f"ok:{spec.api_key_env}"

    # A rotor built while 'a' is cooling only sees 'b'.
    invoke = store_backed_invoke(base, store, "openrouter", "s1", now=lambda: 1010.0)
    assert await invoke(SPEC, {}) == "ok:KEY_B"
