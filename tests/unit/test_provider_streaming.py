"""Streaming adapters + pipeline.stream_complete (MET-548). Network-free."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.harness.providers import (
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
    codex_stream,
    openai_stream,
)
from orchestrator.harness.providers.pipeline import (
    AllProvidersFailedError,
    ProviderError,
    ProviderPipeline,
)

SPEC = ProviderSpec(name="openai", model="gpt-x")


# ---- OpenAI-compatible streaming adapter ----------------------------------


class _OpenAIStream:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents

    def __aiter__(self) -> _OpenAIStream:
        self._it = iter(self._contents)
        return self

    async def __anext__(self) -> object:
        try:
            content = next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


class _OpenAICompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents

    async def create(self, **kwargs: object) -> object:
        return _OpenAIStream(self._contents)


class FakeOpenAI:
    def __init__(self, contents: list[str]) -> None:
        self.chat = SimpleNamespace(completions=_OpenAICompletions(contents))


@pytest.mark.asyncio
async def test_openai_stream_yields_nonempty_deltas() -> None:
    client = FakeOpenAI(["Hel", "", "lo"])  # empty chunk filtered out
    deltas = [d async for d in openai_stream(SPEC, {"prompt": "hi"}, client=client)]
    assert deltas == ["Hel", "lo"]


# ---- codex streaming adapter (shares _codex_stream_deltas) -----------------


class _CodexStream:
    def __init__(self, deltas: list[str]) -> None:
        self._events = [
            SimpleNamespace(type="response.output_text.delta", delta=d) for d in deltas
        ] + [SimpleNamespace(type="response.completed", delta="")]

    def __aiter__(self) -> _CodexStream:
        self._it = iter(self._events)
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _CodexResponses:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    async def create(self, **kwargs: object) -> object:
        return _CodexStream(self._deltas)


class FakeCodex:
    def __init__(self, deltas: list[str]) -> None:
        self.responses = _CodexResponses(deltas)


@pytest.mark.asyncio
async def test_codex_stream_yields_deltas() -> None:
    client = FakeCodex(["p", "ong"])
    spec = ProviderSpec(name="openai-codex", model="gpt-5.5")
    deltas = [d async for d in codex_stream(spec, {"prompt": "hi"}, client=client)]
    assert deltas == ["p", "ong"]


# ---- pipeline.stream_complete ---------------------------------------------


async def _no_sleep(_s: float) -> None:
    return None


def _pipe(retries: int = 0) -> ProviderPipeline:
    slots = RoleModelSlots(
        slots={
            "generator": [ProviderSpec(name="p1", model="m"), ProviderSpec(name="p2", model="m")]
        }
    )
    return ProviderPipeline(
        slots, retry_policy=RetryPolicy(api_max_retries=retries), sleep=_no_sleep
    )


@pytest.mark.asyncio
async def test_stream_complete_fails_over_before_first_token() -> None:
    def stream_invoke(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            if spec.name == "p1":
                raise ProviderError("boom", status_code=500)
                yield ""  # unreachable — makes this an async generator
            yield "hi"

        return gen()

    pipe = _pipe(retries=0)
    deltas = [d async for d in pipe.stream_complete("generator", {}, stream_invoke)]
    assert deltas == ["hi"]  # p1 failed pre-token → failed over to p2


@pytest.mark.asyncio
async def test_stream_complete_no_failover_after_first_token() -> None:
    def stream_invoke(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            if spec.name == "p1":
                yield "a"
                raise ProviderError("mid-stream", status_code=500)
            yield "should-not-reach"

        return gen()

    pipe = _pipe(retries=0)
    collected: list[str] = []
    with pytest.raises(ProviderError):
        async for d in pipe.stream_complete("generator", {}, stream_invoke):
            collected.append(d)
    assert collected == ["a"]  # committed to p1; no failover once streaming


@pytest.mark.asyncio
async def test_stream_complete_retries_same_provider_before_failover() -> None:
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    slots = RoleModelSlots(slots={"generator": [ProviderSpec(name="p1", model="m")]})
    pipe = ProviderPipeline(slots, retry_policy=RetryPolicy(api_max_retries=2), sleep=sleep)

    def stream_invoke(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            raise ProviderError("rate", status_code=429)
            yield ""

        return gen()

    with pytest.raises(AllProvidersFailedError):
        async for _ in pipe.stream_complete("generator", {}, stream_invoke):
            pass
    assert len(slept) == 2  # retried twice with backoff before giving up
