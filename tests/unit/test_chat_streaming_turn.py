"""run_chat_turn_streaming — Option B final-answer streaming (MET-548)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from api_gateway.chat.harness_backend import run_chat_turn_streaming
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.providers.pipeline import ProviderError


async def _final_invoke(spec: ProviderSpec, request: object) -> dict:
    # ReAct completes immediately with a final answer (no tools).
    return {"text": '{"thought": "done", "final": "Hello there"}', "model": spec.model}


def _tokens(*toks: str):  # type: ignore[no-untyped-def]
    def stream_invoke(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
        async def gen() -> AsyncIterator[str]:
            for t in toks:
                yield t

        return gen()

    return stream_invoke


@pytest.mark.asyncio
async def test_streams_final_answer_tokens(tmp_path: Path) -> None:
    deltas: list[str] = []

    async def on_delta(d: str) -> None:
        deltas.append(d)

    text = await run_chat_turn_streaming(
        "hi",
        on_delta=on_delta,
        invoke=_final_invoke,
        stream_invoke=_tokens("Hel", "lo ", "there"),
        credentials=CredentialStore(tmp_path / "c.json"),
    )
    assert deltas == ["Hel", "lo ", "there"]  # streamed token-by-token
    assert text == "Hello there"


@pytest.mark.asyncio
async def test_falls_back_to_computed_answer_when_stream_fails(tmp_path: Path) -> None:
    deltas: list[str] = []

    async def on_delta(d: str) -> None:
        deltas.append(d)

    def failing_stream(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
        async def gen() -> AsyncIterator[str]:
            raise ProviderError("no stream", status_code=400)  # terminal → no retry
            yield ""  # unreachable — makes this an async generator

        return gen()

    text = await run_chat_turn_streaming(
        "hi",
        on_delta=on_delta,
        invoke=_final_invoke,
        stream_invoke=failing_stream,
        credentials=CredentialStore(tmp_path / "c.json"),
    )
    # Streaming failed → the already-computed ReAct answer is emitted as one delta.
    assert deltas == ["Hello there"]
    assert text == "Hello there"
