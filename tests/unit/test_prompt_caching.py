"""Prompt caching: stable prefixes, cache markers, cached-token accounting (FORGE-478)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.harness.providers import caching
from orchestrator.harness.providers.adapters import anthropic_invoke, openai_invoke
from orchestrator.harness.providers.registry import ProviderSpec
from orchestrator.harness.providers.usage import (
    UsageStore,
    configure_usage_store,
    record_call,
    usage_scope,
)

SYSTEM = "You are the MetaForge harness."


def _tool(name: str, **props: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": props},
        },
    }


TOOLS = [_tool("twin.get_node", id={"type": "string"}), _tool("project.list")]


class _Messages:
    def __init__(self, usage: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._usage = usage or SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            model="claude-x",
            usage=self._usage,
            stop_reason="end_turn",
        )


def _prefix(kwargs: dict[str, Any]) -> str:
    """Serialised cacheable prefix: tools then system (Anthropic's hash order)."""
    return json.dumps([kwargs.get("tools"), kwargs.get("system")], sort_keys=False)


async def _anthropic_steps(messages_per_step: list[list[dict[str, Any]]], tools: list) -> _Messages:
    fake = _Messages()
    spec = ProviderSpec(name="anthropic", model="claude-x", api_key_env="K")
    for msgs in messages_per_step:
        await anthropic_invoke(
            spec,
            {"system": SYSTEM, "messages": msgs, "tools": tools},
            client=SimpleNamespace(messages=fake),
        )
    return fake


@pytest.mark.asyncio
async def test_anthropic_prefix_identical_across_steps_and_marked() -> None:
    step1 = [{"role": "user", "content": "design a bracket"}]
    step2 = [
        *step1,
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "user", "content": "more"},
    ]
    # Reversed, differently ordered schemas must serialise identically.
    shuffled = [json.loads(json.dumps(t, sort_keys=True)) for t in reversed(TOOLS)]
    fake = await _anthropic_steps([step1, step2], TOOLS)
    other = await _anthropic_steps([step1], shuffled)

    first, second = fake.calls
    assert _prefix(first) == _prefix(second) == _prefix(other.calls[0])
    assert [t["name"] for t in first["tools"]] == sorted(t["name"] for t in first["tools"])
    # Breakpoints: last tool, system block, last message block (max 4).
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in first["tools"][0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    marked = [
        b
        for m in first["messages"]
        for b in (m["content"] if isinstance(m["content"], list) else [])
        if isinstance(b, dict) and "cache_control" in b
    ]
    assert len(marked) == 1


@pytest.mark.asyncio
async def test_system_suffix_stays_outside_the_cached_block() -> None:
    fake = _Messages()
    spec = ProviderSpec(name="anthropic", model="claude-x", api_key_env="K")
    await anthropic_invoke(
        spec,
        {
            "system": SYSTEM,
            "system_suffix": "NOTE: round-specific",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": TOOLS,
        },
        client=SimpleNamespace(messages=fake),
    )
    blocks = fake.calls[0]["system"]
    assert blocks[0]["text"] == SYSTEM and "cache_control" in blocks[0]
    assert blocks[1]["text"] == "NOTE: round-specific" and "cache_control" not in blocks[1]


class _Completions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            prompt_tokens=2000,
            completion_tokens=10,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1536),
        )
        msg = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            model="gpt-x",
            usage=usage,
        )


@pytest.mark.asyncio
async def test_openai_stable_order_and_cache_key() -> None:
    comp = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    spec = ProviderSpec(name="openai", model="gpt-x", api_key_env="K")
    for content in ("one", "two"):
        await openai_invoke(
            spec,
            {
                "system": SYSTEM,
                "messages": [{"role": "user", "content": content}],
                "tools": list(reversed(TOOLS)),
            },
            client=client,
        )
    a, b = comp.calls
    assert json.dumps(a["tools"]) == json.dumps(b["tools"])
    assert [t["function"]["name"] for t in a["tools"]] == ["project__list", "twin__get_node"]
    assert a["extra_body"] == b["extra_body"]
    assert a["extra_body"]["prompt_cache_key"].startswith("mf-")


