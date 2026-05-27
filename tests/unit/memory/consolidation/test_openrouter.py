"""Unit tests for ``digital_twin.memory.consolidation.openrouter``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from digital_twin.memory.consolidation.openrouter import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    OpenRouterConfig,
    OpenRouterError,
    OpenRouterLLMClient,
)


def _config(**overrides: Any) -> OpenRouterConfig:
    defaults: dict[str, Any] = {
        "api_key": "sk-test",
        "primary_model": "primary/test",
        "fallback_model": "fallback/test",
        "temperature": 0.7,
        "max_tokens": 100,
    }
    defaults.update(overrides)
    return OpenRouterConfig(**defaults)


def _ok_response(narrative: str = "x" * 40, confidence: float = 0.85) -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "narrative": narrative,
                            "confidence": confidence,
                            "kind": "principle",
                        }
                    ),
                }
            }
        ]
    }
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_primary_success_returns_parsed_payload():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["model"] = json.loads(request.content)["model"]
        captured["auth"] = request.headers.get("authorization")
        return _ok_response()

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    out = await client.synthesize_insight("prompt body")
    assert out["narrative"].startswith("x")
    assert out["confidence"] == pytest.approx(0.85)
    assert captured["model"] == "primary/test"
    assert captured["auth"] == "Bearer sk-test"
    await client.close()
    await http.aclose()


@pytest.mark.asyncio
async def test_fallback_used_on_rate_limit():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "primary/test":
            return httpx.Response(429, text="rate limited")
        return _ok_response(narrative="fallback narrative result text")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    out = await client.synthesize_insight("prompt")
    assert out["narrative"] == "fallback narrative result text"
    assert calls == ["primary/test", "fallback/test"]
    await http.aclose()


@pytest.mark.asyncio
async def test_fallback_used_on_5xx():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "primary/test":
            return httpx.Response(503, text="service down")
        return _ok_response()

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    await client.synthesize_insight("prompt")
    assert calls == ["primary/test", "fallback/test"]
    await http.aclose()


@pytest.mark.asyncio
async def test_open_router_error_when_both_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="exhausted")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    with pytest.raises(OpenRouterError, match="both primary"):
        await client.synthesize_insight("prompt")
    await http.aclose()


@pytest.mark.asyncio
async def test_4xx_non_retryable_raises_open_router_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    with pytest.raises(OpenRouterError):
        await client.synthesize_insight("prompt")
    await http.aclose()


@pytest.mark.asyncio
async def test_missing_choices_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    with pytest.raises(OpenRouterError, match="no choices"):
        await client.synthesize_insight("prompt")
    await http.aclose()


@pytest.mark.asyncio
async def test_fenced_json_response_is_parsed():
    fenced_payload = '```json\n{"narrative": "x" * 40, "confidence": 0.8}\n```'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            # Real fenced narrative content (need a valid JSON string in here)
                            "content": '```json\n{"narrative": "a long enough narrative", '
                            '"confidence": 0.8, "kind": "pattern"}\n```',
                        }
                    }
                ]
            },
        )

    # silence unused-var warnings
    _ = fenced_payload
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", transport=transport)
    client = OpenRouterLLMClient(_config(), client=http)

    out = await client.synthesize_insight("prompt")
    assert out["confidence"] == pytest.approx(0.8)
    assert out["kind"] == "pattern"
    await http.aclose()


def test_from_env_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterError, match="OPEN_ROUTER_API_KEY"):
        OpenRouterConfig.from_env()


def test_from_env_picks_up_overrides(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-env")
    monkeypatch.setenv("CONSOLIDATION_MODEL", "anthropic/claude-3-haiku")
    monkeypatch.setenv("CONSOLIDATION_FALLBACK_MODEL", "meta/llama-fast")
    monkeypatch.setenv("CONSOLIDATION_TEMPERATURE", "0.2")
    monkeypatch.setenv("CONSOLIDATION_MAX_TOKENS", "512")

    config = OpenRouterConfig.from_env()
    assert config.api_key == "sk-env"
    assert config.primary_model == "anthropic/claude-3-haiku"
    assert config.fallback_model == "meta/llama-fast"
    assert config.temperature == pytest.approx(0.2)
    assert config.max_tokens == 512


def test_from_env_defaults_to_module_constants(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-env")
    monkeypatch.delenv("CONSOLIDATION_MODEL", raising=False)
    monkeypatch.delenv("CONSOLIDATION_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("CONSOLIDATION_TEMPERATURE", raising=False)
    monkeypatch.delenv("CONSOLIDATION_MAX_TOKENS", raising=False)

    config = OpenRouterConfig.from_env()
    assert config.primary_model == DEFAULT_PRIMARY_MODEL
    assert config.fallback_model == DEFAULT_FALLBACK_MODEL


def test_from_env_recovers_from_bad_float(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-env")
    monkeypatch.setenv("CONSOLIDATION_TEMPERATURE", "not-a-float")
    config = OpenRouterConfig.from_env()
    assert config.temperature == pytest.approx(0.7)
