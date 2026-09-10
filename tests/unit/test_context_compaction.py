"""Unit tests for MET-568 context compaction: token-budgeted history with a
content-preserving summary, within-turn native-loop folding, ReAct trace
compression, and explicit observation truncation. Network-free."""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.harness.compression import (
    budget_history,
    compact_native_messages,
    summarize_turns,
    truncate_observation,
    truncate_observation_value,
)
from orchestrator.harness.native_tools import run_native_tools
from orchestrator.harness.policy import ModelPolicy
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.react import ReActStep, ToolCall
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tools import ToolRegistry

CONFIG = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)


# --- truncate_observation -------------------------------------------------------
def test_truncation_is_loud_not_silent() -> None:
    out = truncate_observation("x" * 3000, max_chars=1000)
    assert out.startswith("x" * 1000)
    assert "[truncated 2000 chars]" in out


def test_short_observation_untouched() -> None:
    assert truncate_observation("short", max_chars=1000) == "short"


# --- truncate_observation_value (MET-58X): structural shrink ------------------------
# Regression for a real bug: `project.list` returns {"projects": [...15 items],
# "total": 15}. A blind character slice on the serialized JSON lands mid-array and
# chops off the trailing "total" key entirely — the model sees 4 intact projects, a
# truncation marker, and no count, so it can't even say "4 of 15".
def _big_project_list(n: int) -> dict[str, Any]:
    return {
        "projects": [
            {
                "id": f"p{i}",
                "name": f"eval-chat_brief_project-native-1-{1785900000 + i}",
                "description": "Eval fixture: a 100x60x25 mm IP54 sensor-node enclosure.",
                "status": "draft",
            }
            for i in range(n)
        ],
        "total": n,
    }


def test_dict_with_list_field_shrinks_list_and_keeps_total() -> None:
    import json

    payload = _big_project_list(15)
    out = truncate_observation_value(payload, max_chars=1500, render=json.dumps)

    assert len(out) <= 1500
    parsed = json.loads(out)  # must be valid, complete JSON — no dangling truncation marker
    assert parsed["total"] == 15  # survives — the whole point of the fix
    assert "projects_omitted_count" in parsed
    assert parsed["projects_omitted_count"] == 15 - len(parsed["projects"])
    assert 0 < len(parsed["projects"]) < 15  # some detail kept, not annihilated


def test_dict_with_list_field_under_budget_is_unchanged() -> None:
    import json

    payload = {"projects": [{"id": "p1"}], "total": 1}
    out = truncate_observation_value(payload, max_chars=1000, render=json.dumps)
    assert out == json.dumps(payload)  # identical — no shrink needed, no marker


def test_bare_list_shrinks_and_notes_omitted_count() -> None:
    import json

    items = [{"id": i} for i in range(200)]
    out = truncate_observation_value(items, max_chars=500, render=json.dumps)
    assert len(out) <= 500
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert "more items omitted" in parsed[-1]
    assert len(parsed) - 1 < 200  # kept fewer real items than the original 200


def test_no_list_field_omits_the_oversized_string_field() -> None:
    """MET-10: this used to fall through to a blind character slice on the
    whole rendered dict, landing mid-string. Live-caught: a real
    cadquery.execute_script result's step_base64 got sliced mid-base64 by
    this exact path, and the model reused the corrupted value verbatim in a
    later twin.commit_geometry call, which failed with "not valid base64"
    instead of an honest "this was too large" signal. Dropping the whole
    field with an explicit marker is safer than any partial prefix."""
    payload = {"huge_blob": "x" * 5000}
    out = truncate_observation_value(payload, max_chars=1000)
    assert len(out) <= 1000
    assert "x" * 100 not in out  # no partial prefix of the corrupted value survives
    assert "omitted" in out and "5000 chars" in out


def test_step_base64_field_is_omitted_not_corrupted() -> None:
    """Direct reproduction of the live-caught shape: a tool result dict with
    other small fields plus one oversized base64 blob."""
    import json

    payload = {
        "cad_file": "/tmp/out.step",
        "step_base64": "QUJD" * 20_000,  # valid base64 repeated -- ~80,000 chars
        "volume_mm3": 1234.5,
    }
    out = truncate_observation_value(payload, max_chars=2000, render=json.dumps)
    parsed = json.loads(out)  # must be valid, complete JSON
    assert parsed["cad_file"] == "/tmp/out.step"  # small fields survive untouched
    assert parsed["volume_mm3"] == 1234.5
    assert "QUJD" * 100 not in parsed["step_base64"]  # no long corrupted prefix
    assert "omitted" in parsed["step_base64"]


def test_non_dict_non_list_value_behaves_like_before() -> None:
    assert truncate_observation_value("short", max_chars=1000) == "short"
    out = truncate_observation_value("y" * 3000, max_chars=1000)
    assert "[truncated 2000 chars]" in out


