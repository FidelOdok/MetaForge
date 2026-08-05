"""Unit tests for the chat-eval shared helpers (MET-570). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_rubric_common import (  # noqa: E402
    FALLBACK_ANSWER,
    agent_replies_after,
    evaluate_declared,
    expand_turns,
    expected_for_variant,
    interpolate_checks,
    parse_sse_lines,
    resolve_outcomes,
    run_check,
    turn_replied,
)


# --- SSE parsing -------------------------------------------------------------
def test_parse_sse_unwraps_envelope() -> None:
    lines = [
        "event: message.delta",
        'data: {"data": {"delta": "Hi"}, "thread_id": "t1", "timestamp": "now"}',
    ]
    events = parse_sse_lines(lines)
    assert events == [{"event": "message.delta", "data": {"delta": "Hi"}}]


def test_parse_sse_falls_back_on_non_json() -> None:
    events = parse_sse_lines(["event: weird", "data: not-json"])
    assert events == [{"event": "weird", "data": {"raw": "not-json"}}]


def test_parse_sse_handles_unenveloped_payload() -> None:
    events = parse_sse_lines(["event: agent.step", 'data: {"tool": "x"}'])
    assert events[0]["data"] == {"tool": "x"}


def test_step_payload_unwraps_gateway_nesting() -> None:
    # notify_agent_step nests the step: {"step": {...}, "agent_id": ...}.
    # Live-caught: capturing the wrapper made every step-based check
    # evaluate empty dicts while tools were really being called.
    from chat_rubric_common import step_payload

    wrapped = {"step": {"tool": "mcp_twin_record_decision", "arguments": {"a": 1}}, "agent_id": "h"}
    assert step_payload(wrapped)["tool"] == "mcp_twin_record_decision"
    # Bare step dicts pass through; garbage becomes an empty step.
    assert step_payload({"tool": "x"}) == {"tool": "x"}
    assert step_payload("not a dict") == {}


# --- reply extraction ---------------------------------------------------------
def _msg(mid: str, kind: str, content: str) -> dict:
    return {"id": mid, "actor_kind": kind, "content": content}


def test_agent_replies_after_finds_reply() -> None:
    msgs = [_msg("u1", "user", "hi"), _msg("a1", "agent", "hello"), _msg("u2", "user", "?")]
    assert [m["id"] for m in agent_replies_after(msgs, "u1")] == ["a1"]


def test_agent_replies_after_missing_id_takes_trailing() -> None:
    msgs = [_msg("u1", "user", "hi"), _msg("a1", "agent", "one"), _msg("a2", "agent", "two")]
    assert [m["id"] for m in agent_replies_after(msgs, "nope")] == ["a1", "a2"]


# --- expansion / interpolation --------------------------------------------------
def test_expand_turns_expands_repeat_blocks() -> None:
    turns = expand_turns(
        [{"say": "start"}, {"repeat": {"count": 3, "template": "point {i}"}}, {"say": "end"}]
    )
    assert [t["say"] for t in turns] == ["start", "point 1", "point 2", "point 3", "end"]


def test_substitute_covers_say_and_checks_with_run_tag() -> None:
    """MET-579: hardcoded artifact names collide with a previous run's
    artifacts ("project already exists"), so $RUN_TAG must uniquify names
    inside the turn text itself, not just check patterns."""
    from chat_rubric_common import substitute

    turns: list[dict] = [
        {
            "say": "Create a project called 'Probe $RUN_TAG' in $PROJECT_ID",
            "checks": [{"id": "a", "type": "reply_contains", "pattern": "Probe $RUN_TAG"}],
        }
    ]
    out = substitute(turns, {"$RUN_TAG": "1755", "$PROJECT_ID": "p-9"})
    assert out[0]["say"] == "Create a project called 'Probe 1755' in p-9"
    assert out[0]["checks"][0]["pattern"] == "Probe 1755"
    # Source fixture untouched.
    assert "$RUN_TAG" in turns[0]["say"]


def test_interpolate_checks_substitutes_project_id() -> None:
    turns: list[dict] = [
        {
            "say": "x",
            "checks": [
                {"id": "a", "type": "reply_contains", "pattern": "$PROJECT_ID"},
                {"id": "b", "type": "tool_arg_equals", "arg": "project_id", "value": "$PROJECT_ID"},
            ],
        }
    ]
    out = interpolate_checks(turns, "p-123")
    assert out[0]["checks"][0]["pattern"] == "p-123"
    assert out[0]["checks"][1]["value"] == "p-123"
    # Original fixture data is not mutated.
    assert turns[0]["checks"][0]["pattern"] == "$PROJECT_ID"


# --- check engine ---------------------------------------------------------------
def _turn(**kw: object) -> dict:
    base: dict = {"reply": "", "steps": [], "checks": []}
    base.update(kw)
    return base


def test_reply_contains_is_case_insensitive_substring() -> None:
    turn = _turn(reply="The alloy is 7075-T6.")
    assert run_check({"type": "reply_contains", "pattern": "7075"}, turn)
    assert run_check({"type": "reply_contains", "pattern": "alloy"}, turn)
    assert not run_check({"type": "reply_contains", "pattern": "6061"}, turn)


def test_reply_contains_regex_mode() -> None:
    turn = _turn(reply="Use an M3 x 8 bolt.")
    check = {"type": "reply_contains", "pattern": "M3\\s*[x×]\\s*8", "regex": True}
    assert run_check(check, turn)


def test_reply_not_contains() -> None:
    assert run_check({"type": "reply_not_contains", "pattern": "oops"}, _turn(reply="fine"))


def test_tool_called_exact_and_prefix() -> None:
    turn = _turn(steps=[{"tool": "mcp_twin_query_cypher", "arguments": {}}])
    assert run_check({"type": "tool_called", "tool": "mcp_twin_query_cypher"}, turn)
    assert run_check({"type": "tool_called", "tool_prefix": "mcp_twin_"}, turn)
    assert not run_check({"type": "tool_called", "tool": "mcp_twin_get_node"}, turn)


def test_tool_arg_equals() -> None:
    turn = _turn(steps=[{"tool": "mcp_twin_record_decision", "arguments": {"project_id": "p-1"}}])
    check = {
        "type": "tool_arg_equals",
        "tool": "mcp_twin_record_decision",
        "arg": "project_id",
        "value": "p-1",
    }
    assert run_check(check, turn)
    assert not run_check({**check, "value": "p-2"}, turn)


def test_no_duplicate_tool_calls() -> None:
    dup = _turn(
        steps=[
            {"tool": "t", "arguments": {"a": 1}},
            {"tool": "t", "arguments": {"a": 1}},
        ]
    )
    distinct = _turn(
        steps=[
            {"tool": "t", "arguments": {"a": 1}},
            {"tool": "t", "arguments": {"a": 2}},
        ]
    )
    assert not run_check({"type": "no_duplicate_tool_calls"}, dup)
    assert run_check({"type": "no_duplicate_tool_calls"}, distinct)


def test_reply_is_not_fallback() -> None:
    assert run_check({"type": "reply_is_not_fallback"}, _turn(reply="real answer"))
    assert not run_check({"type": "reply_is_not_fallback"}, _turn(reply=FALLBACK_ANSWER))
    assert not run_check({"type": "reply_is_not_fallback"}, _turn(reply="  "))


def test_unknown_check_type_raises() -> None:
    with pytest.raises(ValueError):
        run_check({"type": "nope"}, _turn())


def test_turn_replied() -> None:
    assert turn_replied(_turn(reply="hi"))
    assert not turn_replied(_turn(reply=FALLBACK_ANSWER))


def test_evaluate_declared_collects_by_id() -> None:
    turns = [
        _turn(reply="7075", checks=[{"id": "a", "type": "reply_contains", "pattern": "7075"}]),
        _turn(reply="no", checks=[{"id": "b", "type": "reply_contains", "pattern": "yes"}]),
    ]
    assert evaluate_declared(turns) == {"a": True, "b": False}


# --- outcome resolution ------------------------------------------------------------
def test_resolve_outcomes_matrix() -> None:
    checks = {"p": True, "f": False, "xf": False, "xp": True}
    expected = {"xf": "fail", "xp": "fail"}
    assert resolve_outcomes(checks, expected) == {
        "p": "pass",
        "f": "fail",
        "xf": "xfail",
        "xp": "xpass",
    }


def test_expected_for_variant_flat_and_per_variant() -> None:
    expected = {"a": "fail", "b": {"react": "fail"}}
    assert expected_for_variant(expected, "react") == {"a": "fail", "b": "fail"}
    assert expected_for_variant(expected, "native") == {"a": "fail"}
    assert expected_for_variant(None, "native") == {}
