"""Registry wired into config + chat provider resolution (MET-549)."""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import provider_config_from_env
from orchestrator.harness.providers import load_provider_config


def test_config_enriches_known_provider_base_url() -> None:
    cfg = load_provider_config(
        {"roles": {"generator": [{"provider": "deepseek", "model": "deepseek-chat"}]}}
    )
    spec = cfg.slots.candidates("generator")[0]
    assert spec.name == "deepseek"
    assert spec.base_url == "https://api.deepseek.com"  # from the registry
    assert spec.api_key_env == "DEEPSEEK_API_KEY"


def test_config_explicit_fields_win_over_registry() -> None:
    cfg = load_provider_config(
        {
            "roles": {
                "generator": [
                    {
                        "provider": "openrouter",
                        "model": "x",
                        "base_url": "https://proxy/v1",
                        "api_key_env": "MY_KEY",
                    }
                ]
            }
        }
    )
    spec = cfg.slots.candidates("generator")[0]
    assert spec.base_url == "https://proxy/v1"
    assert spec.api_key_env == "MY_KEY"


def test_config_unknown_provider_falls_back() -> None:
    cfg = load_provider_config(
        {
            "roles": {
                "generator": [
                    {"provider": "some-internal-llm", "model": "x", "base_url": "https://h/v1"}
                ]
            }
        }
    )
    spec = cfg.slots.candidates("generator")[0]
    assert spec.name == "some-internal-llm"
    assert spec.base_url == "https://h/v1"


def test_chat_env_resolves_provider_via_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "deepseek-chat")
    monkeypatch.delenv("METAFORGE_LLM_BASE_URL", raising=False)
    spec = provider_config_from_env().slots.candidates("generator")[0]
    assert spec.name == "deepseek"
    assert spec.base_url == "https://api.deepseek.com"
    assert spec.api_key_env == "METAFORGE_LLM_API_KEY"  # chat's single key env still wins


def test_chat_env_explicit_base_url_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "m")
    monkeypatch.setenv("METAFORGE_LLM_BASE_URL", "https://my-gateway/v1")
    spec = provider_config_from_env().slots.candidates("generator")[0]
    assert spec.base_url == "https://my-gateway/v1"
