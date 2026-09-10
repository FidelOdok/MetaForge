"""Unit tests for live provider invoke adapters (MET-548, P0). Network-free."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.harness.providers import ProviderSpec, adapters, anthropic_invoke, openai_invoke
from orchestrator.harness.providers.adapters import (
    _classify_error,
    _desanitize_openai_tool_name,
    _normalize_request,
    _sanitize_openai_tool_names,
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


def _openai_tool_call_resp(name: str) -> object:
    tc = SimpleNamespace(id="call_1", function=SimpleNamespace(name=name, arguments="{}"))
    msg = SimpleNamespace(content=None, tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], model="gpt-5")


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
    assert max_tokens == 8192  # MET-565: the old 1024 silently truncated long answers


def test_normalize_max_tokens_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_MAX_OUTPUT_TOKENS", "2048")
    _, _, max_tokens, _ = _normalize_request({"prompt": "hi"})
    assert max_tokens == 2048


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


# --- MET-738: OpenAI-family tool-name sanitization -------------------------
# OpenAI's function-calling API rejects `function.name` values that don't
# match ^[a-zA-Z0-9_-]+$, but every MetaForge tool id is dotted
# (`namespace.action`, e.g. `twin.commit_geometry`). openai_invoke /
# openai_stream_events must sanitize outgoing schemas and desanitize
# incoming tool_calls so the round trip is invisible to the native loop.
def test_sanitize_openai_tool_names_replaces_dot() -> None:
    tools = [{"type": "function", "function": {"name": "twin.commit_geometry", "parameters": {}}}]
    out = _sanitize_openai_tool_names(tools)
    assert out[0]["function"]["name"] == "twin__commit_geometry"
    # original list/dicts left untouched (no in-place mutation)
    assert tools[0]["function"]["name"] == "twin.commit_geometry"


def test_sanitize_openai_tool_names_passes_through_malformed_entries() -> None:
    tools = [{"x": 1}, {"function": "not-a-dict"}]
    assert _sanitize_openai_tool_names(tools) == tools


def test_desanitize_openai_tool_name_round_trip() -> None:
    assert _desanitize_openai_tool_name("cadquery__export_urdf") == "cadquery.export_urdf"


@pytest.mark.asyncio
async def test_openai_invoke_sanitizes_outgoing_tool_names_and_desanitizes_reply() -> None:
    client = FakeOpenAI(resp=_openai_tool_call_resp("twin__commit_geometry"))
    tools = [{"type": "function", "function": {"name": "twin.commit_geometry", "parameters": {}}}]
    out = await openai_invoke(OPENAI, {"prompt": "go", "tools": tools}, client=client)
    sent = client.chat.completions.calls[0]["tools"]
    assert sent[0]["function"]["name"] == "twin__commit_geometry"
    assert out["tool_calls"] == [{"id": "call_1", "name": "twin.commit_geometry", "arguments": {}}]
