"""Unit tests for the openai-codex invoke adapter (MET-550). Network-free."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.harness.providers import ProviderSpec, adapters, codex_invoke, resolve_provider
from orchestrator.harness.providers.adapters import default_invoke
from orchestrator.harness.providers.pipeline import ProviderError
from orchestrator.harness.providers.registry import CODEX, get_profile

CODEX_SPEC = ProviderSpec(name="openai-codex", model="gpt-5-codex")


class _Stream:
    """Async-iterable of Responses stream events (codex is streaming-only)."""

    def __init__(self, text: str) -> None:
        self._events = [
            SimpleNamespace(type="response.output_text.delta", delta=text),
            SimpleNamespace(type="response.completed"),
        ]

    def __aiter__(self) -> _Stream:
        self._it = iter(self._events)
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _Responses:
    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self._text = text
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _Stream(self._text or "")


class FakeCodexClient:
    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self.responses = _Responses(text, exc)


def test_codex_registered_with_family() -> None:
    assert get_profile("openai-codex").api_family == CODEX
    assert resolve_provider("codex", "gpt-5-codex").name == "openai-codex"  # alias


@pytest.mark.asyncio
async def test_codex_invoke_uses_responses_api() -> None:
    client = FakeCodexClient(text="hi from codex")
    out = await codex_invoke(CODEX_SPEC, {"system": "be terse", "prompt": "hi"}, client=client)
    assert out == {"text": "hi from codex", "model": "gpt-5-codex"}
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5-codex"
    # input is a list of typed items (a bare string is rejected by the backend)
    assert call["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    assert call["instructions"] == "be terse"
    assert call["store"] is False  # mandatory on the codex backend
    assert call["stream"] is True  # the codex backend is streaming-only


@pytest.mark.asyncio
async def test_codex_invoke_defaults_instructions_when_no_system() -> None:
    client = FakeCodexClient(text="ok")
    await codex_invoke(CODEX_SPEC, {"prompt": "hi"}, client=client)
    # Responses API requires non-empty instructions.
    assert client.responses.calls[0]["instructions"]


@pytest.mark.asyncio
async def test_codex_invoke_maps_errors() -> None:
    exc = Exception("unauthorized")
    exc.status_code = 401  # type: ignore[attr-defined]
    client = FakeCodexClient(exc=exc)
    with pytest.raises(ProviderError) as ei:
        await codex_invoke(CODEX_SPEC, {"prompt": "hi"}, client=client)
    assert ei.value.status_code == 401
    assert not ei.value.retryable  # 401 is terminal


@pytest.mark.asyncio
async def test_default_invoke_routes_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_codex(spec: ProviderSpec, request: object) -> dict:
        seen.append("codex")
        return {"text": "c", "model": spec.model}

    monkeypatch.setattr(adapters, "codex_invoke", fake_codex)
    await default_invoke(CODEX_SPEC, {"prompt": "x"})
    assert seen == ["codex"]
