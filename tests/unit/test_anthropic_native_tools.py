"""Native tool-calling on the Anthropic adapter: OpenAI<->Anthropic translation
and tool_use parsing (MET-10)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.harness.providers.adapters import (
    _to_anthropic_messages,
    _to_anthropic_tools,
    anthropic_invoke,
)
from orchestrator.harness.providers.registry import ProviderSpec

SPEC = ProviderSpec(name="anthropic", model="claude-x", api_key_env="ANTHROPIC_API_KEY")


class _FakeMessages:
    def __init__(self, blocks: list[Any], model: str = "claude-x") -> None:
        self._resp = SimpleNamespace(content=blocks, model=model)
        self.captured: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.captured = kwargs
        return self._resp


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _text_block(text: str) -> Any:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id_: str, name: str, inp: dict[str, Any]) -> Any:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


def test_to_anthropic_tools_reshapes_schema() -> None:
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "project_list",
                "description": "list projects",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    out = _to_anthropic_tools(openai_tools)
    assert out == [
        {
            "name": "project_list",
            "description": "list projects",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_to_anthropic_messages_groups_tool_results() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "a", "arguments": '{"x":1}'}},
                {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ra"},
        {"role": "tool", "tool_call_id": "c2", "content": "rb"},
    ]
    out = _to_anthropic_messages(messages)
    # user, assistant(tool_use x2), then ONE user turn carrying both tool_results
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assistant_blocks = out[1]["content"]
    assert assistant_blocks[0] == {"type": "text", "text": "let me check"}
    assert assistant_blocks[1] == {"type": "tool_use", "id": "c1", "name": "a", "input": {"x": 1}}
    assert assistant_blocks[2] == {"type": "tool_use", "id": "c2", "name": "b", "input": {}}
    results = out[2]["content"]
    assert results == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "ra"},
        {"type": "tool_result", "tool_use_id": "c2", "content": "rb"},
    ]


@pytest.mark.asyncio
async def test_anthropic_invoke_parses_tool_use() -> None:
    fake = _FakeMessages([_tool_use_block("t1", "project_list", {"limit": 5})])
    client = _FakeClient(fake)
    tools = [{"type": "function", "function": {"name": "project_list", "parameters": {}}}]
    resp = await anthropic_invoke(
        SPEC,
        {"messages": [{"role": "user", "content": "list"}], "tools": tools},
        client=client,
    )
    assert resp["tool_calls"] == [{"id": "t1", "name": "project_list", "arguments": {"limit": 5}}]
    # tools were forwarded in Anthropic shape
    assert fake.captured is not None
    assert fake.captured["tools"][0]["name"] == "project_list"


@pytest.mark.asyncio
async def test_anthropic_invoke_text_only_has_no_tool_calls() -> None:
    fake = _FakeMessages([_text_block("hello there")])
    client = _FakeClient(fake)
    resp = await anthropic_invoke(
        SPEC, {"messages": [{"role": "user", "content": "hi"}]}, client=client
    )
    assert resp["text"] == "hello there"
    assert "tool_calls" not in resp
    # no tools → messages passed through untouched (no block translation)
    assert fake.captured is not None
    assert fake.captured["messages"] == [{"role": "user", "content": "hi"}]
    assert "tools" not in fake.captured
