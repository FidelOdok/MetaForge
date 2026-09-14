#!/usr/bin/env python3
"""Shared helpers for the multi-turn chat context-engineering evals (MET-570).

The chat suite scores *conversations*, not design-flow runs, so its shared
vocabulary differs from ``rubric_common.py``: SSE event parsing, reply
extraction from a refetched thread, a small generic check engine the scenario
JSON drives (``reply_contains``, ``tool_called``, ...), and xfail/xpass outcome
resolution so known-broken baseline behavior (e.g. facts past the 20-turn
history slice, until MET-568) is recorded as ``xfail`` instead of failing the
run.

A "turn record" is the runner's capture of one conversational turn::

    {
      "index": 3, "say": "...", "tag": "bigobs" | None,
      "reply": "...", "reply_error": False,
      "steps": [{"index", "thought", "tool", "arguments", "observation",
                 "error", "final"}, ...],          # agent.step SSE payloads
      "context_stats": {...} | None,               # context.stats SSE payload
      "checks": [{"id", "type", ...}, ...],        # declared on this turn
      "sse": True, "duration_s": 12.4,
    }
"""

from __future__ import annotations

import json
import re
from typing import Any

# The harness's step-budget bailout reply (api_gateway/chat/harness_backend.py).
FALLBACK_ANSWER = "I couldn't converge on an answer within the step budget."

# Check types the scenario JSON may declare (guarded by test_chat_eval_wiring).
SUPPORTED_CHECK_TYPES = (
    "reply_contains",
    "reply_not_contains",
    "tool_called",
    "tool_arg_equals",
    "no_duplicate_tool_calls",
    # MET-569: the mechanical half of the same property. `no_duplicate_tool_calls`
    # measures model *discipline* (did it emit a duplicate at all); this measures
    # that a duplicate it did emit COST NOTHING -- the harness served it from the
    # turn cache instead of re-running the tool.
    "no_duplicate_tool_executions",
    "reply_is_not_fallback",
    # For gateway_action turns (runner-performed HTTP steps, e.g. approving a
    # gate between agent turns): did the action complete?
    "action_succeeded",
    # Negative tool assertion (MET-584): the turn must NOT have called it.
    "tool_not_called",
)


# --- SSE ---------------------------------------------------------------------
def unwrap_sse_data(raw: str) -> Any:
    """Parse one SSE ``data:`` payload, unwrapping the gateway's StreamEvent
    envelope (the useful payload lives under the inner ``data`` key)."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    if isinstance(parsed, dict):
        return parsed.get("data", parsed)
    return parsed


def parse_sse_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Parse ``event:``/``data:`` SSE lines into ``{"event", "data"}`` dicts."""
    out: list[dict[str, Any]] = []
    event_type: str | None = None
    for line in lines:
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            raw = line[len("data:") :].strip()
            out.append({"event": event_type, "data": unwrap_sse_data(raw)})
            event_type = None
    return out


def step_payload(data: Any) -> dict[str, Any]:
    """Unwrap an ``agent.step`` event's payload to the step dict itself.

    The gateway nests the step under a ``step`` key
    (``{"step": {...}, "agent_id": ...}``, see ``notify_agent_step``).
    Live-caught during the MET-575 acceptance run: the runner captured the
    wrapper, so every step-based check (tool_called, tool_arg_equals,
    dedupe) evaluated empty dicts — tools were being called and landing
    work products while the checks reported no calls at all. Tolerates a
    bare step dict for forward/backward compatibility.
    """
    if isinstance(data, dict) and isinstance(data.get("step"), dict):
        return dict(data["step"])
    return dict(data) if isinstance(data, dict) else {}


# --- reply extraction ---------------------------------------------------------
def agent_replies_after(messages: list[dict[str, Any]], user_msg_id: str) -> list[dict[str, Any]]:
    """Agent messages that appear after the just-sent user message.

    The gateway appends the agent reply synchronously during the message POST,
    so a thread refetch already contains it. Falls back to the trailing agent
    messages when the user message id isn't found.
    """
    out: list[dict[str, Any]] = []
    seen = False
    for m in messages:
        if m.get("id") == user_msg_id:
            seen = True
            continue
        if seen and m.get("actor_kind") == "agent":
            out.append(m)
    if not out:
        trailing: list[dict[str, Any]] = []
        for m in reversed(messages):
            if m.get("actor_kind") == "agent":
                trailing.append(m)
            else:
                break
        out = list(reversed(trailing))
    return out


