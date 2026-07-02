"""Unit tests for the provider registry (MET-549)."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import (
    UnknownProviderError,
    available_providers,
    resolve_provider,
)
from orchestrator.harness.providers.adapters import default_invoke  # noqa: F401 (dispatch check)
from orchestrator.harness.providers.registry import ANTHROPIC, OPENAI, get_profile


def test_available_providers_includes_core() -> None:
    ids = available_providers()
    for expected in ["anthropic", "openai", "openrouter", "deepseek", "xai", "ollama"]:
        assert expected in ids


def test_resolve_openrouter_fixed_base_url() -> None:
    spec = resolve_provider("openrouter", "meta-llama/llama-4")
    assert spec.name == "openrouter"
    assert spec.base_url == "https://openrouter.ai/api/v1"
    assert spec.api_key_env == "OPENROUTER_API_KEY"
    assert spec.model == "meta-llama/llama-4"


def test_resolve_anthropic_family_and_no_base_url() -> None:
    spec = resolve_provider("anthropic", "claude-opus-4-8")
    assert spec.name == "anthropic"  # routes to native adapter in default_invoke
    assert spec.base_url is None
    assert get_profile("anthropic").api_family == ANTHROPIC


def test_alias_resolution() -> None:
    assert resolve_provider("claude", "m").name == "anthropic"
    assert resolve_provider("grok", "m").name == "xai"
    assert resolve_provider("qwen", "m").name == "alibaba"
    assert get_profile("hf").api_family == OPENAI


def test_base_url_from_env_when_not_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
    spec = resolve_provider("zai", "glm-4.6")
    assert spec.base_url == "https://api.z.ai/api/paas/v4"


def test_explicit_base_url_overrides_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_DEEPSEEK_BASE_URL", "https://ignored")
    spec = resolve_provider("deepseek", "deepseek-chat", base_url="https://proxy.internal/v1")
    assert spec.base_url == "https://proxy.internal/v1"


def test_no_base_url_when_unset() -> None:
    # A region-specific provider with no env set resolves to None (SDK default / error later).
    spec = resolve_provider("minimax", "abab")
    assert spec.base_url is None


def test_custom_api_key_env_override() -> None:
    spec = resolve_provider("openrouter", "m", api_key_env="MY_KEY")
    assert spec.api_key_env == "MY_KEY"


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        resolve_provider("nope", "m")