@pytest.mark.asyncio
async def test_self_hosted_endpoint_gets_no_cache_key() -> None:
    comp = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    spec = ProviderSpec(
        name="openai", model="m", api_key_env="K", base_url="http://localhost:8000/v1"
    )
    await openai_invoke(spec, {"system": SYSTEM, "prompt": "hi"}, client=client)
    assert "extra_body" not in comp.calls[0]


def test_cache_key_changes_with_prefix_only() -> None:
    assert caching.prompt_cache_key(SYSTEM, TOOLS) == caching.prompt_cache_key(SYSTEM, TOOLS)
    assert caching.prompt_cache_key(SYSTEM, TOOLS) != caching.prompt_cache_key("other", TOOLS)
    assert caching.prompt_cache_key(SYSTEM, TOOLS) != caching.prompt_cache_key(SYSTEM, TOOLS[:1])


@pytest.mark.asyncio
async def test_accounting_records_cached_input_from_provider() -> None:
    store = UsageStore()
    configure_usage_store(store)
    try:
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=0,
        )
        fake = _Messages(usage)
        spec = ProviderSpec(name="anthropic", model="claude-x", api_key_env="K")
        resp = await anthropic_invoke(
            spec,
            {"system": SYSTEM, "prompt": "hi", "tools": TOOLS},
            client=SimpleNamespace(messages=fake),
        )
        with usage_scope(run_id="r1", phase="p"):
            record_call(
                provider="anthropic",
                model="claude-x",
                pipeline_role="generator",
                usage=resp["usage"],
            )
        totals = store.run_totals("r1")
        assert totals is not None
        assert totals["cached_input_tokens"] == 900
        assert totals["prompt_tokens"] == 1000
    finally:
        configure_usage_store(None)


@pytest.mark.asyncio
async def test_native_loop_prefix_is_identical_across_steps() -> None:
    """Tools and system serialise identically on every step of a multi-step turn."""
    from orchestrator.harness.native_tools import run_native_tools
    from tests.unit.test_native_tools import _runtime_with_double

    rt = _runtime_with_double()
    seen: list[str] = []
    replies = [
        {"text": "", "tool_calls": [{"id": "c1", "name": "double", "arguments": {"x": 2}}]},
        {"text": "", "tool_calls": [{"id": "c2", "name": "double", "arguments": {"x": 4}}]},
        {"text": "done", "tool_calls": []},
    ]

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        seen.append(json.dumps([request["tools"], request["system"]]))
        return {"model": spec.model, **replies[len(seen) - 1]}

    res = await run_native_tools(rt, "go", invoke=invoke)
    assert res.status == "completed"
    assert len(seen) == 3
    assert len(set(seen)) == 1


# --- FORGE-825: Claude behind OpenRouter ------------------------------------------------

_OPENROUTER = "https://openrouter.ai/api/v1"


class _OpenRouterCompletions(_Completions):
    """Reports a Claude cache read and write the way OpenRouter does."""

    async def create(self, **kwargs: Any) -> Any:
        resp = await super().create(**kwargs)
        resp.usage.prompt_tokens_details = SimpleNamespace(
            cached_tokens=70_000, cache_write_tokens=1_200
        )
        resp.usage.prompt_tokens = 75_000
        return resp


async def _openrouter_calls(model: str, suffixes: list[str | None]) -> list[dict[str, Any]]:
    comp = _OpenRouterCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    spec = ProviderSpec(name="openrouter", model=model, api_key_env="K", base_url=_OPENROUTER)
    history: list[dict[str, Any]] = [{"role": "user", "content": "design a lamp"}]
    for i, suffix in enumerate(suffixes):
        request: dict[str, Any] = {
            "system": SYSTEM,
            "messages": list(history),
            # Tools arrive in a different order on alternate calls.
            "tools": list(reversed(TOOLS)) if i % 2 else list(TOOLS),
        }
        if suffix:
            request["system_suffix"] = suffix
        await openai_invoke(spec, request, client=client)
        history.append({"role": "assistant", "content": f"step {i}"})
        history.append({"role": "user", "content": f"more {i}"})
    return comp.calls


def _stable_prefix(call: dict[str, Any]) -> str:
    """The bytes up to the system breakpoint: tools, then the marked system part."""
    system = call["messages"][0]
    marked = [p for p in system["content"] if "cache_control" in p]
    return json.dumps([call["tools"], marked], sort_keys=False)


