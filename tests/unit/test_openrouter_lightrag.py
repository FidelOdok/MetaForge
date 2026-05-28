"""Unit tests for the OpenRouter-backed LightRAG ``llm_model_func`` (MET-466 Task 2)."""

from __future__ import annotations

import inspect
import json
from typing import Any

import httpx
import pytest

from digital_twin.knowledge.lightrag_service import LightRAGConfig
from digital_twin.knowledge.openrouter_lightrag import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    OpenRouterLightRAGConfig,
    OpenRouterLightRAGError,
    build_openrouter_llm_model_func,
)


def _ok_response(content: str, status: int = 200) -> httpx.Response:
    if status != 200:
        return httpx.Response(status, text="upstream error")
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    return httpx.Response(status, json=body)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    )


def _config(**overrides: Any) -> OpenRouterLightRAGConfig:
    defaults: dict[str, Any] = {
        "api_key": "test-key",
        "primary_model": DEFAULT_PRIMARY_MODEL,
        "fallback_model": DEFAULT_FALLBACK_MODEL,
        "temperature": 0.2,
        "max_tokens": 64,
    }
    defaults.update(overrides)
    return OpenRouterLightRAGConfig(**defaults)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_from_env_happy_path(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "abc")
    monkeypatch.setenv("LIGHTRAG_MODEL", "anthropic/claude-3.5-sonnet")
    monkeypatch.setenv("LIGHTRAG_FALLBACK_MODEL", "fallback/x")
    monkeypatch.setenv("LIGHTRAG_TEMPERATURE", "0.4")
    monkeypatch.setenv("LIGHTRAG_MAX_TOKENS", "1024")
    cfg = OpenRouterLightRAGConfig.from_env()
    assert cfg.api_key == "abc"
    assert cfg.primary_model == "anthropic/claude-3.5-sonnet"
    assert cfg.fallback_model == "fallback/x"
    assert cfg.temperature == 0.4
    assert cfg.max_tokens == 1024


def test_config_from_env_defaults_when_optional_missing(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "abc")
    for k in (
        "LIGHTRAG_MODEL",
        "LIGHTRAG_FALLBACK_MODEL",
        "LIGHTRAG_TEMPERATURE",
        "LIGHTRAG_MAX_TOKENS",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = OpenRouterLightRAGConfig.from_env()
    assert cfg.primary_model == DEFAULT_PRIMARY_MODEL
    assert cfg.fallback_model == DEFAULT_FALLBACK_MODEL


def test_config_from_env_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterLightRAGError, match="OPEN_ROUTER_API_KEY"):
        OpenRouterLightRAGConfig.from_env()


# ---------------------------------------------------------------------------
# Factory: signature compatibility + behaviour
# ---------------------------------------------------------------------------


def test_factory_returns_async_callable_with_lightrag_signature():
    func = build_openrouter_llm_model_func(_config(), client=_client(lambda _r: _ok_response("hi")))
    assert inspect.iscoroutinefunction(func)
    sig = inspect.signature(func)
    # Must accept LightRAG's named kwargs (system_prompt, history_messages) + **kwargs
    assert "system_prompt" in sig.parameters
    assert "history_messages" in sig.parameters
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


@pytest.mark.asyncio
async def test_call_returns_raw_model_content_and_sends_expected_payload():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return _ok_response("extracted entities")

    func = build_openrouter_llm_model_func(_config(), client=_client(handler))

    result = await func(
        "extract entities from this datasheet text",
        system_prompt="You extract Component / Supplier entities.",
        history_messages=[{"role": "user", "content": "previous turn"}],
    )

    assert result == "extracted entities"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["x-title"]
    assert captured["body"]["model"] == DEFAULT_PRIMARY_MODEL
    # message stack: system + history + final user prompt
    roles = [m["role"] for m in captured["body"]["messages"]]
    assert roles == ["system", "user", "user"]
    assert captured["body"]["messages"][-1]["content"].startswith("extract entities")


@pytest.mark.asyncio
async def test_call_ignores_unknown_kwargs():
    func = build_openrouter_llm_model_func(_config(), client=_client(lambda _r: _ok_response("ok")))
    # LightRAG may pass additional kwargs (e.g. ``hashing_kv``) — they must
    # not blow up our adapter.
    out = await func("a prompt", hashing_kv={"x": 1}, mode="local")
    assert out == "ok"


@pytest.mark.asyncio
async def test_call_falls_back_on_429():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body["model"])
        if body["model"] == DEFAULT_PRIMARY_MODEL:
            return _ok_response("", status=429)
        return _ok_response("recovered")

    func = build_openrouter_llm_model_func(_config(), client=_client(handler))
    assert await func("prompt") == "recovered"
    assert seen == [DEFAULT_PRIMARY_MODEL, DEFAULT_FALLBACK_MODEL]


@pytest.mark.asyncio
async def test_call_raises_when_both_models_fail():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _ok_response("", status=503)

    func = build_openrouter_llm_model_func(_config(), client=_client(handler))
    with pytest.raises(OpenRouterLightRAGError, match="both primary"):
        await func("prompt")


@pytest.mark.asyncio
async def test_call_raises_on_missing_choices():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "rate limited"})

    func = build_openrouter_llm_model_func(_config(), client=_client(handler))
    with pytest.raises(OpenRouterLightRAGError):
        await func("prompt")


@pytest.mark.asyncio
async def test_call_raises_on_empty_content():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "   "}}]}
        )

    func = build_openrouter_llm_model_func(_config(), client=_client(handler))
    with pytest.raises(OpenRouterLightRAGError, match="empty content"):
        await func("prompt")


# ---------------------------------------------------------------------------
# LightRAGConfig integration
# ---------------------------------------------------------------------------


def test_lightrag_config_accepts_llm_model_func():
    func = build_openrouter_llm_model_func(_config(), client=_client(lambda _r: _ok_response("x")))
    cfg = LightRAGConfig(llm_model_func=func)
    assert cfg.llm_model_func is func


def test_lightrag_config_defaults_llm_model_func_to_none():
    cfg = LightRAGConfig()
    # When None, the service falls back to ``_noop_llm_model_func`` —
    # naive vector mode keeps its existing behaviour.
    assert cfg.llm_model_func is None
