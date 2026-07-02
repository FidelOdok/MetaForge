"""Unit tests for the AWS Bedrock invoke adapter (MET-549). Network-free."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import ProviderSpec, adapters, bedrock_invoke, resolve_provider
from orchestrator.harness.providers.adapters import default_invoke
from orchestrator.harness.providers.pipeline import ProviderError
from orchestrator.harness.providers.registry import BEDROCK, get_profile

BR = ProviderSpec(name="bedrock", model="anthropic.claude-3-5-sonnet")


class FakeBedrockClient:
    def __init__(self, resp: object = None, exc: Exception | None = None) -> None:
        self._resp = resp
        self._exc = exc
        self.calls: list[dict] = []

    def converse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._resp


def _converse_resp(text: str) -> dict:
    return {"output": {"message": {"content": [{"text": text}]}}}


def test_bedrock_registered() -> None:
    assert get_profile("bedrock").api_family == BEDROCK
    assert resolve_provider("aws-bedrock", "m").name == "bedrock"


@pytest.mark.asyncio
async def test_bedrock_invoke_returns_text() -> None:
    client = FakeBedrockClient(resp=_converse_resp("hi from bedrock"))
    out = await bedrock_invoke(BR, {"system": "be terse", "prompt": "hi"}, client=client)
    assert out == {"text": "hi from bedrock", "model": "anthropic.claude-3-5-sonnet"}
    call = client.calls[0]
    assert call["messages"][0]["content"][0]["text"] == "hi"
    assert call["system"] == [{"text": "be terse"}]


@pytest.mark.asyncio
async def test_bedrock_throttling_maps_to_retryable_429() -> None:
    exc = Exception("throttled")
    exc.response = {  # type: ignore[attr-defined]
        "Error": {"Code": "ThrottlingException"},
        "ResponseMetadata": {"HTTPStatusCode": 400},
    }
    client = FakeBedrockClient(exc=exc)
    with pytest.raises(ProviderError) as ei:
        await bedrock_invoke(BR, {"prompt": "hi"}, client=client)
    assert ei.value.status_code == 429 and ei.value.retryable


@pytest.mark.asyncio
async def test_default_invoke_routes_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_bedrock(spec: ProviderSpec, request: object) -> dict:
        seen.append("bedrock")
        return {"text": "b", "model": spec.model}

    monkeypatch.setattr(adapters, "bedrock_invoke", fake_bedrock)
    await default_invoke(BR, {"prompt": "x"})
    assert seen == ["bedrock"]
