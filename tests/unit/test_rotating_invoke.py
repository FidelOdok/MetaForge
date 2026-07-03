"""Unit tests for auth-profile rotation wired into the invoke path (MET-551)."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import (
    AuthProfile,
    ProfileRotor,
    ProviderSpec,
    rotating_invoke,
)
from orchestrator.harness.providers.pipeline import ProviderError

SPEC = ProviderSpec(name="openrouter", model="x", base_url="https://openrouter.ai/api/v1")
A = AuthProfile(name="key-a", api_key_env="KEY_A")
B = AuthProfile(name="key-b", api_key_env="KEY_B", base_url="https://alt/v1")


@pytest.mark.asyncio
async def test_applies_pinned_profile_credentials() -> None:
    rotor = ProfileRotor([A, B])
    seen: list[ProviderSpec] = []

    async def base(spec: ProviderSpec, request: object) -> str:
        seen.append(spec)
        return "ok"

    invoke = rotating_invoke(base, rotor, "s1")
    await invoke(SPEC, {})
    assert seen[0].api_key_env == "KEY_A"  # profile creds applied
    assert seen[0].base_url == "https://openrouter.ai/api/v1"  # profile has none → keep spec's


@pytest.mark.asyncio
async def test_rotates_on_auth_error_then_succeeds() -> None:
    rotor = ProfileRotor([A, B])
    tried: list[str] = []

    async def base(spec: ProviderSpec, request: object) -> str:
        tried.append(spec.api_key_env or "")
        if spec.api_key_env == "KEY_A":
            raise ProviderError("unauthorized", status_code=401)
        return "ok"

    invoke = rotating_invoke(base, rotor, "s1")
    assert await invoke(SPEC, {}) == "ok"
    assert tried == ["KEY_A", "KEY_B"]  # rotated to B
    assert rotor.current("s1").name == "key-b"  # pin advanced to the healthy profile


@pytest.mark.asyncio
async def test_rotation_applies_profile_base_url() -> None:
    rotor = ProfileRotor([A, B])

    async def base(spec: ProviderSpec, request: object) -> str:
        if spec.api_key_env == "KEY_A":
            raise ProviderError("rate", status_code=429)
        return spec.base_url or ""

    invoke = rotating_invoke(base, rotor, "s1")
    assert await invoke(SPEC, {}) == "https://alt/v1"  # B's base_url applied


@pytest.mark.asyncio
async def test_exhaustion_raises_last_error() -> None:
    rotor = ProfileRotor([A, B])

    async def base(spec: ProviderSpec, request: object) -> str:
        raise ProviderError("nope", status_code=403)

    invoke = rotating_invoke(base, rotor, "s1")
    with pytest.raises(ProviderError, match="nope"):
        await invoke(SPEC, {})


@pytest.mark.asyncio
async def test_non_auth_error_does_not_rotate() -> None:
    rotor = ProfileRotor([A, B])
    calls = {"n": 0}

    async def base(spec: ProviderSpec, request: object) -> str:
        calls["n"] += 1
        raise ProviderError("bad request", status_code=400)

    invoke = rotating_invoke(base, rotor, "s1")
    with pytest.raises(ProviderError, match="bad request"):
        await invoke(SPEC, {})
    assert calls["n"] == 1  # no rotation on a non-auth error
