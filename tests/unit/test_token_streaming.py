"""MET-591: token-level streaming through the agent loop. Network-free.

Covers the event-stream adapters (text deltas + fragmented tool-call
assembly), the pipeline's first-event failover contract, and the native
loop's on_stream_event forwarding with invoke fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.harness.native_tools import run_native_tools
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.providers.adapters import (
    StreamingUnsupported,
    default_stream_events,
    openai_stream_events,
)
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tools import ToolRegistry

CONFIG = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)
SPEC = ProviderSpec(name="openai", model="gpt-x", api_key="k")


def _chunk(content: str | None = None, tool_calls: list | None = None) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tc(index: int, id: str | None = None, name: str | None = None, args: str | None = None) -> Any:
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id, function=fn)


class _FakeOpenAI:
    """chat.completions.create(stream=True) double yielding scripted chunks."""

    def __init__(self, chunks: list[Any]) -> None:
        async def _create(**kwargs: Any) -> Any:
            self.kwargs = kwargs

            async def _gen():
                for c in chunks:
                    yield c

            return _gen()

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


# --- openai event adapter -----------------------------------------------------------
@pytest.mark.asyncio
async def test_openai_events_assemble_text_and_fragmented_tool_calls() -> None:
    client = _FakeOpenAI(
        [
            _chunk(content="let me "),
            _chunk(content="check"),
            _chunk(tool_calls=[_tc(0, id="c1", name="twin_get", args='{"no')]),
            _chunk(tool_calls=[_tc(0, args='de_id": "n1"}')]),
            _chunk(tool_calls=[_tc(1, id="c2", name="list_projects", args="{}")]),
        ]
    )
    events = [
        e
        async for e in openai_stream_events(
            SPEC,
            {"messages": [{"role": "user", "content": "go"}], "tools": [{"x": 1}]},
            client=client,
        )
    ]
    deltas = [e["text"] for e in events if e["type"] == "text_delta"]
    assert deltas == ["let me ", "check"]
    result = events[-1]["result"]
    assert events[-1]["type"] == "response"
    assert result["text"] == "let me check"
    assert result["tool_calls"] == [
        {"id": "c1", "name": "twin_get", "arguments": {"node_id": "n1"}},
        {"id": "c2", "name": "list_projects", "arguments": {}},
    ]
    assert client.kwargs["stream"] is True and client.kwargs["tools"] == [{"x": 1}]


@pytest.mark.asyncio
async def test_openai_events_text_only_response() -> None:
    client = _FakeOpenAI([_chunk(content="hello")])
    events = [
        e
        async for e in openai_stream_events(
            SPEC, {"messages": [{"role": "user", "content": "hi"}]}, client=client
        )
    ]
    assert events[-1]["result"] == {"text": "hello", "model": "gpt-x"}
    assert "tool_calls" not in events[-1]["result"]


# --- family dispatch ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unsupported_families_raise_before_first_event() -> None:
    for family in ("openai-codex", "gemini", "bedrock"):
        agen = default_stream_events(ProviderSpec(name=family, model="m", api_key="k"), {})
        with pytest.raises(StreamingUnsupported):
            await agen.__anext__()


# --- pipeline failover -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_pipeline_falls_over_before_first_event() -> None:
    rt = HarnessRuntime.build(
        load_provider_config(
            {
                "roles": {
                    "generator": [
                        {"provider": "openai-codex", "model": "m1"},
                        {"provider": "anthropic", "model": "m2"},
                    ]
                }
            }
        )
    )

    async def fake_stream_events(spec: ProviderSpec, request: Any):
        if spec.name == "openai-codex":
            raise StreamingUnsupported(spec.name)
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "response", "result": {"text": "ok", "model": spec.model}}

    events = [e async for e in rt.stream_events("generator", {}, fake_stream_events)]
    assert [e["type"] for e in events] == ["text_delta", "response"]
    assert events[-1]["result"]["model"] == "m2"  # second candidate served


# --- native loop integration --------------------------------------------------------------
@pytest.mark.asyncio
async def test_native_loop_streams_thinking_and_uses_response() -> None:
    tools = ToolRegistry()

    async def echo(arguments: dict[str, object]) -> object:
        return {"ok": True}

    tools.register_native("echo", description="e", input_schema={"type": "object"}, handler=echo)
    rt = HarnessRuntime.build(CONFIG, tools=tools)

    calls = {"n": 0}

    async def fake_stream_events(spec: ProviderSpec, request: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "text_delta", "text": "using "}
            yield {"type": "text_delta", "text": "echo"}
            yield {
                "type": "response",
                "result": {
                    "text": "using echo",
                    "tool_calls": [{"id": "c1", "name": "echo", "arguments": {}}],
                    "model": spec.model,
                },
            }
        else:
            yield {"type": "text_delta", "text": "done!"}
            yield {"type": "response", "result": {"text": "done!", "model": spec.model}}

    thoughts: list[str] = []

    async def on_stream_event(event: dict[str, Any]) -> None:
        if event["type"] == "text_delta":
            thoughts.append(event["text"])

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        raise AssertionError("invoke must not be called when streaming succeeds")

    result = await run_native_tools(
        rt,
        "go",
        invoke=invoke,
        max_steps=4,
        on_stream_event=on_stream_event,
        stream_events=fake_stream_events,
    )
    assert result.status == "completed" and result.output == "done!"
    assert thoughts == ["using ", "echo", "done!"]
    assert len(result.steps) == 1  # the echo call


@pytest.mark.asyncio
async def test_native_loop_falls_back_to_invoke_when_streaming_fails() -> None:
    rt = HarnessRuntime.build(CONFIG)

    async def broken_stream(spec: ProviderSpec, request: Any):
        raise StreamingUnsupported(spec.name)
        yield {}

    async def on_stream_event(event: dict[str, Any]) -> None:
        return None

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        return {"text": "fallback answer", "model": spec.model}

    result = await run_native_tools(
        rt,
        "go",
        invoke=invoke,
        max_steps=2,
        on_stream_event=on_stream_event,
        stream_events=broken_stream,
    )
    assert result.status == "completed" and result.output == "fallback answer"


@pytest.mark.asyncio
async def test_no_thinking_callback_keeps_invoke_path() -> None:
    rt = HarnessRuntime.build(CONFIG)
    streamed = {"n": 0}

    async def fake_stream_events(spec: ProviderSpec, request: Any):
        streamed["n"] += 1
        yield {"type": "response", "result": {"text": "x", "model": spec.model}}

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        return {"text": "via invoke", "model": spec.model}

    result = await run_native_tools(
        rt, "go", invoke=invoke, max_steps=2, stream_events=fake_stream_events
    )
    assert result.output == "via invoke" and streamed["n"] == 0


# --- MET-592: typed events -----------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_events_announce_action_at_first_named_fragment() -> None:
    client = _FakeOpenAI(
        [
            _chunk(content="hmm "),
            _chunk(tool_calls=[_tc(0, id="c1", name="freecad_pad", args='{"le')]),
            _chunk(tool_calls=[_tc(0, args='n": 5}')]),
        ]
    )
    events = [
        e
        async for e in openai_stream_events(
            SPEC,
            {"messages": [{"role": "user", "content": "go"}], "tools": [{}]},
            client=client,
        )
    ]
    types = [e["type"] for e in events]
    # action announced right after its name arrives, BEFORE args complete.
    assert types == ["text_delta", "action_started", "response"]
    assert events[1]["name"] == "freecad_pad"
    assert events[-1]["result"]["tool_calls"][0]["arguments"] == {"len": 5}


class _FakeAnthropicStream:
    """messages.stream(...) double yielding raw typed events."""

    def __init__(self, events: list[Any], final: Any) -> None:
        self._events = events
        self._final = final

    def __call__(self, **kwargs: Any) -> Any:
        return self

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def __aiter__(self) -> Any:
        async def _gen():
            for e in self._events:
                yield e

        return _gen()

    async def get_final_message(self) -> Any:
        return self._final


@pytest.mark.asyncio
async def test_anthropic_events_type_thinking_text_and_action() -> None:
    from orchestrator.harness.providers.adapters import anthropic_stream_events

    raw = [
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="thinking_delta", thinking="plan: pad the sketch"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="I'll pad it now."),
        ),
        SimpleNamespace(
            type="content_block_start",
            content_block=SimpleNamespace(type="tool_use", name="freecad_pad_sketch"),
        ),
    ]
    final = SimpleNamespace(
        model="claude-x",
        content=[
            SimpleNamespace(type="text", text="I'll pad it now."),
            SimpleNamespace(type="tool_use", id="t1", name="freecad_pad_sketch", input={"len": 5}),
        ],
    )
    stream = _FakeAnthropicStream(raw, final)
    client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    spec = ProviderSpec(name="anthropic", model="claude-x", api_key="k")
    events = [
        e
        async for e in anthropic_stream_events(
            spec, {"messages": [{"role": "user", "content": "go"}]}, client=client
        )
    ]
    assert [e["type"] for e in events] == [
        "thinking_delta",
        "text_delta",
        "action_started",
        "response",
    ]
    assert events[0]["text"] == "plan: pad the sketch"
    assert events[2]["name"] == "freecad_pad_sketch"
    assert events[-1]["result"]["tool_calls"][0]["name"] == "freecad_pad_sketch"


@pytest.mark.asyncio
async def test_loop_forwards_typed_events(tmp_path: Any) -> None:
    rt = HarnessRuntime.build(CONFIG)
    seen: list[tuple[str, str]] = []

    async def fake_stream_events(spec: ProviderSpec, request: Any):
        yield {"type": "thinking_delta", "text": "reasoning..."}
        yield {"type": "action_started", "name": "echo"}
        yield {"type": "text_delta", "text": "answer"}
        yield {"type": "response", "result": {"text": "answer", "model": spec.model}}

    async def on_stream_event(event: dict[str, Any]) -> None:
        seen.append((event["type"], event.get("text") or event.get("name") or ""))

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        raise AssertionError("streaming path expected")

    result = await run_native_tools(
        rt,
        "go",
        invoke=invoke,
        max_steps=2,
        on_stream_event=on_stream_event,
        stream_events=fake_stream_events,
    )
    assert result.output == "answer"
    assert seen == [
        ("thinking_delta", "reasoning..."),
        ("action_started", "echo"),
        ("text_delta", "answer"),
    ]


# --- MET-596: token usage capture ----------------------------------------------------


@pytest.mark.asyncio
async def test_openai_stream_events_capture_usage() -> None:
    usage_chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=34),
    )
    client = _FakeOpenAI([_chunk(content="hi"), usage_chunk])
    events = [
        e
        async for e in openai_stream_events(
            SPEC, {"messages": [{"role": "user", "content": "x"}]}, client=client
        )
    ]
    assert events[-1]["result"]["usage"] == {"input_tokens": 120, "output_tokens": 34}
    # include_usage was requested from the provider.
    assert client.kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_native_loop_sums_usage_across_calls() -> None:
    tools = ToolRegistry()

    async def echo(arguments: dict[str, object]) -> object:
        return {"ok": True}

    tools.register_native("echo", description="e", input_schema={"type": "object"}, handler=echo)
    rt = HarnessRuntime.build(CONFIG, tools=tools)
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "echo", "arguments": {}}],
                "model": spec.model,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            }
        return {
            "text": "done",
            "model": spec.model,
            "usage": {"input_tokens": 150, "output_tokens": 25},
        }

    result = await run_native_tools(rt, "go", invoke=invoke, max_steps=4)
    assert result.usage == {"input_tokens": 250, "output_tokens": 35}


@pytest.mark.asyncio
async def test_loop_usage_none_when_provider_reports_nothing() -> None:
    rt = HarnessRuntime.build(CONFIG)

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        return {"text": "done", "model": spec.model}

    result = await run_native_tools(rt, "go", invoke=invoke, max_steps=2)
    assert result.usage is None
