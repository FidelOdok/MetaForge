"""Unit tests for live provider invoke adapters (MET-548, P0). Network-free."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.harness.providers import ProviderSpec, adapters, anthropic_invoke, openai_invoke
from orchestrator.harness.providers.adapters import (
    _classify_error,
    _normalize_request,
    default_invoke,
)
from orchestrator.harness.providers.pipeline import ProviderError

ANTHROPIC = ProviderSpec(name="anthropic", model="claude-opus-4-8")
OPENAI = ProviderSpec(name="openai", model="gpt-5")


# --- fakes -----------------------------------------------------------------
class _Method:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self._resp = resp
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._resp


class FakeAnthropic:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self.messages = _Method(resp, exc)


class FakeOpenAI:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self.chat = SimpleNamespace(completions=_Method(resp, exc))


def _anthropic_resp(text: str) -> object:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], model="claude-opus-4-8"
    )


def _openai_resp(text: str) -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], model="gpt-5"
    )


# --- classifier ------------------------------------------------------------
def test_classify_429_is_retryable() -> None:
    err = _classify_error(SimpleNamespace(status_code=429, __str__=lambda s: "rate"))  # type: ignore[arg-type]
    assert err.status_code == 429 and err.retryable


def test_classify_500_is_retryable() -> None:
    exc = Exception("boom")
    exc.status_code = 503  # type: ignore[attr-defined]
    assert _classify_error(exc).retryable


def test_classify_400_not_retryable() -> None:
    exc = Exception("bad request")
    exc.status_code = 400  # type: ignore[attr-defined]
    assert not _classify_error(exc).retryable


def test_classify_by_exception_name() -> None:
    class RateLimitError(Exception):
        pass

    assert _classify_error(RateLimitError("slow down")).retryable


# --- request normalization -------------------------------------------------
def test_normalize_prompt_shorthand() -> None:
    system, messages, max_tokens, temp = _normalize_request({"prompt": "hi"})
    assert system is None
    assert messages == [{"role": "user", "content": "hi"}]
    assert max_tokens == 1024


def test_normalize_full_request() -> None:
    system, messages, max_tokens, _ = _normalize_request(
        {"system": "be terse", "messages": [{"role": "user", "content": "q"}], "max_tokens": 50}
    )
    assert system == "be terse"
    assert max_tokens == 50


# --- adapters (happy path) -------------------------------------------------
@pytest.mark.asyncio
async def test_anthropic_invoke_returns_text() -> None:
    client = FakeAnthropic(resp=_anthropic_resp("hello from claude"))
    out = await anthropic_invoke(ANTHROPIC, {"prompt": "hi"}, client=client)
    assert out == {"text": "hello from claude", "model": "claude-opus-4-8"}
    assert client.messages.calls[0]["model"] == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_openai_invoke_prepends_system() -> None:
    client = FakeOpenAI(resp=_openai_resp("hello from gpt"))
    out = await openai_invoke(OPENAI, {"system": "be terse", "prompt": "hi"}, client=client)
    assert out["text"] == "hello from gpt"
    assert client.chat.completions.calls[0]["messages"][0] == {
        "role": "system",
        "content": "be terse",
    }


# --- adapters (error mapping) ----------------------------------------------
@pytest.mark.asyncio
async def test_anthropic_invoke_maps_rate_limit() -> None:
    exc = Exception("429 slow down")
    exc.status_code = 429  # type: ignore[attr-defined]
    client = FakeAnthropic(exc=exc)
    with pytest.raises(ProviderError) as ei:
        await anthropic_invoke(ANTHROPIC, {"prompt": "hi"}, client=client)
    assert ei.value.status_code == 429 and ei.value.retryable


# --- dispatch --------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_invoke_dispatches_by_family(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_anthropic(spec: ProviderSpec, request: object) -> dict:
        seen.append("anthropic")
        return {"text": "a", "model": spec.model}

    async def fake_openai(spec: ProviderSpec, request: object) -> dict:
        seen.append("openai")
        return {"text": "o", "model": spec.model}

    monkeypatch.setattr(adapters, "anthropic_invoke", fake_anthropic)
    monkeypatch.setattr(adapters, "openai_invoke", fake_openai)

    await default_invoke(ANTHROPIC, {"prompt": "x"})
    await default_invoke(OPENAI, {"prompt": "x"})
    await default_invoke(ProviderSpec(name="openrouter", model="z"), {"prompt": "x"})
    assert seen == ["anthropic", "openai", "openai"]
