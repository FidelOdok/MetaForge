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
