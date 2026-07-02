"""Unit tests for the openai-codex invoke adapter (MET-550). Network-free."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.harness.providers import ProviderSpec, adapters, codex_invoke, resolve_provider
from orchestrator.harness.providers.adapters import default_invoke
from orchestrator.harness.providers.pipeline import ProviderError
from orchestrator.harness.providers.registry import CODEX, get_profile

CODEX_SPEC = ProviderSpec(name="openai-codex", model="gpt-5-codex")


class _Responses:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self._resp = resp
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._resp


class FakeCodexClient:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self.responses = _Responses(resp, exc)


def test_codex_registered_with_family() -> None:
    assert get_profile("openai-codex").api_family == CODEX
    assert resolve_provider("codex", "gpt-5-codex").name == "openai-codex"  # alias


@pytest.mark.asyncio
async def test_codex_invoke_uses_responses_api() -> None:
    client = FakeCodexClient(resp=SimpleNamespace(output_text="hi from codex"))
    out = await codex_invoke(CODEX_SPEC, {"system": "be terse", "prompt": "hi"}, client=client)
    assert out == {"text": "hi from codex", "model": "gpt-5-codex"}
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5-codex"
    assert call["input"] == "hi"
    assert call["instructions"] == "be terse"


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
