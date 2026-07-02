"""Unit tests for the Gemini invoke adapter (MET-549). Network-free."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.harness.providers import ProviderSpec, adapters, gemini_invoke, resolve_provider
from orchestrator.harness.providers.adapters import default_invoke
from orchestrator.harness.providers.pipeline import ProviderError
from orchestrator.harness.providers.registry import GEMINI, get_profile

GEM = ProviderSpec(name="gemini", model="gemini-2.5-pro")


class _AioModels:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self._resp = resp
        self._exc = exc
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._resp


class FakeGenaiClient:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self.aio = SimpleNamespace(models=_AioModels(resp, exc))


def test_gemini_registered_with_family() -> None:
    assert get_profile("gemini").api_family == GEMINI
    spec = resolve_provider("google", "gemini-2.5-pro")  # alias
    assert spec.name == "gemini"


@pytest.mark.asyncio
async def test_gemini_invoke_returns_text() -> None:
    client = FakeGenaiClient(resp=SimpleNamespace(text="hi from gemini"))
    out = await gemini_invoke(GEM, {"system": "be terse", "prompt": "hi"}, client=client)
    assert out == {"text": "hi from gemini", "model": "gemini-2.5-pro"}
    call = client.aio.models.calls[0]
    assert call["model"] == "gemini-2.5-pro"
    assert call["config"]["system_instruction"] == "be terse"


@pytest.mark.asyncio
async def test_gemini_invoke_maps_errors() -> None:
    exc = Exception("resource exhausted")
    exc.status_code = 429  # type: ignore[attr-defined]
    client = FakeGenaiClient(exc=exc)
    with pytest.raises(ProviderError) as ei:
        await gemini_invoke(GEM, {"prompt": "hi"}, client=client)
    assert ei.value.retryable


@pytest.mark.asyncio
async def test_default_invoke_routes_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_gemini(spec: ProviderSpec, request: object) -> dict:
        seen.append("gemini")
        return {"text": "g", "model": spec.model}

    monkeypatch.setattr(adapters, "gemini_invoke", fake_gemini)
    await default_invoke(GEM, {"prompt": "x"})
    assert seen == ["gemini"]
