"""native_tools_enabled: default-on for OpenAI-family, flag overrides (MET-10)."""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import native_tools_enabled


def test_default_on_for_native_tool_families(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_NATIVE_TOOLS", raising=False)
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    # OpenAI-compatible and Anthropic adapters both parse native tool_calls.
    for prov in ("openrouter", "openai", "ollama", "vllm", "anthropic"):
        assert native_tools_enabled(prov) is True, prov


def test_default_off_for_non_native_families(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_NATIVE_TOOLS", raising=False)
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    for prov in ("gemini", "bedrock"):
        assert native_tools_enabled(prov) is False, prov
    assert native_tools_enabled(None) is False


def test_flag_forces_on_or_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
    assert native_tools_enabled("openrouter") is False  # override the family default
    assert native_tools_enabled("anthropic") is False  # override the family default
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "true")
    assert native_tools_enabled("gemini") is True  # force on regardless of family
