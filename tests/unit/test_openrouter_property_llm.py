"""Unit tests for the OpenRouter PropertyLLM adapter (MET-462 follow-up)."""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest

from digital_twin.knowledge.openrouter_property_llm import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    OpenRouterPropertyConfig,
    OpenRouterPropertyError,
    OpenRouterPropertyLLM,
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


def _config(**overrides: Any) -> OpenRouterPropertyConfig:
    defaults: dict[str, Any] = {
        "api_key": "test-key",
        "primary_model": DEFAULT_PRIMARY_MODEL,
        "fallback_model": DEFAULT_FALLBACK_MODEL,
        "temperature": 0.0,
        "max_tokens": 64,
    }
    defaults.update(overrides)
    return OpenRouterPropertyConfig(**defaults)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_from_env_happy_path(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "abc")
    monkeypatch.setenv("PROPERTY_EXTRACTION_MODEL", "anthropic/claude-3.5-sonnet")
    monkeypatch.setenv("PROPERTY_EXTRACTION_FALLBACK_MODEL", "fallback/x")
    monkeypatch.setenv("PROPERTY_EXTRACTION_TEMPERATURE", "0.2")
    monkeypatch.setenv("PROPERTY_EXTRACTION_MAX_TOKENS", "1234")

    cfg = OpenRouterPropertyConfig.from_env()

    assert cfg.api_key == "abc"
    assert cfg.primary_model == "anthropic/claude-3.5-sonnet"
    assert cfg.fallback_model == "fallback/x"
    assert cfg.temperature == 0.2
    assert cfg.max_tokens == 1234


def test_config_from_env_defaults_when_optional_missing(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "abc")
    for key in (
        "PROPERTY_EXTRACTION_MODEL",
        "PROPERTY_EXTRACTION_FALLBACK_MODEL",
        "PROPERTY_EXTRACTION_TEMPERATURE",
        "PROPERTY_EXTRACTION_MAX_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = OpenRouterPropertyConfig.from_env()

    assert cfg.primary_model == DEFAULT_PRIMARY_MODEL
    assert cfg.fallback_model == DEFAULT_FALLBACK_MODEL
    assert cfg.temperature == 0.0


def test_config_from_env_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterPropertyError, match="OPEN_ROUTER_API_KEY"):
        OpenRouterPropertyConfig.from_env()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_complete_signature_matches_property_llm_protocol():
    # ``PropertyLLM`` is a Protocol with ``async def complete(self, prompt: str) -> str``.
    # It is not @runtime_checkable, so we verify the method exists and is a coroutine
    # function — that's what callers programming against the Protocol rely on.
    llm = OpenRouterPropertyLLM(_config(), client=_client(lambda _: _ok_response("hi")))
    method = getattr(llm, "complete", None)
    assert method is not None and callable(method)
    assert inspect.iscoroutinefunction(method)


# ---------------------------------------------------------------------------
# Happy path + fallback + failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_raw_model_content():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        import json

        captured["body"] = json.loads(request.content.decode())
        return _ok_response('{"found": false}')

    llm = OpenRouterPropertyLLM(_config(), client=_client(handler))

    result = await llm.complete("extract supply_voltage from this datasheet text")

    assert result == '{"found": false}'
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["x-title"]
    assert captured["body"]["model"] == DEFAULT_PRIMARY_MODEL
    assert captured["body"]["messages"][-1]["content"].startswith("extract supply_voltage")


@pytest.mark.asyncio
async def test_complete_falls_back_to_secondary_on_retryable_error():
    seen_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode())
        seen_models.append(body["model"])
        if body["model"] == DEFAULT_PRIMARY_MODEL:
            return _ok_response("", status=429)
        return _ok_response('{"found": true, "value": "3.3", "unit": "V"}')

    llm = OpenRouterPropertyLLM(_config(), client=_client(handler))

    out = await llm.complete("supply_voltage prompt")
    assert out == '{"found": true, "value": "3.3", "unit": "V"}'
    assert seen_models == [DEFAULT_PRIMARY_MODEL, DEFAULT_FALLBACK_MODEL]


@pytest.mark.asyncio
async def test_complete_raises_when_both_models_fail():
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok_response("", status=503)

    llm = OpenRouterPropertyLLM(_config(), client=_client(handler))

    with pytest.raises(OpenRouterPropertyError, match="both primary"):
        await llm.complete("anything")


@pytest.mark.asyncio
async def test_complete_raises_on_missing_choices_payload():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "rate limited"})

    llm = OpenRouterPropertyLLM(_config(), client=_client(handler))

    with pytest.raises(OpenRouterPropertyError):
        await llm.complete("anything")


@pytest.mark.asyncio
async def test_complete_raises_on_empty_content():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "   "}}]}
        )

    llm = OpenRouterPropertyLLM(_config(), client=_client(handler))

    with pytest.raises(OpenRouterPropertyError, match="empty content"):
        await llm.complete("anything")
