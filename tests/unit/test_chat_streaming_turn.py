"""run_chat_turn_streaming — the loop's own answer streams as chunked deltas (MET-565).

The old design re-generated the final text with a second, context-free model
call and streamed THAT — it could drift from the loop's actual conclusion and
cost an extra call per turn. These tests pin the new contract: what streams is
exactly ``result.output``, chunked losslessly, with no streaming model call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from api_gateway.chat.harness_backend import _chunk_text, run_chat_turn_streaming
from orchestrator.harness.providers import CredentialStore, ProviderSpec


async def _final_invoke(spec: ProviderSpec, request: object) -> dict:
    # ReAct completes immediately with a final answer (no tools).
    return {"text": '{"thought": "done", "final": "Hello there"}', "model": spec.model}


def _forbidden_stream(spec: ProviderSpec, request: object):  # type: ignore[no-untyped-def]
    async def gen() -> AsyncIterator[str]:
        raise AssertionError("stream_invoke must not be called — the loop's answer streams as-is")
        yield ""  # unreachable — makes this an async generator

    return gen()


@pytest.mark.asyncio
async def test_streams_loop_answer_without_second_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")  # fake emits ReAct JSON-final
    deltas: list[str] = []

    async def on_delta(d: str) -> None:
        deltas.append(d)

    text = await run_chat_turn_streaming(
        "hi",
        on_delta=on_delta,
        invoke=_final_invoke,
        stream_invoke=_forbidden_stream,
        credentials=CredentialStore(tmp_path / "c.json"),
    )
    assert text == "Hello there"
    assert deltas  # emitted incrementally
    assert "".join(deltas) == "Hello there"  # deltas reassemble the loop's exact answer


@pytest.mark.asyncio
async def test_empty_answer_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")  # fake emits ReAct JSON-final

    async def empty_final(spec: ProviderSpec, request: object) -> dict:
        return {"text": '{"thought": "…", "final": ""}', "model": spec.model}

    deltas: list[str] = []

    async def on_delta(d: str) -> None:
        deltas.append(d)

    text = await run_chat_turn_streaming(
        "hi",
        on_delta=on_delta,
        invoke=empty_final,
        stream_invoke=_forbidden_stream,
        credentials=CredentialStore(tmp_path / "c.json"),
    )
    # A completed turn with no final text still says something, never blank.
    assert text == "".join(deltas)
    assert text.strip()


def test_chunk_text_is_lossless() -> None:
    text = "word " * 100 + "\n\nfinal   line with  spacing"
    assert "".join(_chunk_text(text)) == text
    assert all(len(c) <= 60 for c in _chunk_text("x" * 30 + " " + "y" * 20))


def test_chunk_text_handles_long_unbroken_token() -> None:
    blob = "A" * 500  # no whitespace at all — must still chunk losslessly
    chunks = _chunk_text(blob)
    assert "".join(chunks) == blob
