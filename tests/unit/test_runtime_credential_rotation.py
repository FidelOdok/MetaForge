"""Credential rotation wired into HarnessRuntime.complete (MET-551)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.harness import HarnessRuntime, build_agent_runtime
from orchestrator.harness.providers import (
    Credential,
    CredentialStore,
    ProviderSpec,
    load_provider_config,
)
from orchestrator.harness.providers.pipeline import ProviderError

CONFIG = load_provider_config({"roles": {"generator": [{"provider": "openrouter", "model": "x"}]}})


def _store(tmp_path: Path) -> CredentialStore:
    s = CredentialStore(tmp_path / "c.json")
    s.add(Credential(provider="openrouter", name="a", api_key_env="KEY_A"))
    s.add(Credential(provider="openrouter", name="b", api_key_env="KEY_B"))
    return s


@pytest.mark.asyncio
async def test_runtime_without_store_passes_invoke_through() -> None:
    rt = HarnessRuntime.build(CONFIG)

    async def invoke(spec: ProviderSpec, request: object) -> str:
        return f"ok:{spec.api_key_env}"

    # No store → the spec's own api_key_env (from the registry) is used.
    assert await rt.complete("generator", {}, invoke) == "ok:OPENROUTER_API_KEY"


@pytest.mark.asyncio
async def test_runtime_with_store_rotates_and_blacklists(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rt = HarnessRuntime.build(CONFIG, credentials=store, session_id="s1")
    tried: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        tried.append(spec.api_key_env or "")
        if spec.api_key_env == "KEY_A":
            raise ProviderError("revoked", status_code=401)
        return "ok"

    assert await rt.complete("generator", {}, invoke) == "ok"
    assert tried == ["KEY_A", "KEY_B"]  # rotated through stored creds
    # terminal 401 blacklisted 'a' in the store (persisted)
    assert [c.name for c in CredentialStore(tmp_path / "c.json").healthy("openrouter")] == ["b"]


@pytest.mark.asyncio
async def test_build_agent_runtime_plumbs_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ctx = build_agent_runtime(CONFIG, credentials=store, session_id="s2")
    assert ctx.runtime.credentials is store
    assert ctx.runtime.session_id == "s2"
