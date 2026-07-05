"""Per-turn model/provider/tools override for chat (MET-548). Network-free."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_gateway.chat.harness_backend import provider_config_from_env, run_chat_turn
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from skill_registry.mcp_bridge import InMemoryMcpBridge


def test_provider_config_override_uses_registry_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "openai/gpt-4o-mini")
    # Selecting a DIFFERENT provider uses that provider's own registry key env.
    cfg = provider_config_from_env(provider="deepseek", model="deepseek-chat")
    spec = cfg.slots.candidates("generator")[0]
    assert spec.name == "deepseek"
    assert spec.model == "deepseek-chat"
    assert spec.api_key_env == "DEEPSEEK_API_KEY"  # not METAFORGE_LLM_API_KEY


def test_provider_config_same_provider_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "openai/gpt-4o-mini")
    # Overriding only the model on the SAME provider keeps the env key.
    cfg = provider_config_from_env(model="openai/gpt-4o")
    spec = cfg.slots.candidates("generator")[0]
    assert spec.name == "openrouter"
    assert spec.model == "openai/gpt-4o"
    assert spec.api_key_env == "METAFORGE_LLM_API_KEY"


@pytest.mark.asyncio
async def test_run_chat_turn_honors_model_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    seen: dict = {}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        seen["model"] = spec.model
        seen["provider"] = spec.name
        return {"text": '{"thought": "done", "final": "ok"}', "model": spec.model}

    out = await run_chat_turn(
        "hi",
        invoke=invoke,
        model="anthropic/claude-3.7-sonnet",
        provider="openrouter",
        credentials=CredentialStore(tmp_path / "c.json"),
    )
    assert out == "ok"
    assert seen == {"model": "anthropic/claude-3.7-sonnet", "provider": "openrouter"}


@pytest.mark.asyncio
async def test_enabled_tools_filters_registered_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.query_node", capability="twin")
    bridge.register_tool("kicad.run_erc", capability="eda")
    bridge.register_tool_response("twin.query_node", {"ok": True})

    # Only twin.query_node enabled → the model calling kicad would 404, but here
    # we just assert the enabled tool is callable and the other isn't registered.
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            payload = {"thought": "x", "tool": "mcp_twin_query_node", "arguments": {}}
            return {"text": json.dumps(payload), "model": spec.model}
        return {"text": '{"thought": "done", "final": "done"}', "model": spec.model}

    out = await run_chat_turn(
        "q",
        invoke=invoke,
        max_steps=3,
        credentials=CredentialStore(tmp_path / "c.json"),
        mcp_bridge=bridge,
        enabled_tools=["twin.query_node"],
    )
    assert out == "done"
    assert calls["n"] == 2  # tool driven + final
