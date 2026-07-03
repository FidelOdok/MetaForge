"""Unit tests for store→rotation glue with dead-token propagation (MET-551)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.harness.providers import (
    Credential,
    CredentialStore,
    NoHealthyCredentialsError,
    ProviderSpec,
    rotor_from_store,
    store_backed_invoke,
)
from orchestrator.harness.providers.pipeline import ProviderError

SPEC = ProviderSpec(name="openrouter", model="x")


def _store(tmp_path: Path) -> CredentialStore:
    s = CredentialStore(tmp_path / "c.json")
    s.add(Credential(provider="openrouter", name="a", api_key_env="KEY_A"))
    s.add(Credential(provider="openrouter", name="b", api_key_env="KEY_B"))
    return s


def test_rotor_from_store_uses_healthy_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_dead("openrouter", "a")
    rotor = rotor_from_store(store, "openrouter")
    assert rotor.pin("s").name == "b"  # dead one excluded


def test_rotor_from_store_raises_when_none_healthy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_dead("openrouter", "a")
    store.mark_dead("openrouter", "b")
    with pytest.raises(NoHealthyCredentialsError):
        rotor_from_store(store, "openrouter")


@pytest.mark.asyncio
async def test_terminal_failure_blacklists_credential_in_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tried: list[str] = []

    async def base(spec: ProviderSpec, request: object) -> str:
        tried.append(spec.api_key_env or "")
        if spec.api_key_env == "KEY_A":
            raise ProviderError("revoked", status_code=401)  # terminal → dead
        return "ok"

    invoke = store_backed_invoke(base, store, "openrouter", "s1")
    assert await invoke(SPEC, {}) == "ok"
    assert tried == ["KEY_A", "KEY_B"]
    # 'a' persisted dead in the store; a fresh store from disk sees it.
    reopened = CredentialStore(tmp_path / "c.json")
    assert [c.name for c in reopened.healthy("openrouter")] == ["b"]
    assert reopened.credentials("openrouter")[0].dead is True


@pytest.mark.asyncio
async def test_rate_limit_rotates_but_does_not_blacklist(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def base(spec: ProviderSpec, request: object) -> str:
        if spec.api_key_env == "KEY_A":
            raise ProviderError("slow down", status_code=429)  # transient → NOT dead
        return "ok"

    invoke = store_backed_invoke(base, store, "openrouter", "s1")
    assert await invoke(SPEC, {}) == "ok"
    # 'a' is not blacklisted — a 429 is transient, not a bad credential.
    reopened = CredentialStore(tmp_path / "c.json")
    assert reopened.credentials("openrouter")[0].dead is False