def test_native_json_safe_preserves_project_list_total() -> None:
    """End-to-end through native_tools.py's own render (json.dumps, no default=str
    quirks) — the actual code path a real project.list tool call goes through.
    600 items comfortably exceeds the real 75_000-char cap (MET-598)."""
    import json

    from orchestrator.harness.native_tools import _json_safe

    out = _json_safe(_big_project_list(600))
    parsed = json.loads(out)
    assert parsed["total"] == 600
    assert parsed["projects_omitted_count"] == 600 - len(parsed["projects"])


@pytest.mark.asyncio
async def test_policy_trace_preserves_total_for_large_dict_observation() -> None:
    """The ReAct (non-native) path — ModelPolicy._render_trace — has the exact
    same defect class fixed the same way."""
    rt = HarnessRuntime.build(CONFIG)
    seen: dict[str, str] = {}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        seen["content"] = request["messages"][0]["content"]
        return {"text": '{"final": "done"}', "model": spec.model}

    step = ReActStep(
        thought="t", tool_call=ToolCall("project.list", {}), observation=_big_project_list(600)
    )
    await ModelPolicy(rt, invoke=invoke).next_action("goal", [step])
    assert "'total': 600" in seen["content"]  # str() repr — survives the shrink
    assert "omitted_count" in seen["content"]


# --- budget_history -----------------------------------------------------------------
def _turn(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def test_history_under_budget_is_kept_whole() -> None:
    history = [_turn("user", "a"), _turn("assistant", "b")]
    kept, dropped = budget_history(history, max_tokens=1000)
    assert kept == history and dropped == []


def test_history_over_budget_keeps_newest_drops_oldest() -> None:
    history = [_turn("user", f"turn {i}: " + "x" * 400) for i in range(10)]
    kept, dropped = budget_history(history, max_tokens=300)
    assert kept and dropped
    assert kept[-1] is history[-1]  # newest survives
    assert dropped[0] is history[0]  # oldest goes first
    assert kept + [] == history[len(dropped) :]  # contiguous split


def test_single_oversized_turn_is_still_kept() -> None:
    # The newest turn must never be dropped, even if it alone busts the budget.
    history = [_turn("user", "y" * 10_000)]
    kept, dropped = budget_history(history, max_tokens=10)
    assert kept == history and dropped == []


# --- summarize_turns -------------------------------------------------------------------
def test_summary_preserves_early_facts() -> None:
    # The whole point: facts stated in turn 1 must survive into the summary
    # so the model can still recall them (the MET-568 xfail scenario).
    dropped = [
        _turn("user", "The bracket serial is MF-7741-X and the alloy is 7075-T6."),
        _turn("assistant", "Noted."),
    ]
    summary = summarize_turns(dropped)
    assert "MF-7741-X" in summary
    assert "7075-T6" in summary
    assert "2 earlier conversation turns" in summary


def test_summary_truncates_long_turns_and_caps_total() -> None:
    dropped = [_turn("user", "z" * 500) for _ in range(100)]
    summary = summarize_turns(dropped, max_chars_per_turn=50, max_total_chars=1000)
    assert len(summary) < 1400
    assert "more turns omitted" in summary


# --- compact_native_messages ------------------------------------------------------------
def _exchange(i: int, obs_chars: int = 400) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"c{i}",
                    "type": "function",
                    "function": {"name": f"tool_{i % 2}", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "content": "o" * obs_chars},
    ]


def test_native_messages_under_budget_untouched() -> None:
    messages = [_turn("user", "goal"), *_exchange(1)]
    assert compact_native_messages(messages, max_tokens=100_000) is messages


def test_native_messages_fold_older_exchanges() -> None:
    lead = [_turn("user", "history"), _turn("user", "goal")]
    messages = list(lead)
    for i in range(8):
        messages.extend(_exchange(i))
    out = compact_native_messages(messages, max_tokens=500, keep_recent_exchanges=2)
    # Lead segment intact, synopsis inserted, only recent exchanges verbatim.
    assert out[0] == lead[0] and out[1] == lead[1]
    assert "earlier tool-exchange messages compressed" in out[2]["content"]
    assert "tool_0" in out[2]["content"] and "tool_1" in out[2]["content"]
    remaining_starts = [m for m in out[3:] if m.get("tool_calls")]
    assert len(remaining_starts) == 2


def test_native_messages_few_exchanges_not_folded() -> None:
    messages = [_turn("user", "goal"), *_exchange(1, obs_chars=100_000)]
    # Over budget but only one exchange — nothing old enough to fold.
    out = compact_native_messages(messages, max_tokens=100, keep_recent_exchanges=3)
    assert out is messages


