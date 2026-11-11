"""Old tool results are cleared from a long turn in batches (MetaForge, 2026-11-11).

A research turn fetched 11 to 16 pages; every page stayed in the context for every later
step, so the prompt grew to about 95k tokens and the turn cost $0.60 to $0.80. Once the
results older than the newest few pass a threshold, they are cleared together: each is kept
behind a handle and replaced by a stub that says how to read it again. Batching matters:
clearing breaks the prompt cache after the first cleared message, so it happens rarely.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.harness.result_handles import (
    CLEAR_CHARS_ENV,
    ResultStore,
    clear_stale_results,
)


def _exchange(i: int, tool: str, chars: int) -> list[dict[str, Any]]:
    call = {"id": f"c{i}", "type": "function", "function": {"name": tool, "arguments": "{}"}}
    return [
        {"role": "assistant", "content": f"step {i}", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": f"<{i}>" + "x" * chars},
    ]


def _turn(sizes: list[int]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "find the parts"}]
    for i, n in enumerate(sizes):
        msgs += _exchange(i, "mcp_web_fetch", n)
    return msgs


def _tools(msgs: list[dict[str, Any]]) -> list[str]:
    return [m["content"] for m in msgs if m["role"] == "tool"]


def test_under_the_threshold_nothing_changes() -> None:
    msgs = _turn([8000] * 8)  # 5 old results, 40k chars: under 48k
    assert clear_stale_results(msgs, ResultStore()) == msgs


def test_over_the_threshold_the_old_results_are_cleared_and_the_newest_kept() -> None:
    store = ResultStore()
    msgs = _turn([8000] * 10)  # 7 old results, 56k chars
    out = clear_stale_results(msgs, store)
    contents = _tools(out)
    stubs = [json.loads(c) for c in contents[:7]]
    assert all(s["cleared"] is True and s["tool"] == "mcp_web_fetch" for s in stubs)
    assert contents[7:] == _tools(msgs)[7:]  # the newest three, verbatim
    # Each cleared result reads back whole through its handle.
    back = store.read(stubs[2]["result_handle"], limit=100)
    assert back["total_chars"] == 8003 and back["content"].startswith("<2>")
    assert "read_tool_result" in stubs[0]["note"]
    assert msgs[2]["content"].startswith("<0>")  # the input is not touched


def test_small_results_stay() -> None:
    msgs = _turn([300] * 3 + [9000] * 9)
    contents = _tools(clear_stale_results(msgs, ResultStore()))
    assert all(c.startswith("<") for c in contents[:3])


def test_clearing_again_changes_nothing() -> None:
    store = ResultStore()
    once = clear_stale_results(_turn([8000] * 10), store)
    assert clear_stale_results(once, store) == once


def test_a_result_already_behind_a_handle_keeps_its_handle() -> None:
    store = ResultStore()
    item = store.put("mcp_web_fetch", "page " * 5000, 25000)
    summary = json.dumps(
        {"result_handle": item.handle, "tool": "mcp_web_fetch", "summary": "y" * 6000}
    )
    msgs = _turn([8000] * 10)
    msgs[2]["content"] = summary
    out = clear_stale_results(msgs, store)
    first = json.loads(_tools(out)[0])
    assert first.get("cleared") is True, first
    # The first keeps its handle; the six other old results get one each.
    assert first["result_handle"] == item.handle and len(store) == 1 + 6


def test_the_threshold_comes_from_the_environment_and_zero_turns_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msgs = _turn([8000] * 10)
    monkeypatch.setenv(CLEAR_CHARS_ENV, "0")
    assert clear_stale_results(msgs, ResultStore()) == msgs
    monkeypatch.setenv(CLEAR_CHARS_ENV, "20000")
    assert json.loads(_tools(clear_stale_results(_turn([8000] * 6), ResultStore()))[0])["cleared"]


# -- in the tool loop ---------------------------------------------------------------------


def _runtime(with_reader: bool) -> Any:
    from orchestrator.harness import HarnessRuntime
    from orchestrator.harness.providers import load_provider_config
    from orchestrator.harness.tools import ToolRegistry

    async def fetch(args: dict[str, Any]) -> dict[str, Any]:
        return {"text": f"page {args['n']} " + "w" * 7000}

    async def reader(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": ""}

    tools = ToolRegistry()
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    tools.register_native("fetch", description="fetch a page", input_schema=schema, handler=fetch)
    if with_reader:
        tools.register_native(
            "read_tool_result",
            description="read a result",
            input_schema={"type": "object"},
            handler=reader,
        )
    config = load_provider_config(
        {"roles": {"generator": [{"provider": "openai", "model": "gpt-4o"}]}}
    )
    return HarnessRuntime.build(config, tools=tools)


async def _run(with_reader: bool) -> list[list[dict[str, Any]]]:
    from orchestrator.harness.native_tools import run_native_tools

    seen: list[list[dict[str, Any]]] = []

    async def invoke(spec: Any, request: Any) -> dict[str, Any]:
        seen.append(list(request["messages"]))
        n = len(seen)
        if n <= 12:
            call = {"id": f"c{n}", "name": "fetch", "arguments": {"n": n}}
            return {"model": spec.model, "text": f"reading page {n}", "tool_calls": [call]}
        return {"model": spec.model, "text": "done", "tool_calls": []}

    rt = _runtime(with_reader)
    result = await run_native_tools(rt, "find it", invoke=invoke, max_steps=20)
    assert result.status == "completed"
    return seen


def _sent_chars(msgs: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) for m in msgs if m.get("role") == "tool")


async def test_a_long_turn_sends_the_model_cleared_stubs_once_the_old_results_are_large() -> None:
    seen = await _run(with_reader=True)
    last = seen[-1]
    stubs = [
        m for m in last if m.get("role") == "tool" and m["content"].startswith('{"cleared": true')
    ]
    assert stubs, "nothing was cleared"
    # The newest three results reach the model verbatim.
    tail = [m["content"] for m in last if m.get("role") == "tool"][-3:]
    assert all("page" in c and "cleared" not in c for c in tail)
    # And the context stays bounded: well under the twelve pages it would otherwise carry.
    assert _sent_chars(last) < 12 * 7000 // 2


async def test_without_a_reader_tool_nothing_is_cleared() -> None:
    seen = await _run(with_reader=False)
    assert not any(
        m["content"].startswith('{"cleared"') for m in seen[-1] if m.get("role") == "tool"
    )
