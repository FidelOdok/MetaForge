"""Unit tests for harness-backed chat (MET-548, surface A). Network-free."""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import (
    chat_harness_enabled,
    provider_config_from_env,
    run_chat_turn,
)
from orchestrator.harness.providers import ProviderSpec


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_CHAT_HARNESS", raising=False)
    assert chat_harness_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "on", "YES"])
def test_flag_on(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("METAFORGE_CHAT_HARNESS", val)
    assert chat_harness_enabled() is True


def test_provider_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("METAFORGE_LLM_PROVIDER", "METAFORGE_LLM_MODEL", "METAFORGE_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = provider_config_from_env()
    specs = cfg.slots.candidates("generator")
    assert specs[0] == ProviderSpec(
        name="anthropic", model="claude-opus-4-8", api_key_env="METAFORGE_LLM_API_KEY"
    )


def test_provider_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "meta-llama/llama-4")
    monkeypatch.setenv("METAFORGE_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    spec = provider_config_from_env().slots.candidates("generator")[0]
    assert spec.name == "openrouter" and spec.base_url == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_run_chat_turn_returns_final(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)

    async def fake_invoke(spec: ProviderSpec, request: object) -> dict:
        return {
            "text": '{"thought": "easy", "final": "hello from the harness"}',
            "model": spec.model,
        }

    out = await run_chat_turn("say hi", invoke=fake_invoke)
    assert out == "hello from the harness"


@pytest.mark.asyncio
async def test_run_chat_turn_exhaustion_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)

    async def never_final(spec: ProviderSpec, request: object) -> dict:
        # Always proposes a (nonexistent) tool, never finalizes -> exhaust.
        return {"text": '{"tool": "noop", "arguments": {}}', "model": spec.model}

    out = await run_chat_turn("loop forever", invoke=never_final, max_steps=2)
    assert "couldn't converge" in out