# --- ModelPolicy trace compression ------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_compresses_long_trace_into_synopsis() -> None:
    rt = HarnessRuntime.build(CONFIG)
    seen: dict[str, str] = {}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        seen["content"] = request["messages"][0]["content"]
        return {"text": '{"final": "done"}', "model": spec.model}

    steps = [
        ReActStep(
            thought=f"t{i}",
            tool_call=ToolCall(f"tool_{i}", {}),
            observation="obs " + "x" * 2000,
        )
        for i in range(12)
    ]
    policy = ModelPolicy(rt, invoke=invoke, trace_token_budget=800)
    await policy.next_action("the goal", steps)
    content = seen["content"]
    assert "earlier steps compressed" in content  # synopsis present
    assert "- called tool_0(" not in content  # oldest step line folded away
    assert "tool_0×1" in content  # …but still named in the synopsis tally
    assert "- called tool_11(" in content  # recent kept verbatim, args included (MET-650)


@pytest.mark.asyncio
async def test_policy_truncates_observations_with_marker() -> None:
    rt = HarnessRuntime.build(CONFIG)
    seen: dict[str, str] = {}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        seen["content"] = request["messages"][0]["content"]
        return {"text": '{"final": "done"}', "model": spec.model}

    steps = [ReActStep(thought="t", tool_call=ToolCall("big", {}), observation="y" * 90_000)]
    await ModelPolicy(rt, invoke=invoke).next_action("goal", steps)
    assert "[truncated" in seen["content"]


# --- run_native_tools within-turn compaction ----------------------------------------------
@pytest.mark.asyncio
async def test_native_loop_compacts_between_iterations() -> None:
    tools = ToolRegistry()

    async def big_tool(arguments: dict[str, object]) -> object:
        return "B" * 4000

    tools.register_native(
        "big_tool", description="returns a lot", input_schema={"type": "object"}, handler=big_tool
    )
    rt = HarnessRuntime.build(CONFIG, tools=tools)

    captured: list[int] = []
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        captured.append(len(request["messages"]))
        calls["n"] += 1
        if calls["n"] <= 6:
            return {
                "text": "",
                "tool_calls": [{"id": f"c{calls['n']}", "name": "big_tool", "arguments": {}}],
                "model": spec.model,
            }
        return {"text": "final answer", "model": spec.model}

    result = await run_native_tools(rt, "go", invoke=invoke, max_steps=10, max_context_tokens=2000)
    assert result.status == "completed"
    # Without compaction the message list grows by 2 per iteration (7th call
    # would see 1 + 12 = 13). With folding it must stay well below that.
    assert max(captured) < 13
    assert calls["n"] == 7


# --- routes._thread_history token budget ---------------------------------------------------
@pytest.mark.asyncio
async def test_thread_history_budgets_and_summarizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MET-568: dropped early turns fold into a content-preserving summary
    pair instead of vanishing — the fact from turn 1 stays recallable."""
    import api_gateway.chat.routes as routes

    class _Msg:
        def __init__(self, kind: str, content: str) -> None:
            self.actor_kind = kind
            self.content = content
            self.status = "ok"

    msgs = [_Msg("user", "The serial is MF-7741-X. Remember it.")]
    for i in range(30):
        msgs.append(_Msg("user", f"filler question {i} " + "pad " * 120))
        msgs.append(_Msg("agent", f"filler answer {i} " + "pad " * 120))
    msgs.append(_Msg("user", "current turn (dropped by history builder)"))

    class _FakeBackend:
        async def get_messages(self, thread_id: str) -> list[_Msg]:
            return msgs

    monkeypatch.setattr(routes, "_backend", _FakeBackend())
    monkeypatch.setenv("METAFORGE_HISTORY_TOKENS", "800")

    history = await routes._thread_history("t1")
    assert history[0]["content"].startswith("[conversation summary]")
    assert "MF-7741-X" in history[0]["content"]  # early fact survives
    assert history[1]["role"] == "assistant"  # ack pair keeps roles alternating
    assert "filler answer 29" in history[-1]["content"]  # newest kept verbatim


@pytest.mark.asyncio
async def test_thread_history_under_budget_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_gateway.chat.routes as routes

    class _Msg:
        def __init__(self, kind: str, content: str) -> None:
            self.actor_kind = kind
            self.content = content
            self.status = "ok"

    msgs = [_Msg("user", "hello"), _Msg("agent", "hi"), _Msg("user", "current")]

    class _FakeBackend:
        async def get_messages(self, thread_id: str) -> list[_Msg]:
            return msgs

    monkeypatch.setattr(routes, "_backend", _FakeBackend())
    monkeypatch.delenv("METAFORGE_HISTORY_TOKENS", raising=False)
    history = await routes._thread_history("t1")
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