# --- scenario expansion --------------------------------------------------------
def expand_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand ``{"repeat": {"count": N, "template": "... {i} ..."}}`` filler
    blocks into N concrete turns (1-based ``{i}``); pass real turns through."""
    out: list[dict[str, Any]] = []
    for t in turns:
        rep = t.get("repeat")
        if rep:
            for i in range(1, int(rep["count"]) + 1):
                out.append({"say": str(rep["template"]).replace("{i}", str(i))})
        else:
            out.append(dict(t))
    return out


def substitute(turns: list[dict[str, Any]], subs: dict[str, str]) -> list[dict[str, Any]]:
    """Substitute placeholders in turn ``say`` texts and check ``pattern``/``value``.

    ``$PROJECT_ID`` carries the runner-created project id into checks;
    ``$RUN_TAG`` (MET-579) uniquifies names a scenario asks the agent to
    create — a hardcoded name like "Eval Dedupe Probe" collides with the
    previous run's artifact ("A project named X already exists"), so from
    the second run onward the scenario measured name-conflict handling
    instead of tool discipline.
    """
    if not subs:
        return turns
    out = []
    for t in turns:
        t = dict(t)
        say = t.get("say")
        if isinstance(say, str):
            for placeholder, value in subs.items():
                say = say.replace(placeholder, value)
            t["say"] = say
        checks = []
        for c in t.get("checks") or []:
            c = dict(c)
            for key in ("pattern", "value"):
                if isinstance(c.get(key), str):
                    for placeholder, value in subs.items():
                        c[key] = c[key].replace(placeholder, value)
            checks.append(c)
        if checks:
            t["checks"] = checks
        out.append(t)
    return out


def interpolate_checks(turns: list[dict[str, Any]], project_id: str | None) -> list[dict[str, Any]]:
    """Back-compat wrapper: substitute ``$PROJECT_ID`` only."""
    if not project_id:
        return turns
    return substitute(turns, {"$PROJECT_ID": project_id})


# --- check engine --------------------------------------------------------------
def tool_steps(turn: dict[str, Any]) -> list[dict[str, Any]]:
    """The turn's steps that actually called a tool (final answers excluded)."""
    return [s for s in turn.get("steps") or [] if s.get("tool")]


def canon_args(arguments: Any) -> str:
    """Canonical JSON for tool arguments, for duplicate-call detection."""
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)


def turn_replied(turn: dict[str, Any]) -> bool:
    """The turn produced a real answer (non-empty, not the step-budget bailout)."""
    reply = str(turn.get("reply") or "").strip()
    return bool(reply) and reply != FALLBACK_ANSWER


def _reply_matches(turn: dict[str, Any], check: dict[str, Any]) -> bool:
    reply = str(turn.get("reply") or "")
    pattern = str(check.get("pattern", ""))
    if check.get("regex"):
        return re.search(pattern, reply, re.IGNORECASE) is not None
    return pattern.casefold() in reply.casefold()


def run_check(check: dict[str, Any], turn: dict[str, Any]) -> bool:
    """Evaluate one declared check against its turn record."""
    ctype = check.get("type")
    if ctype == "reply_contains":
        return _reply_matches(turn, check)
    if ctype == "reply_not_contains":
        return not _reply_matches(turn, check)
    if ctype == "tool_called":
        prefix = check.get("tool_prefix")
        if prefix:
            return any(str(s.get("tool", "")).startswith(str(prefix)) for s in tool_steps(turn))
        return any(s.get("tool") == check.get("tool") for s in tool_steps(turn))
    if ctype == "tool_arg_equals":
        for s in tool_steps(turn):
            if s.get("tool") == check.get("tool"):
                args = s.get("arguments") or {}
                if str(args.get(str(check.get("arg")))) == str(check.get("value")):
                    return True
        return False
    if ctype == "no_duplicate_tool_calls":
        seen: set[tuple[Any, str]] = set()
        for s in tool_steps(turn):
            key = (s.get("tool"), canon_args(s.get("arguments")))
            if key in seen:
                return False
            seen.add(key)
        return True
    if ctype == "no_duplicate_tool_executions":
        seen_exec: set[tuple[Any, str]] = set()
        for s in tool_steps(turn):
            key = (s.get("tool"), canon_args(s.get("arguments")))
            if key not in seen_exec:
                seen_exec.add(key)
                continue
            obs = s.get("observation")
            if not (isinstance(obs, dict) and obs.get("cached") is True):
                return False
        return True
    if ctype == "reply_is_not_fallback":
        return turn_replied(turn)
    if ctype == "action_succeeded":
        return bool(turn.get("action_ok"))
    if ctype == "tool_not_called":
        return not any(s.get("tool") == check.get("tool") for s in tool_steps(turn))
    raise ValueError(f"unsupported check type: {ctype!r}")


def evaluate_declared(turns: list[dict[str, Any]]) -> dict[str, bool]:
    """Run every check declared on the (expanded) turn records, keyed by id."""
    out: dict[str, bool] = {}
    for turn in turns:
        for check in turn.get("checks") or []:
            out[str(check["id"])] = run_check(check, turn)
    return out


# --- outcome resolution ----------------------------------------------------------
def expected_for_variant(expected_today: dict[str, Any] | None, variant: str) -> dict[str, str]:
    """Flatten ``expected_today`` (flat or per-variant) for one variant name."""
    out: dict[str, str] = {}
    for cid, exp in (expected_today or {}).items():
        if isinstance(exp, dict):
            v = exp.get(variant)
            if v:
                out[cid] = str(v)
        else:
            out[cid] = str(exp)
    return out


def resolve_outcomes(checks: dict[str, bool], expected: dict[str, str]) -> dict[str, str]:
    """Map check results to ``pass``/``fail``/``xfail``/``xpass``.

    ``expected`` maps check id to ``"fail"`` (already variant-flattened). A
    check that fails as predicted is ``xfail`` (confirmed baseline); one that
    passes despite the prediction is ``xpass`` (the fix landed).
    """
    out: dict[str, str] = {}
    for cid, ok in checks.items():
        exp_fail = expected.get(cid) == "fail"
        if ok:
            out[cid] = "xpass" if exp_fail else "pass"
        else:
            out[cid] = "xfail" if exp_fail else "fail"
    return out


def score(checks: dict[str, bool]) -> float:
    """Fraction of checks that passed (1.0 when there are none)."""
    if not checks:
        return 1.0
    return round(sum(1 for v in checks.values() if v) / len(checks), 3)