@pytest.mark.asyncio
async def test_openrouter_claude_gets_breakpoints() -> None:
    (call,) = await _openrouter_calls("anthropic/claude-sonnet-5.5", ["[project context]\nLamp"])
    system = call["messages"][0]
    assert system["role"] == "system"
    stable, context = system["content"]
    assert stable == {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    # The per-turn context rides after the breakpoint, unmarked.
    assert context == {"type": "text", "text": "[project context]\nLamp"}
    # OpenRouter's automatic breakpoint follows the conversation.
    assert call["extra_body"]["cache_control"] == {"type": "ephemeral"}
    assert call["extra_body"]["prompt_cache_key"].startswith("mf-")


@pytest.mark.asyncio
async def test_openrouter_claude_prefix_is_byte_stable_across_calls_and_turns() -> None:
    calls = await _openrouter_calls(
        "anthropic/claude-sonnet-5.5",
        ["[project context]\nrev 1", "[project context]\nrev 2", None],
    )
    assert len({_stable_prefix(c) for c in calls}) == 1
    assert len({json.dumps(c["extra_body"], sort_keys=True) for c in calls}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["openai/gpt-5", "google/gemini-3-pro", "deepseek/deepseek-v4"])
async def test_other_openrouter_models_get_no_breakpoints(model: str) -> None:
    (call,) = await _openrouter_calls(model, ["note"])
    assert call["messages"][0] == {"role": "system", "content": f"{SYSTEM}\n\nnote"}
    assert "cache_control" not in json.dumps(call)


@pytest.mark.asyncio
async def test_claude_on_a_self_hosted_endpoint_gets_no_breakpoints() -> None:
    comp = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    spec = ProviderSpec(
        name="vllm", model="claude-ish", api_key_env="K", base_url="http://localhost:8000/v1"
    )
    await openai_invoke(spec, {"system": SYSTEM, "prompt": "hi"}, client=client)
    assert "cache_control" not in json.dumps(comp.calls[0])


def test_claude_model_detection() -> None:
    assert caching.is_claude_model("anthropic/claude-sonnet-5.5")
    assert caching.is_claude_model("~anthropic/claude-sonnet-latest")
    assert caching.is_claude_model("claude-opus-5-5")
    assert not caching.is_claude_model("openai/gpt-5")
    assert caching.routes_claude_cache_control("openrouter", None, "anthropic/claude-sonnet-5.5")
    assert not caching.routes_claude_cache_control("openai", None, "gpt-5")


@pytest.mark.asyncio
async def test_openrouter_cache_read_and_write_reach_the_usage_log() -> None:
    comp = _OpenRouterCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    spec = ProviderSpec(
        name="openrouter",
        model="anthropic/claude-sonnet-5.5",
        api_key_env="K",
        base_url=_OPENROUTER,
    )
    resp = await openai_invoke(spec, {"system": SYSTEM, "prompt": "hi"}, client=client)
    usage = resp["usage"]
    assert usage["cached_input_tokens"] == 70_000
    assert usage["cache_creation_input_tokens"] == 1_200
    assert usage["input_includes_cached"] is True
    store = UsageStore()
    configure_usage_store(store)
    try:
        with usage_scope(run_id="r-or", phase="p"):
            record_call(
                provider="openrouter",
                model="anthropic/claude-sonnet-5.5",
                pipeline_role="generator",
                usage=usage,
            )
        totals = store.run_totals("r-or")
        assert totals is not None
        assert totals["cached_input_tokens"] == 70_000
        assert totals["prompt_tokens"] == 75_000
    finally:
        configure_usage_store(None)


@pytest.mark.asyncio
async def test_native_loop_sends_system_context_after_the_breakpoint() -> None:
    """The project brief travels as system_suffix, never inside ``system``."""
    from orchestrator.harness.native_tools import run_native_tools
    from tests.unit.test_native_tools import _runtime_with_double

    rt = _runtime_with_double()
    seen: list[dict[str, Any]] = []

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        seen.append(dict(request))
        return {"model": spec.model, "text": "done", "tool_calls": []}

    await run_native_tools(rt, "go", invoke=invoke, system="STABLE", system_context="BRIEF")
    assert seen[0]["system"] == "STABLE"
    assert seen[0]["system_suffix"] == "BRIEF"
